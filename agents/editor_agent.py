"""Video Factory V15 — Editor Agent.

Makes intelligent editing decisions based on scene mood,
camera notes, and transitions from the ScenePlanner.

Replaces the hardcoded FFmpeg parameters in V14 with
scene-aware dynamic editing.

MODULE CONTRACT:
  Input:  StoryState.scenes + raw clips/images
  Output: Editing decisions (durations, transitions, effects per clip)
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from core.state import SceneBlueprint, StoryState
from models.config_models import NichoConfig


class EditDecision:
    """Editing instructions for a single clip/scene."""

    def __init__(
        self,
        clip_index: int,
        duration: float,
        zoom_type: str = "none",      # "zoom_in", "zoom_out", "ken_burns", "none"
        zoom_intensity: float = 0.0,
        fade_in: float = 0.15,
        fade_out: float = 0.15,
        transition_out: str = "cut",   # "cut", "fade", "whip"
        color_grade: str = "default",  # "warm", "cold", "dark", "default"
        speed_factor: float = 1.0,     # 1.0 = normal, <1 = slow-mo, >1 = timelapse
    ):
        self.clip_index = clip_index
        self.duration = duration
        self.zoom_type = zoom_type
        self.zoom_intensity = zoom_intensity
        self.fade_in = fade_in
        self.fade_out = fade_out
        self.transition_out = transition_out
        self.color_grade = color_grade
        self.speed_factor = speed_factor

    def to_dict(self) -> dict:
        """Serialize decision for logs/manifests/timeline debug."""
        return {
            "clip_index": self.clip_index,
            "duration": self.duration,
            "zoom_type": self.zoom_type,
            "zoom_intensity": self.zoom_intensity,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
            "transition_out": self.transition_out,
            "color_grade": self.color_grade,
            "speed_factor": self.speed_factor,
        }


class EditorAgent:
    """Generate intelligent editing decisions from scene plan."""

    def run(
        self,
        state: StoryState,
        nicho: NichoConfig,
        num_clips: int,
        audio_duration: float,
    ) -> list[EditDecision]:
        """Create per-clip editing decisions based on scene mood/cameras.

        Args:
            state: StoryState with approved scenes.
            nicho: Niche config.
            num_clips: Actual number of available clips.
            audio_duration: Total audio duration.

        Returns:
            List of EditDecision, one per clip.
        """
        t0 = time.time()
        decisions = []

        scenes = state.scenes
        if num_clips <= 0:
            # No clip decisions to make when render will be image-only.
            logger.warning("EditorAgent: no clips available, skipping clip edit decisions")
            return []

        if not scenes:
            # Fallback: even distribution with default settings
            return self._default_decisions(num_clips, audio_duration, nicho)

        # Map scenes to actual clips (scenes may != clips)
        clip_scenes = self._map_scenes_to_clips(scenes, num_clips, audio_duration)

        for i, (scene, duration) in enumerate(clip_scenes):
            decision = self._scene_to_decision(i, scene, duration)
            decision = self._apply_reference_pacing(state, i, decision)
            decisions.append(decision)

        # Keep total edit length aligned with usable narration duration.
        target_total = max(4.0, audio_duration - 2.0)
        self._rescale_decisions(decisions, target_total)

        elapsed = round(time.time() - t0, 2)
        logger.info(
            f"🎞️ Edit decisions: {len(decisions)} clips, "
            f"total {sum(d.duration for d in decisions):.1f}s ({elapsed}s)"
        )
        return decisions

    def build_timeline_json(
        self,
        state: StoryState,
        media_paths: list[Path],
        decisions: list[EditDecision],
        audio_duration: float,
        timeline_path: Path,
        subtitles_path: Optional[Path] = None,
        narration_audio_path: Optional[Path] = None,
        music_path: Optional[Path] = None,
        composition_id: str = "UniversalCommercial",
        style_playbook: str = "",
    ) -> dict:
        """Build and persist a Remotion-compatible timeline JSON.

        The timeline is a structured artifact consumed by the Remotion renderer.
        """
        timeline = self._build_timeline_payload(
            state=state,
            media_paths=media_paths,
            decisions=decisions,
            audio_duration=audio_duration,
            subtitles_path=subtitles_path,
            narration_audio_path=narration_audio_path,
            music_path=music_path,
            style_playbook=style_playbook,
        )

        resolved_composition = str(composition_id or "UniversalCommercial").strip() or "UniversalCommercial"
        timeline["composition_id"] = resolved_composition
        meta = timeline.get("meta") if isinstance(timeline.get("meta"), dict) else {}
        meta["composition_id"] = resolved_composition
        timeline["meta"] = meta

        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(
            json.dumps(timeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(
            f"🧭 Timeline JSON generado: {timeline_path.name} "
            f"({len(timeline.get('scenes', []))} escenas)"
        )
        return timeline

    def build_incremental_eml_seed(
        self,
        state: StoryState,
        media_paths: list[Path],
        decisions: list[EditDecision],
        audio_duration: float,
    ) -> dict:
        """Build an incremental edit_decisions seed from editor decisions.

        This seed is consumed as a fallback mapper when timeline-derived cuts are
        unavailable for specific compositions.
        """
        valid_media = [p for p in media_paths if p and p.exists()]
        cuts: list[dict] = []

        working_decisions = list(decisions)
        if not working_decisions and valid_media:
            fallback_dur = max(1.2, audio_duration / max(len(valid_media), 1))
            working_decisions = [
                EditDecision(clip_index=i, duration=fallback_dur)
                for i in range(len(valid_media))
            ]

        for idx, media_path in enumerate(valid_media):
            if not working_decisions:
                decision = EditDecision(clip_index=idx, duration=max(1.2, audio_duration / max(len(valid_media), 1)))
            else:
                decision = working_decisions[min(idx, len(working_decisions) - 1)]

            duration = max(0.8, float(decision.duration or 0.0))
            transform: dict = {
                "scale": 1.0,
                "position": "center",
            }
            animation = self._zoom_to_transform_animation(decision.zoom_type)
            if animation:
                transform["animation"] = animation

            transition_out = self._normalize_transition_for_eml(decision.transition_out)
            scene_text = state.scenes[idx].text if idx < len(state.scenes) else ""

            cuts.append(
                {
                    "id": f"editor_cut_{idx + 1}",
                    "source": str(media_path.resolve().as_posix()),
                    "in_seconds": 0.0,
                    "out_seconds": round(duration, 3),
                    "speed": max(0.1, float(decision.speed_factor or 1.0)),
                    "layer": "primary",
                    "transform": transform,
                    "transition_in": "cut",
                    "transition_out": transition_out,
                    "transition_duration": round(max(0.0, float(decision.fade_out or 0.0)), 3),
                    "reason": (scene_text or f"Editor mapped cut {idx + 1}")[:180],
                }
            )

        return {
            "version": "1.0",
            "mapper": "editor_incremental_v1",
            "cuts": cuts,
            "metadata": {
                "scene_count": len(state.scenes),
                "media_count": len(valid_media),
                "audio_duration_seconds": round(float(audio_duration or 0.0), 3),
            },
        }

    def _build_timeline_payload(
        self,
        state: StoryState,
        media_paths: list[Path],
        decisions: list[EditDecision],
        audio_duration: float,
        subtitles_path: Optional[Path],
        narration_audio_path: Optional[Path],
        music_path: Optional[Path],
        style_playbook: str = "",
    ) -> dict:
        """Create timeline payload with scene timing, style and captions."""
        valid_media = [p for p in media_paths if p and p.exists()]
        scenes: list[dict] = []
        cursor = 0.0

        # Guarantee there is one decision per media item (or a safe fallback).
        working_decisions = list(decisions)
        if not working_decisions and valid_media:
            fallback_dur = max(1.2, audio_duration / max(len(valid_media), 1))
            working_decisions = [
                EditDecision(clip_index=i, duration=fallback_dur)
                for i in range(len(valid_media))
            ]

        if valid_media:
            for idx, media_path in enumerate(valid_media):
                decision = working_decisions[min(idx, len(working_decisions) - 1)]
                duration = max(0.8, float(decision.duration or 0.0))
                scene_text = state.scenes[idx].text if idx < len(state.scenes) else ""

                scenes.append(
                    {
                        "id": f"scene_{idx + 1}",
                        "kind": "video",
                        "src": str(media_path.resolve().as_posix()),
                        "startSeconds": round(cursor, 3),
                        "durationSeconds": round(duration, 3),
                        "tone": self._tone_for_grade(decision.color_grade),
                        "fadeInFrames": max(0, int(round(decision.fade_in * 30))),
                        "fadeOutFrames": max(0, int(round(decision.fade_out * 30))),
                        "filter": self._css_filter_for_grade(decision.color_grade),
                        # Extra fields kept for traceability/debug in render stage.
                        "zoomType": decision.zoom_type,
                        "zoomIntensity": decision.zoom_intensity,
                        "transitionOut": decision.transition_out,
                        "speedFactor": decision.speed_factor,
                        "sceneText": scene_text[:220],
                    }
                )
                cursor += duration
        else:
            # Last-resort title card so Remotion receives a valid composition payload.
            scenes.append(
                {
                    "id": "title_fallback",
                    "kind": "title",
                    "text": state.hook or state.topic or "Video Factory",
                    "startSeconds": 0.0,
                    "durationSeconds": round(max(3.0, audio_duration), 3),
                    "accent": "#86d8ff",
                    "intensity": 1.0,
                }
            )

        if scenes and audio_duration > 0:
            total = sum(float(s.get("durationSeconds", 0.0)) for s in scenes)
            if total < audio_duration:
                scenes[-1]["durationSeconds"] = round(
                    float(scenes[-1].get("durationSeconds", 0.0)) + (audio_duration - total),
                    3,
                )

        captions_words = self._parse_ass_word_captions(subtitles_path)
        palette_hexes = self._extract_palette_hexes(state.color_palette)
        theme_name = self._resolve_visual_theme(state, style_playbook)
        heading_font = self._resolve_heading_font(theme_name)
        body_font = self._resolve_body_font(theme_name)

        caption_font_size = 52
        subtitle_style = str(state.style_profile.subtitle_style or "").strip().lower()
        if subtitle_style in {"readable_large", "bold_animated"}:
            caption_font_size = 56
        elif subtitle_style in {"minimal_elegant", "clean_modern"}:
            caption_font_size = 50

        caption_color = "#F8FAFC"
        if theme_name in {"minimal", "minimalist-diagram", "clean-professional"}:
            caption_color = "#111827"

        caption_highlight = palette_hexes[1] if len(palette_hexes) > 1 else (
            palette_hexes[0] if palette_hexes else "#FBBF24"
        )

        caption_background = "rgba(0, 0, 0, 0.60)"
        if theme_name in {"minimal", "minimalist-diagram", "clean-professional"}:
            caption_background = "rgba(255, 255, 255, 0.86)"

        primary_color = palette_hexes[0] if palette_hexes else self._default_primary_for_theme(theme_name)
        accent_color = caption_highlight

        transitions = [str(x).strip().lower() for x in (state.style_profile.transitions or []) if str(x).strip()]
        transition_preset = "swipe" if any(t in {"whip", "zoom_cut", "wipe"} for t in transitions) else "slide"

        cut_speed = str(state.style_profile.cut_speed or "").strip().lower()
        kinetic_level = "dynamic" if cut_speed in {"ultra_rapido", "rapido"} else "soft"
        if cut_speed == "ultra_rapido":
            kinetic_level = "intense"

        layout_variant = "split"
        if theme_name in {"minimal", "minimalist-diagram"}:
            layout_variant = "stacked"

        captions = None
        if captions_words:
            captions = {
                "words": captions_words,
                "wordsPerPage": 4,
                "fontSize": caption_font_size,
                "color": caption_color,
                "highlightColor": caption_highlight,
                "backgroundColor": caption_background,
            }

        soundtrack = None
        if narration_audio_path and narration_audio_path.exists():
            soundtrack = {
                "src": str(narration_audio_path.resolve().as_posix()),
                "volume": 1.0,
                "fadeInSeconds": 0.15,
                "fadeOutSeconds": 0.20,
            }

        music = None
        if music_path and music_path.exists():
            music = {
                "src": str(music_path.resolve().as_posix()),
                "volume": max(0.05, min(0.65, float(state.style_profile.music_volume or 0.16))),
                "fadeInSeconds": 0.5,
                "fadeOutSeconds": 1.2,
            }

        visual_style = {
            "theme": theme_name,
            "primaryColor": primary_color,
            "accentColor": accent_color,
            "fontFamily": f"{heading_font}, sans-serif",
            "layoutVariant": layout_variant,
            "kineticLevel": kinetic_level,
            "transitionPreset": transition_preset,
            "featureCardMode": "window",
        }

        theme_config = {
            "primaryColor": primary_color,
            "accentColor": accent_color,
            "headingFont": heading_font,
            "bodyFont": body_font,
            "captionHighlightColor": caption_highlight,
            "captionBackgroundColor": caption_background,
            "transitionDuration": 0.3 if transition_preset == "swipe" else 0.45,
        }

        style_meta = {
            "cut_speed": state.style_profile.cut_speed,
            "subtitle_style": state.style_profile.subtitle_style,
            "music_volume": float(state.style_profile.music_volume),
            "transitions": list(state.style_profile.transitions or []),
            "visual_base": state.style_profile.visual_base,
            "aspect_ratio": state.style_profile.aspect_ratio,
            "color_palette": state.color_palette,
        }

        caption_style = {
            "fontSize": caption_font_size,
            "color": caption_color,
            "highlightColor": caption_highlight,
            "backgroundColor": caption_background,
            "wordsPerPage": 4,
        }

        playbook_slug = str(style_playbook or "").strip()

        return {
            "timelineVersion": "1.0",
            "generatedBy": "EditorAgent",
            "meta": {
                "topic": state.topic,
                "platform": state.platform,
                "audioDurationSeconds": round(audio_duration, 3),
                "sceneCount": len(scenes),
                "playbook": playbook_slug,
                "theme": theme_name,
                "style_profile": style_meta,
                "captionStyle": caption_style,
            },
            "playbook": playbook_slug,
            "theme": theme_name,
            "themeConfig": theme_config,
            "style": visual_style,
            "layoutVariant": layout_variant,
            "kineticLevel": kinetic_level,
            "transitionPreset": transition_preset,
            "featureCardMode": "window",
            "scenes": scenes,
            "soundtrack": soundtrack,
            "music": music,
            "captions": captions,
            "titleFontSize": 72,
            "titleWidth": 860,
            "signalLineCount": 18,
        }

    @staticmethod
    def _extract_palette_hexes(palette_text: str) -> list[str]:
        if not palette_text:
            return []
        return [c.upper() for c in re.findall(r"#[0-9A-Fa-f]{3,8}", palette_text)]

    @staticmethod
    def _resolve_visual_theme(state: StoryState, style_playbook: str) -> str:
        playbook = str(style_playbook or "").strip().lower()
        if playbook in {
            "clean-professional",
            "flat-motion-graphics",
            "minimalist-diagram",
            "anime-ghibli",
            "cyberpunk",
            "minimal",
            "playful",
        }:
            return playbook

        playbook_map = {
            "finanzas": "minimalist-diagram",
            "curiosidades": "playful",
            "historia": "clean-professional",
            "historias_reddit": "minimal",
            "ia_herramientas": "cyberpunk",
        }
        if playbook in playbook_map:
            return playbook_map[playbook]

        platform_map = {
            "tiktok": "flat-motion-graphics",
            "tiktok_reels": "flat-motion-graphics",
            "reels": "playful",
            "shorts": "clean-professional",
            "facebook": "minimal",
        }
        platform = str(state.platform or "").strip().lower()
        if platform in platform_map:
            return platform_map[platform]

        cut_speed = str(state.style_profile.cut_speed or "").strip().lower()
        if cut_speed == "ultra_rapido":
            return "flat-motion-graphics"
        if cut_speed == "cinematografico":
            return "clean-professional"
        return "minimal"

    @staticmethod
    def _default_primary_for_theme(theme_name: str) -> str:
        defaults = {
            "clean-professional": "#2563EB",
            "flat-motion-graphics": "#7C3AED",
            "minimalist-diagram": "#1A1A2E",
            "anime-ghibli": "#2D5016",
            "cyberpunk": "#0F172A",
            "minimal": "#334155",
            "playful": "#BE123C",
        }
        return defaults.get(str(theme_name or "").strip().lower(), "#334155")

    @staticmethod
    def _resolve_heading_font(theme_name: str) -> str:
        mapping = {
            "clean-professional": "Inter",
            "flat-motion-graphics": "Space Grotesk",
            "minimalist-diagram": "IBM Plex Sans",
            "anime-ghibli": "Noto Serif JP",
            "cyberpunk": "Orbitron",
            "minimal": "IBM Plex Sans",
            "playful": "Baloo 2",
        }
        return mapping.get(str(theme_name or "").strip().lower(), "Space Grotesk")

    @staticmethod
    def _resolve_body_font(theme_name: str) -> str:
        mapping = {
            "clean-professional": "Inter",
            "flat-motion-graphics": "Space Grotesk",
            "minimalist-diagram": "IBM Plex Sans",
            "anime-ghibli": "Noto Sans",
            "cyberpunk": "Space Grotesk",
            "minimal": "IBM Plex Sans",
            "playful": "Nunito",
        }
        return mapping.get(str(theme_name or "").strip().lower(), "Space Grotesk")

    @staticmethod
    def _tone_for_grade(color_grade: str) -> str:
        """Map V15 color grades to CinematicRenderer tones."""
        grade = (color_grade or "default").lower()
        mapping = {
            "warm": "neutral",
            "cold": "cold",
            "dark": "void",
            "default": "steel",
        }
        return mapping.get(grade, "steel")

    @staticmethod
    def _css_filter_for_grade(color_grade: str) -> str:
        """Map V15 color grades to CSS filter chain used by Remotion."""
        grade = (color_grade or "default").lower()
        mapping = {
            "warm": "contrast(1.05) saturate(1.10) brightness(1.02)",
            "cold": "contrast(1.10) saturate(0.86) brightness(0.97)",
            "dark": "contrast(1.12) saturate(0.82) brightness(0.90)",
            "default": "contrast(1.06) saturate(0.90) brightness(0.98)",
        }
        return mapping.get(grade, mapping["default"])

    @staticmethod
    def _zoom_to_transform_animation(zoom_type: str) -> str:
        clean = str(zoom_type or "").strip().lower()
        mapping = {
            "ken_burns": "ken-burns-slow-zoom",
            "zoom_in": "zoom-in",
            "zoom_out": "zoom-out",
        }
        return mapping.get(clean, "")

    @staticmethod
    def _normalize_transition_for_eml(value: str) -> str:
        clean = str(value or "cut").strip().lower().replace("-", "_").replace(" ", "_")
        mapping = {
            "cut": "cut",
            "hard_cut": "cut",
            "none": "cut",
            "fade": "fade",
            "crossfade": "dissolve",
            "dissolve": "dissolve",
            "wipe": "wipe",
            "whip": "wipe",
            "zoom_cut": "cut",
        }
        return mapping.get(clean, "cut")

    def _parse_ass_word_captions(self, subtitles_path: Optional[Path]) -> list[dict]:
        """Parse ASS dialogue events into word-level caption timing."""
        if not subtitles_path or not subtitles_path.exists():
            return []

        words: list[dict] = []
        try:
            for line in subtitles_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.startswith("Dialogue:"):
                    continue

                parts = line.split(",", 9)
                if len(parts) < 10:
                    continue

                start_s = self._ass_time_to_seconds(parts[1])
                end_s = self._ass_time_to_seconds(parts[2])
                if end_s <= start_s:
                    continue

                raw_text = parts[9]
                if not self._append_karaoke_words(words, raw_text, start_s, end_s):
                    self._append_uniform_words(words, raw_text, start_s, end_s)
        except Exception as exc:
            logger.debug(f"ASS caption parsing skipped: {exc}")

        return words

    @staticmethod
    def _append_karaoke_words(words: list[dict], raw_text: str, start_s: float, end_s: float) -> bool:
        """Extract real word timings from ASS karaoke tags (\\k/\\kf/\\ko)."""
        karaoke_re = re.compile(r"\{\\(?:k|K|kf|KF|ko|KO)(\d+)\}")
        matches = list(karaoke_re.finditer(raw_text or ""))
        if not matches:
            return False

        chunks: list[tuple[float, list[str]]] = []
        for idx, match in enumerate(matches):
            next_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
            chunk_text = raw_text[match.end() : next_pos]
            chunk_text = re.sub(r"\{[^}]*\}", "", chunk_text)
            chunk_text = chunk_text.replace("\\N", " ").replace("\\n", " ")
            chunk_text = re.sub(r"\s+", " ", chunk_text).strip()
            tokens = EditorAgent._tokenize_caption_words(chunk_text)
            if not tokens:
                continue

            dur_cs = max(1, int(match.group(1) or 0))
            chunks.append((dur_cs / 100.0, tokens))

        if not chunks:
            return False

        total_tag_span = sum(span for span, _ in chunks)
        line_span = max(0.04, end_s - start_s)
        scale = (line_span / total_tag_span) if total_tag_span > 0 else 1.0

        cursor = start_s
        line_words: list[dict] = []
        for chunk_span, tokens in chunks:
            alloc = max(0.04, chunk_span * scale)
            step = alloc / max(len(tokens), 1)
            for token in tokens:
                w_start = cursor
                w_end = min(end_s, cursor + step)
                line_words.append(
                    {
                        "word": token,
                        "startMs": int(round(w_start * 1000)),
                        "endMs": int(round(w_end * 1000)),
                    }
                )
                cursor = w_end

        if not line_words:
            return False

        # Anchor the last token to dialogue end to absorb centisecond rounding drift.
        last_end = int(round(end_s * 1000))
        if line_words[-1]["endMs"] < last_end:
            line_words[-1]["endMs"] = last_end

        for item in line_words:
            if item["endMs"] <= item["startMs"]:
                item["endMs"] = item["startMs"] + 40
            words.append(item)
        return True

    @staticmethod
    def _append_uniform_words(words: list[dict], raw_text: str, start_s: float, end_s: float) -> None:
        """Fallback parser when karaoke tags are unavailable in ASS text."""
        clean = re.sub(r"\{[^}]*\}", "", raw_text or "")
        clean = clean.replace("\\N", " ").replace("\\n", " ")
        clean = re.sub(r"\s+", " ", clean).strip()
        tokens = EditorAgent._tokenize_caption_words(clean)
        if not tokens:
            return

        span = max(0.04, end_s - start_s)
        step = span / len(tokens)
        cursor = start_s
        for token in tokens:
            w_start = cursor
            w_end = min(end_s, cursor + step)
            start_ms = int(round(w_start * 1000))
            end_ms = int(round(w_end * 1000))
            if end_ms <= start_ms:
                end_ms = start_ms + 40
            words.append({"word": token, "startMs": start_ms, "endMs": end_ms})
            cursor = w_end

    @staticmethod
    def _tokenize_caption_words(text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9ÁÉÍÓÚáéíóúÑñÜü]+|[^\s]", text or "")

    @staticmethod
    def _ass_time_to_seconds(value: str) -> float:
        """Convert ASS timestamp (H:MM:SS.CS) to seconds."""
        try:
            hms, centis = value.strip().split(".")
            hh, mm, ss = hms.split(":")
            return int(hh) * 3600 + int(mm) * 60 + int(ss) + (int(centis) / 100.0)
        except Exception:
            return 0.0

    def _scene_to_decision(
        self,
        index: int,
        scene: SceneBlueprint,
        duration: float,
    ) -> EditDecision:
        """Convert a scene's creative direction into FFmpeg-compatible parameters."""

        # --- Zoom from camera notes ---
        zoom_type = "none"
        zoom_intensity = 0.0

        camera = scene.camera_notes.lower()
        if "zoom in" in camera:
            zoom_type = "zoom_in"
            zoom_intensity = 0.05
        elif "zoom out" in camera:
            zoom_type = "zoom_out"
            zoom_intensity = 0.05
        elif "pan" in camera:
            zoom_type = "ken_burns"
            zoom_intensity = 0.03
        elif "dutch" in camera:
            zoom_type = "zoom_in"  # Simulated with slight zoom
            zoom_intensity = 0.03
        elif "close up" in camera:
            zoom_type = "zoom_in"
            zoom_intensity = 0.08

        # --- Fade from mood ---
        mood = scene.mood.lower()
        fade_in = 0.15
        fade_out = 0.15

        if mood in ("tense", "shock"):
            fade_in = 0.05   # Quick cuts for tension
            fade_out = 0.05
        elif mood in ("calm", "inspiring"):
            fade_in = 0.3    # Slow dissolves for calm
            fade_out = 0.3
        elif mood == "revelatory":
            fade_in = 0.2
            fade_out = 0.1   # Quick exit after reveal

        # --- Color grade from mood ---
        color_grades = {
            "tense": "dark",
            "shock": "cold",
            "calm": "warm",
            "inspiring": "warm",
            "revelatory": "default",
            "neutral": "default",
        }
        color_grade = color_grades.get(mood, "default")

        # --- Speed from mood ---
        speed = 1.0
        if mood == "tense":
            speed = 1.05   # Slightly faster for tension
        elif mood == "calm":
            speed = 0.95   # Slightly slower for calm

        # --- Transition ---
        transition = scene.transition_out.lower()
        if transition not in ("cut", "fade", "whip", "zoom_cut"):
            transition = "cut"

        return EditDecision(
            clip_index=index,
            duration=round(duration, 3),
            zoom_type=zoom_type,
            zoom_intensity=zoom_intensity,
            fade_in=fade_in,
            fade_out=fade_out,
            transition_out=transition,
            color_grade=color_grade,
            speed_factor=speed,
        )

    def _map_scenes_to_clips(
        self,
        scenes: list[SceneBlueprint],
        num_clips: int,
        audio_duration: float,
    ) -> list[tuple[SceneBlueprint, float]]:
        """Map N scenes to M clips with proportional duration allocation.

        If scenes > clips: merge short scenes into single clips.
        If clips > scenes: repeat last scene's settings for extra clips.
        """
        if not scenes or num_clips <= 0:
            return []

        # Calculate target duration per clip
        total_scene_dur = sum(s.duration_seconds for s in scenes) or 1

        # Intro offset (2s for intro image)
        usable_duration = max(4.0, audio_duration - 2.0)

        result = []

        if len(scenes) <= num_clips:
            # More clips than scenes: assign proportionally, pad with last scene
            for scene in scenes:
                ratio = scene.duration_seconds / total_scene_dur
                dur = round(ratio * usable_duration, 3)
                dur = max(1.5, min(5.0, dur))
                result.append((scene, dur))

            # Pad remaining clips with last scene's settings
            if len(result) < num_clips:
                last_scene = scenes[-1]
                remaining = usable_duration - sum(d for _, d in result)
                extra_count = num_clips - len(result)
                extra_dur = max(1.5, remaining / max(extra_count, 1))
                for _ in range(extra_count):
                    result.append((last_scene, round(extra_dur, 3)))
        else:
            # More scenes than clips: merge adjacent scenes
            chunk_size = len(scenes) / num_clips
            for i in range(num_clips):
                start_idx = int(i * chunk_size)
                end_idx = int((i + 1) * chunk_size)
                chunk = scenes[start_idx:end_idx]

                # Use the first scene's creative direction, sum durations
                primary = chunk[0]
                dur = sum(s.duration_seconds for s in chunk)
                ratio = dur / total_scene_dur
                actual_dur = round(ratio * usable_duration, 3)
                actual_dur = max(1.5, min(5.0, actual_dur))
                result.append((primary, actual_dur))

        # Normalize to fit audio duration
        total = sum(d for _, d in result)
        if total > 0:
            scale = usable_duration / total
            result = [(scene, round(dur * scale, 3)) for scene, dur in result]

        return result

    def _default_decisions(
        self,
        num_clips: int,
        audio_duration: float,
        nicho: NichoConfig,
    ) -> list[EditDecision]:
        """Fallback: V14-compatible even distribution."""
        usable = max(4.0, audio_duration - 2.0)
        dur_each = round(usable / max(num_clips, 1), 3)

        zoom = "zoom_in" if "cinemat" in nicho.tipo_cortes.lower() else "none"

        return [
            EditDecision(
                clip_index=i,
                duration=dur_each,
                zoom_type=zoom,
                zoom_intensity=0.05 if zoom != "none" else 0,
            )
            for i in range(num_clips)
        ]

    def _apply_reference_pacing(
        self,
        state: StoryState,
        index: int,
        decision: EditDecision,
    ) -> EditDecision:
        """Blend default duration with reference cadence when available."""
        avg_cut = float(getattr(state, "reference_avg_cut_seconds", 0.0) or 0.0)
        hook_sec = float(getattr(state, "reference_hook_seconds", 0.0) or 0.0)

        if avg_cut > 0:
            blended = (decision.duration * 0.65) + (avg_cut * 0.35)
            decision.duration = round(max(0.8, min(5.0, blended)), 3)

        if index == 0 and hook_sec > 0:
            decision.duration = round(max(0.8, min(decision.duration, hook_sec)), 3)

        return decision

    @staticmethod
    def _rescale_decisions(decisions: list[EditDecision], target_total: float) -> None:
        """Rescale decisions in place to keep total duration consistent."""
        if not decisions:
            return

        current = sum(max(0.1, d.duration) for d in decisions)
        if current <= 0:
            return

        scale = target_total / current
        for d in decisions:
            d.duration = round(max(0.8, d.duration * scale), 3)

    @staticmethod
    def decision_to_zoompan(decision: EditDecision) -> str:
        """Convert an EditDecision to FFmpeg zoompan filter string."""
        if decision.zoom_type == "none" or decision.zoom_intensity == 0:
            return ""

        intensity = decision.zoom_intensity
        dur_frames = int(decision.duration * 30)  # 30fps

        if decision.zoom_type == "zoom_in":
            return (
                f"zoompan=z='if(lte(on,{dur_frames}),"
                f"min(zoom+{intensity / dur_frames:.6f},1+{intensity}),zoom)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={dur_frames}:s=1080x1920:fps=30"
            )
        elif decision.zoom_type == "zoom_out":
            return (
                f"zoompan=z='if(lte(on,{dur_frames}),"
                f"max(zoom-{intensity / dur_frames:.6f},1.0),zoom)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={dur_frames}:s=1080x1920:fps=30"
            )
        elif decision.zoom_type == "ken_burns":
            # Gentle zoom + horizontal pan
            return (
                f"zoompan=z='if(lte(on,{dur_frames}),"
                f"min(zoom+{intensity / dur_frames:.6f},1+{intensity}),zoom)'"
                f":x='iw/2-(iw/zoom/2)+on*0.5':y='ih/2-(ih/zoom/2)'"
                f":d={dur_frames}:s=1080x1920:fps=30"
            )

        return ""

    @staticmethod
    def decision_to_color_grade(decision: EditDecision) -> str:
        """Convert color_grade to FFmpeg eq filter adjustments."""
        grades = {
            "warm": "eq=saturation=1.1:contrast=1.05:brightness=0.02:gamma=0.95",
            "cold": "eq=saturation=0.85:contrast=1.1:brightness=-0.01:gamma=1.0",
            "dark": "eq=saturation=0.8:contrast=1.15:brightness=-0.02:gamma=0.9",
            "default": "eq=saturation=0.90:contrast=1.06:brightness=0.012:gamma=0.97",
        }
        return grades.get(decision.color_grade, grades["default"])
