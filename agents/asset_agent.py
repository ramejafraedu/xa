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
from typing import Optional

from loguru import logger

from config import settings
from core.state import StoryState
from models.config_models import NichoConfig


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
        results = {
            "stock_clips": [],
            "images": [],
            "music_path": None,
            "sfx_paths": [],
        }

        # --- 1. Stock fallback for uncovered scenes ---
        clips_needed = nicho.num_clips
        if clips_needed > 0:
            results["stock_clips"] = self._fetch_stock_clips(
                state, nicho, clips_needed
            )

        # --- 2. Images (with visual direction from StoryState) ---
        results["images"] = self._generate_images(
            state, nicho, timestamp, temp_dir
        )

        # --- 3. Music (mood from script) ---
        results["music_path"] = self._fetch_music(
            state, nicho, timestamp, temp_dir
        )

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
    ) -> list[str]:
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

            urls = fetch_stock_videos(keywords, count)
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
    ) -> list[Path]:
        """Generate images with visual direction from StoryState."""
        try:
            from pipeline.image_gen import generate_images

            raw_content = getattr(state, "_raw_content", {})

            # Build enhanced prompt using scene context
            prompt_base = raw_content.get("prompt_imagen", "")
            if not prompt_base:
                prompt_base = state.visual_direction or nicho.nombre

            # Add StoryState visual coherence
            if state.color_palette:
                prompt_base += f", {state.color_palette}"

            ab_variant = raw_content.get("_ab_variant", "A")

            images = generate_images(
                prompt_base,
                nicho.direccion_visual,
                ab_variant,
                timestamp,
                temp_dir,
            )
            return images

        except Exception as e:
            logger.warning(f"Image generation failed: {e}")
            return []

    def _fetch_music(
        self,
        state: StoryState,
        nicho: NichoConfig,
        timestamp: int,
        temp_dir: Path,
    ) -> Optional[Path]:
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
            from pipeline.music_ai import fetch_music_with_fallback
            fetch_music_with_fallback(
                mood, music_path,
                duration_seconds=state.total_duration() or 30,
                nicho=nicho.slug,
            )
            if music_path.exists() and music_path.stat().st_size > 1000:
                return music_path
        except Exception:
            pass

        try:
            from pipeline.music import fetch_music
            fetch_music(mood, music_path)
            if music_path.exists() and music_path.stat().st_size > 1000:
                return music_path
        except Exception as e:
            logger.debug(f"Music fetch failed: {e}")

        return None
