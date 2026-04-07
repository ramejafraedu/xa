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

import time
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

    def run(
        self,
        state: StoryState,
        nicho: NichoConfig,
        timestamp: int,
        temp_dir: Path,
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
        image_candidates = strict_free_candidates(["leonardo", "pollinations"], usage="media")
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
        music_candidates = strict_free_candidates(["lyria", "pixabay", "jamendo"], usage="media")
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

            # Use keywords from raw content (V14 compat)
            raw_content = getattr(state, "_raw_content", {})
            keywords = raw_content.get("palabras_clave", [])[:nicho.keywords_count]

            if not keywords:
                # Fallback: extract from scene texts
                keywords = []
                for scene in state.scenes[:count]:
                    words = scene.text.split()[:2]
                    keywords.extend(words)

            urls = fetch_stock_videos(keywords, count, provider_order=provider_order)
            logger.info(f"📦 Stock: fetching {count} clips")
            return urls

        except Exception as e:
            logger.debug(f"Stock fetch failed: {e}")
            return []

    def _generate_images(
        self,
        state: StoryState,
        nicho: NichoConfig,
        timestamp: int,
        temp_dir: Path,
        provider_order: Optional[list[str]] = None,
    ) -> tuple[list[Path], dict[str, dict[str, int]]]:
        """Generate images with visual direction from StoryState."""
        try:
            from pipeline.image_gen import generate_images_with_stats

            raw_content = getattr(state, "_raw_content", {})

            # Build enhanced prompt using scene context
            prompt_base = raw_content.get("prompt_imagen", "")
            if not prompt_base:
                prompt_base = state.visual_direction or nicho.nombre

            # Add StoryState visual coherence
            if state.color_palette:
                prompt_base += f", {state.color_palette}"

            ab_variant = raw_content.get("_ab_variant", "A")

            images, stats = generate_images_with_stats(
                prompt_base,
                nicho.direccion_visual,
                ab_variant,
                timestamp,
                temp_dir,
                provider_order=provider_order,
            )
            return images, stats

        except Exception as e:
            logger.warning(f"Image generation failed: {e}")
            return [], {
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
