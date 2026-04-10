"""Video Factory V15 — Asset Agent.

Coordinates asset generation using scene-specific prompts
from the SceneAgent instead of generic keywords.

Each asset is generated with:
  - Character appearance consistency
  - Mood/lighting from the scene plan
  - Camera direction hints
  - Continuity with adjacent scenes

Wraps existing V14 modules: image_gen, veo_clips, music, sfx.
"""
from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

from config import settings
from core.openmontage_free import (
    apply_bg_remove,
    apply_face_restore,
    apply_upscale,
    strict_free_candidates,
)
from core.provider_selector import ProviderSelector
from core.state import StoryState
from models.config_models import NichoConfig

T = TypeVar("T")


class AssetAgent:
    """Generate coherent assets based on the approved scene plan."""

    _AMBIGUOUS_STOCK_TOKENS = {
        "algo", "caso", "cosa", "cosas", "dato", "datos", "historia", "local",
        "lugar", "mundo", "tema", "viral", "video", "red", "redes", "casa",
        "hogar", "llave", "llaves", "trato", "acuerdo", "grupo", "gente",
        "something", "case", "thing", "things", "data", "history", "local",
        "place", "world", "topic", "viral", "video", "network", "networks",
        "house", "home", "key", "keys", "deal", "agreement", "group", "people",
    }

    _HISTORICAL_MARKERS = {
        "historia", "historico", "historical", "archive", "archival", "vintage",
        "tribu", "tribal", "tribe", "fbi", "asesinato", "murder", "crimen",
        "crime", "conspiracion", "conspiracy", "justicia", "justice",
        "investigacion", "investigation", "osage",
    }

    _DARK_MARKERS = {
        "oscuro", "dark", "misterio", "mystery", "true crime", "asesinato",
        "crimen", "vigilancia", "surveillance", "secret", "secreto",
        "conspiracion", "fbi", "investigacion", "detective", "spy", "espia",
    }

    _MODERN_BLOCKLIST = {
        "modern", "corporate", "office", "startup", "business meeting",
        "real estate", "property", "keys handover", "apartment", "skyscraper",
        "glass building", "city skyline", "luxury home", "construction worker",
        "sunny park", "playground", "happy family", "smiling police",
        "laptop", "smartphone", "iphone", "computer", "digital", "technology",
        "modern car", "highway", "internet", "social media", "wifi",
    }

    _CARTOON_BLOCKLIST = {
        "cartoon", "animation", "animated", "3d", "3d render", "cgi",
        "illustration", "mascot", "comic", "cute", "kawaii",
        "anime", "manga", "pixar", "disney", "character design",
    }

    def run(
        self,
        state: StoryState,
        nicho: NichoConfig,
        timestamp: int,
        temp_dir: Path,
        runtime_overrides: Optional[dict] = None,
    ) -> dict:
        """Generate all assets for the video.

        Uses scene-specific prompts from StoryState.scenes
        instead of generic keywords (V14 behavior).

        Returns dict with keys: clips, images, music_path, sfx_paths
        """
        t0 = time.time()
        selector = ProviderSelector()
        results = {
            "stock_clips": [],
            "images": [],
            "music_path": None,
            "sfx_paths": [],
            "provider_orders": {},
            "provider_sources": {},
        }

        # --- 1. Stock fallback for uncovered scenes ---
        clips_needed = nicho.num_clips
        stock_candidates = strict_free_candidates(["pexels", "pixabay", "coverr"], usage="media")
        stock_order = selector.get_provider_order("stock_video", stock_candidates)
        results["provider_orders"]["stock_video"] = stock_order

        if clips_needed > 0:
            results["stock_clips"] = self._with_backoff(
                "stock clips",
                lambda: self._fetch_stock_clips(
                    state,
                    nicho,
                    clips_needed,
                    provider_order=stock_order,
                ),
                is_success=lambda value: len(value) > 0,
                max_attempts=2,
            )

        stock_sources = {
            item.get("provider", "")
            for item in results["stock_clips"]
            if isinstance(item, dict) and item.get("provider")
        }
        if stock_sources:
            for provider in stock_sources:
                selector.mark_result("stock_video", provider, True)
        elif stock_order:
            selector.mark_result("stock_video", stock_order[0], False, "no clips returned")

        # --- 2. Images (with visual direction from StoryState) ---
        image_candidates = strict_free_candidates(
            ["pexels", "pixabay", "leonardo", "pollinations"],
            usage="media",
        )
        if not image_candidates:
            image_candidates = ["pixabay", "pollinations"]
        image_order = selector.get_provider_order("image_generation", image_candidates)
        results["provider_orders"]["image_generation"] = image_order

        image_payload = self._with_backoff(
            "image generation",
            lambda: self._generate_images(
                state,
                nicho,
                timestamp,
                temp_dir,
                provider_order=image_order,
                runtime_overrides=runtime_overrides,
            ),
            is_success=lambda value: len(value[0]) > 0,
            max_attempts=2,
        )
        results["images"], image_stats = image_payload

        if settings.enable_openmontage_free_tools and settings.openmontage_enable_enhancement:
            results["images"] = self._enhance_images_openmontage(results["images"], temp_dir, timestamp)

        for provider, stats in image_stats.items():
            if stats.get("ok", 0) > 0:
                selector.mark_result("image_generation", provider, True)
            elif stats.get("fail", 0) > 0:
                selector.mark_result("image_generation", provider, False, "image generation failed")

        # --- 3. Music (mood from script) ---
        music_candidates = strict_free_candidates(["suno", "lyria", "pixabay", "jamendo"], usage="media")
        if settings.suno_api_key and settings.use_suno_music and "suno" not in music_candidates:
            music_candidates.insert(0, "suno")
        music_order = selector.get_provider_order("music_generation", music_candidates)
        results["provider_orders"]["music_generation"] = music_order

        music_payload = self._with_backoff(
            "music generation",
            lambda: self._fetch_music(
                state,
                nicho,
                timestamp,
                temp_dir,
                provider_order=music_order,
            ),
            is_success=lambda value: value[0] is not None,
            max_attempts=2,
        )
        results["music_path"], music_source = music_payload
        results["provider_sources"]["music"] = music_source

        if music_source and music_source != "none":
            selector.mark_result("music_generation", music_source, True)
        elif music_order:
            selector.mark_result("music_generation", music_order[0], False, "no music source succeeded")

        # --- 4. SFX ---
        try:
            from pipeline.sfx import fetch_sfx
            results["sfx_paths"] = fetch_sfx(timestamp, temp_dir)
        except Exception as e:
            logger.debug(f"SFX fetch failed: {e}")

        elapsed = round(time.time() - t0, 2)
        logger.info(
            f"🎨 Assets ready: "
            f"Stock={len(results['stock_clips'])}, "
            f"Images={len(results['images'])}, "
            f"Music={'✅' if results['music_path'] else '❌'} "
            f"({elapsed}s)"
        )
        return results

    def _fetch_stock_clips(
        self,
        state: StoryState,
        nicho: NichoConfig,
        count: int,
        provider_order: Optional[list[str]] = None,
    ) -> list[dict]:
        """Fetch stock clips using scene-aware keywords."""
        try:
            from pipeline.video_stock import fetch_stock_videos

            query_plan = self._build_stock_query_plan(state, nicho, count)
            keywords = query_plan["queries"]
            if not keywords:
                logger.warning("Stock keyword builder returned empty list; using niche slug fallback")
                keywords = [nicho.slug]

            urls = fetch_stock_videos(
                keywords,
                count,
                provider_order=provider_order,
                require_realistic=bool(query_plan.get("require_realistic")),
            )
            logger.info(
                f"📦 Stock: fetching {count} clips "
                f"[profile={query_plan.get('profile', 'default')}] "
                f"with keywords={', '.join(keywords[:6])}"
            )
            return urls

        except Exception as e:
            logger.debug(f"Stock fetch failed: {e}")
            return []

    def _build_stock_query_plan(self, state: StoryState, nicho: NichoConfig, count: int) -> dict:
        profile = self._detect_visual_context(state, nicho)
        queries = self._build_stock_keywords(state, nicho, count, profile)
        return {
            "profile": profile["label"],
            "require_realistic": profile["require_realistic"],
            "queries": queries,
        }

    def _build_stock_keywords(
        self,
        state: StoryState,
        nicho: NichoConfig,
        count: int,
        profile: Optional[dict] = None,
    ) -> list[str]:
        """Build high-signal stock keywords and filter cross-domain mismatches."""
        profile = profile or self._detect_visual_context(state, nicho)
        raw_content = getattr(state, "_raw_content", {}) or {}
        seeds: list[str] = []

        raw_keywords = raw_content.get("palabras_clave", [])
        if isinstance(raw_keywords, list):
            seeds.extend(str(x) for x in raw_keywords[: max(nicho.keywords_count, 8)])

        seeds.extend(state.key_points[:6])
        seeds.extend([state.hook, state.topic])
        
        # Identify high-tension scenes to prioritize mood over literal keywords
        high_tension_scenes = [s for s in state.scenes if s.mood in {"shock", "tense", "revelatory", "suspense"}]
        is_climax_active = any(s.mood in {"shock", "revelatory"} for s in state.scenes[:count])

        for scene in state.scenes[: max(4, count)]:
            if scene.mood in {"shock", "tense", "revelatory"}:
                # For high tension, use the mood itself as a seed to avoid literal "peaceful" visuals
                seeds.append(f"{scene.mood} atmosphere")
            seeds.append(scene.text)

        # Detect scientific/academic context to avoid celebration mismatches
        is_scientific_context = self._detect_scientific_context(seeds)
        is_historical = profile.get("historical", False)
        decade_suffix = profile.get("decade_suffix", "")

        candidates: list[str] = list(profile.get("anchors", []))
        for seed in seeds:
            normalized = self._normalize_keyword(seed)
            if not normalized:
                continue

            translated = self._translate_stock_phrase(normalized)
            if translated and self._is_allowed_stock_candidate(translated, profile, nicho.slug):
                if is_historical and decade_suffix and not any(x in translated for x in ["vintage", "historical", "19"]):
                    translated = f"{decade_suffix} {translated}"
                candidates.append(translated)

            if " " in normalized and len(normalized.split()) <= 4 and len(normalized) <= 40:
                phrase_candidate = self._translate_stock_phrase(normalized)
                if self._is_allowed_stock_candidate(phrase_candidate, profile, nicho.slug):
                    if is_historical and decade_suffix and not any(x in phrase_candidate for x in ["vintage", "historical", "19"]):
                        phrase_candidate = f"{decade_suffix} {phrase_candidate}"
                    candidates.append(phrase_candidate)
            for token in normalized.split():
                if len(token) >= 4:
                    if is_scientific_context and self._is_family_related_keyword(token):
                        continue
                    token_candidate = self._translate_stock_phrase(token)
                    if self._is_allowed_stock_candidate(token_candidate, profile, nicho.slug):
                        if is_historical and decade_suffix and not any(x in token_candidate for x in ["vintage", "historical", "19"]):
                            token_candidate = f"{decade_suffix} {token_candidate}"
                        candidates.append(token_candidate)

        seen: set[str] = set()
        filtered: list[str] = []
        for kw in candidates:
            if kw in seen:
                continue
            seen.add(kw)
            if not self._is_allowed_stock_candidate(kw, profile, nicho.slug):
                continue
            if is_scientific_context and self._is_celebration_keyword(kw):
                continue
            filtered.append(kw)

        anchors_by_nicho = {
            "finanzas": [
                "finanzas", "dinero", "inversion", "inflacion", "ahorro",
                "economia", "mercado", "deuda", "emprendimiento", "negocio",
                "dolar", "peso",
            ],
        }
        for anchor in anchors_by_nicho.get(nicho.slug, []):
            if anchor not in seen:
                filtered.append(anchor)
                seen.add(anchor)

        limit = max(nicho.keywords_count, 8)
        return filtered[:limit]

    def _detect_scientific_context(self, seeds: list[str]) -> bool:
        """Detect if the content is scientific/academic to avoid celebration mismatches."""
        scientific_markers = {
            "estudio", "universidad", "cientifico", "investigacion", "datos",
            "neurociencia", "psicologia", "comportamiento", "experimento",
            "ciencia", "analisis", "estadistica", "hipotesis", "research",
            "university", "scientific", "neuroscience", "psychology",
        }
        all_text = " ".join(seeds).lower()
        return any(marker in all_text for marker in scientific_markers)

    def _detect_visual_context(self, state: StoryState, nicho: NichoConfig) -> dict:
        blob = " ".join(
            filter(
                None,
                [
                    state.topic,
                    state.hook,
                    state.script_full,
                    state.reference_summary,
                    " ".join(state.reference_key_points[:4]),
                    " ".join(state.key_points[:6]),
                    " ".join(scene.text for scene in state.scenes[:6]),
                    nicho.tono,
                    nicho.estilo_narrativo,
                ],
            )
        ).lower()
        years = re.findall(r"\b(18\d{2}|19\d{2})\b", blob)
        historical = bool(years) or any(marker in blob for marker in self._HISTORICAL_MARKERS)
        dark = any(marker in blob for marker in self._DARK_MARKERS) or any(
            scene.mood in {"shock", "tense", "revelatory"} for scene in state.scenes[:6]
        )
        label = "general"
        anchors: list[str] = []
        decade_suffix = ""
        if historical and dark:
            label = "historical_dark"
            decade_suffix = f"{years[0][:3]}0s" if years else "vintage"
            anchors.extend([
                f"{decade_suffix} investigation",
                "vintage documents",
                "archival newspaper",
                "detective files",
                "shadowy hallway",
            ])
        elif historical:
            label = "historical"
            decade_suffix = f"{years[0][:3]}0s" if years else "vintage"
            anchors.extend([
                f"{decade_suffix} archive",
                "vintage documents",
                "period street",
                "old newspaper",
                "courtroom history",
            ])
        elif dark:
            label = "dark_investigation"
            anchors.extend([
                "surveillance shadows",
                "detective board",
                "secret files",
                "crime evidence",
                "moody corridor",
            ])
        else:
            label = "default"
            topic_seed = self._translate_stock_phrase(self._normalize_keyword(state.topic))
            hook_seed = self._translate_stock_phrase(self._normalize_keyword(state.hook))
            anchors.extend([topic_seed, hook_seed])

        return {
            "label": label,
            "historical": historical,
            "dark": dark,
            "decade_suffix": decade_suffix,
            "require_realistic": historical or dark or nicho.slug in {"historia", "historias_reddit"},
            "anchors": [a for a in anchors if a],
        }

    def _translate_stock_phrase(self, value: str) -> str:
        mapping = {
            "asesinato": "murder",
            "asesinatos": "murders",
            "crimen": "crime",
            "crimenes": "crimes",
            "conspiracion": "conspiracy",
            "conspiraciones": "conspiracy",
            "vigilancia": "surveillance",
            "escuchas": "wiretap",
            "justicia": "justice",
            "tribu": "tribal",
            "tribal": "tribal",
            "investigacion": "investigation",
            "investigaciones": "investigation",
            "policia": "police",
            "documentos": "documents",
            "documento": "document",
            "archivo": "archive",
            "periodico": "newspaper",
            "periodicos": "newspaper",
            "secreto": "secret",
            "secretos": "secret",
            "oscuro": "dark",
            "sombras": "shadows",
            "shock": "shocking",
            "tense": "tense",
            "revelatory": "revelation",
            "suspense": "suspense",
            "misterio": "mystery",
        }
        text = str(value or "").strip().lower()
        if not text:
            return ""
        translated = " ".join(mapping.get(token, token) for token in text.split()).strip()
        translated = re.sub(r"\b(18\d)\d\b", r"\1s", translated)
        translated = re.sub(r"\b(19\d)\d\b", r"\1s", translated)
        return translated

    def _is_allowed_stock_candidate(self, keyword: str, profile: dict, nicho_slug: str) -> bool:
        normalized = self._normalize_keyword(keyword)
        if not normalized:
            return False
        
        # Block if ANY token in the keyword is ambiguous
        for token in normalized.split():
            if token in self._AMBIGUOUS_STOCK_TOKENS:
                return False
                
        if any(token in normalized for token in self._CARTOON_BLOCKLIST):
            return False
        if profile.get("historical") and any(token in normalized for token in self._MODERN_BLOCKLIST):
            return False
        if profile.get("dark") and any(token in normalized for token in {"sunny", "park", "happy", "smile", "playful"}):
            return False
        return not self._is_blocked_keyword(normalized, nicho_slug)

    def _is_family_related_keyword(self, token: str) -> bool:
        """Check if a token is family-related and could trigger celebration clips."""
        family_tokens = {
            "hijos", "padres", "madre", "padre", "papa", "mama",
            "children", "parents", "mother", "father", "dad", "mom",
            "familia", "family", "unico", "only", "hijo", "hija",
        }
        return token.lower() in family_tokens

    def _is_celebration_keyword(self, kw: str) -> bool:
        """Check if keyword is celebration/holiday related."""
        normalized = kw.lower().strip()
        return normalized in self._CELEBRATION_KEYWORDS

    # Context-aware keyword blocking: prevents mismatches like Father's Day
    # clips appearing when discussing psychology studies about "hijos" or "padres"
    _BLOCKED_KEYWORDS_BY_CONTEXT: dict[str, set[str]] = {
        # Scientific/academic contexts should not match with holiday/celebration footage
        "estudio", "universidad", "cientifico", "investigacion", "datos",
        "neurociencia", "psicologia", "comportamiento", "experimento",
        # These keywords alone can trigger wrong visuals
    }

    _CELEBRATION_KEYWORDS: set[str] = {
        "dad", "father", "happy father's day", "fathers day", "papa", "papá",
        "dia del padre", "celebracion", "celebration", "holiday", "fiesta",
        "fireworks", "bengala", "sparkler", "confetti", "party",
        "mom", "mother", "mama", "mamá", "dia de la madre", "navidad",
        "christmas", "birthday", "cumpleaños", "aniversario", "anniversary",
    }

    def _is_blocked_keyword(self, keyword: str, nicho_slug: str) -> bool:
        """Prevent obvious cross-domain stock matches for each niche."""
        normalized = keyword.lower().strip()

        # Block celebration/holiday keywords when they appear out of context
        if normalized in self._CELEBRATION_KEYWORDS:
            return True

        # Check for celebration-related substrings in longer phrases
        for celeb_kw in self._CELEBRATION_KEYWORDS:
            if celeb_kw in normalized and len(celeb_kw) >= 4:
                return True

        if nicho_slug != "finanzas":
            return False

        blocked = {
            "aerobics", "aerobic", "fitness", "workout", "gym", "crossfit",
            "entrenamiento", "deporte", "electrocardiograma", "hospital",
            "doctor", "medico", "salud", "cardio", "yoga", "wellness",
            "india", "indio", "rupia", "rupee",
        }
        return any(token in keyword for token in blocked)

    @staticmethod
    def _normalize_keyword(value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r"[^a-z0-9\s_-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _generate_images(
        self,
        state: StoryState,
        nicho: NichoConfig,
        timestamp: int,
        temp_dir: Path,
        provider_order: Optional[list[str]] = None,
        runtime_overrides: Optional[dict] = None,
    ) -> tuple[list[Path], dict[str, dict[str, int]]]:
        """Generate images with visual direction from StoryState."""
        try:
            from pipeline.image_gen import generate_images_with_stats

            raw_content = getattr(state, "_raw_content", {})

            prompt_base = raw_content.get("prompt_imagen", "")
            if not prompt_base:
                prompt_base = state.visual_direction or nicho.nombre

            scene_visuals = [s.visual_prompt for s in state.scenes[:3] if s.visual_prompt]
            if scene_visuals:
                prompt_base = f"{prompt_base}. {' '.join(scene_visuals[:2])[:420]}"

            if state.reference_summary:
                prompt_base = f"{prompt_base}. {state.reference_summary[:180]}"

            if state.color_palette:
                prompt_base += f", {state.color_palette}"

            ab_variant = raw_content.get("_ab_variant", "A")

            overrides = runtime_overrides or {}
            prefer_stock_images = overrides.get("prefer_stock_images")
            cache_ttl_days = overrides.get("media_cache_ttl_days")

            enable_cache = overrides.get("enable_image_cache")
            if "disable_image_cache" in overrides:
                enable_cache = not bool(overrides.get("disable_image_cache"))

            try:
                image_count = int(overrides.get("generated_images_count", settings.generated_images_count))
            except (TypeError, ValueError):
                image_count = int(settings.generated_images_count)
            image_count = max(4, min(10, image_count))

            images, stats = generate_images_with_stats(
                prompt_base,
                nicho.direccion_visual,
                ab_variant,
                timestamp,
                temp_dir,
                count=image_count,
                provider_order=provider_order,
                prefer_stock_images=prefer_stock_images,
                cache_ttl_days=cache_ttl_days,
                enable_cache=enable_cache,
            )
            return images, stats

        except Exception as e:
            logger.warning(f"Image generation failed: {e}")
            return [], {
                "pexels": {"ok": 0, "fail": 0},
                "pixabay": {"ok": 0, "fail": 0},
                "leonardo": {"ok": 0, "fail": 0},
                "pollinations": {"ok": 0, "fail": 0},
            }

    def _fetch_music(
        self,
        state: StoryState,
        nicho: NichoConfig,
        timestamp: int,
        temp_dir: Path,
        provider_order: Optional[list[str]] = None,
    ) -> tuple[Optional[Path], str]:
        """Fetch music based on script mood."""
        music_path = temp_dir / f"musica_{timestamp}.mp3"

        # Get mood from raw content or scenes
        raw_content = getattr(state, "_raw_content", {})
        mood = raw_content.get("mood_musica", nicho.genero_musica)

        # Use dominant scene mood if available
        if state.scenes:
            scene_moods = [s.mood for s in state.scenes]
            mood_mapping = {
                "tense": "dark",
                "shock": "epic",
                "inspiring": "motivational",
                "calm": "ambient",
                "revelatory": "cinematic",
            }
            dominant = max(set(scene_moods), key=scene_moods.count)
            mood = mood_mapping.get(dominant, mood)

        try:
            from pipeline.music_ai import fetch_music_with_fallback_source

            ok, source = fetch_music_with_fallback_source(
                mood, music_path,
                duration_seconds=state.total_duration() or 30,
                nicho=nicho.slug,
                provider_order=provider_order,
            )
            if ok and music_path.exists() and music_path.stat().st_size > 1000:
                return music_path, source
        except Exception:
            pass

        try:
            from pipeline.music import fetch_music
            ok, source = fetch_music(mood, music_path)
            if music_path.exists() and music_path.stat().st_size > 1000:
                return music_path, source if ok else "none"
        except Exception as e:
            logger.debug(f"Music fetch failed: {e}")

        return None, "none"

    def _enhance_images_openmontage(
        self,
        images: list[Path],
        temp_dir: Path,
        timestamp: int,
    ) -> list[Path]:
        """Best-effort OpenMontage enhancement chain for generated images.

        Keeps original images whenever a tool is unavailable or fails.
        """
        if not images:
            return images

        enhanced: list[Path] = []
        for idx, img in enumerate(images):
            current = img
            try:
                face_path = temp_dir / f"img_face_{timestamp}_{idx}.png"
                restored = apply_face_restore(current, face_path)
                if restored:
                    current = restored

                nobg_path = temp_dir / f"img_nobg_{timestamp}_{idx}.png"
                nobg = apply_bg_remove(current, nobg_path)
                if nobg:
                    current = nobg

                upscale_path = temp_dir / f"img_up_{timestamp}_{idx}.png"
                upscaled = apply_upscale(current, upscale_path, scale=2)
                if upscaled:
                    current = upscaled
            except Exception as exc:
                logger.debug(f"OpenMontage image enhancement skipped: {exc}")

            enhanced.append(current)

        return enhanced

    def _with_backoff(
        self,
        label: str,
        func: Callable[[], T],
        is_success: Callable[[T], bool],
        max_attempts: int = 2,
        base_delay: float = 1.6,
    ) -> T:
        """Retry a stage-level operation with exponential backoff."""
        result = func()
        if is_success(result):
            return result

        for attempt in range(2, max_attempts + 1):
            delay = round(base_delay ** (attempt - 1), 2)
            logger.warning(f"{label} retry {attempt}/{max_attempts} in {delay}s")
            time.sleep(delay)
            result = func()
            if is_success(result):
                return result

        return result
