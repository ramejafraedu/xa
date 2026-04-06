"""Veo Clips — AI-generated video clips via Gemini Veo 3.1.

Generates custom 8-second clips based on script keywords,
with Pexels stock footage as fallback.

MODULE CONTRACT:
  Input:  list of prompts (from keywords/scenes) + config
  Output: list[Path] of downloaded MP4 clips

Provider hierarchy:
  1. Veo 3.1 (Gemini API free tier) → custom AI clips
  2. Pexels (existing) → stock fallback
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings


def generate_veo_clips(
    prompts: list[str],
    timestamp: int,
    temp_dir: Path,
    aspect_ratio: str = "9:16",
    max_clips: int = 8,
) -> list[Path]:
    """Generate video clips using Veo 3.1.

    Args:
        prompts: Scene descriptions for each clip.
        timestamp: Job timestamp for unique filenames.
        temp_dir: Directory to save clips.
        aspect_ratio: "9:16" (portrait) or "16:9" (landscape).
        max_clips: Maximum clips to generate.

    Returns:
        List of paths to generated MP4 clips.
    """
    if not settings.gemini_api_key:
        logger.warning("No GEMINI_API_KEY — skipping Veo, using stock fallback")
        return []

    if not settings.use_veo_clips:
        logger.debug("Veo clips disabled in config")
        return []

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-genai not installed. Run: pip install google-genai")
        return []

    clips: list[Path] = []
    prompts_to_use = prompts[:max_clips]

    for i, prompt in enumerate(prompts_to_use):
        clip_path = temp_dir / f"veo_clip_{timestamp}_{i}.mp4"

        # Idempotency: skip if already exists
        if clip_path.exists() and clip_path.stat().st_size > 10000:
            logger.debug(f"Veo clip {i} already exists, skipping")
            clips.append(clip_path)
            continue

        try:
            logger.info(f"🎬 Veo 3.1: Generating clip {i+1}/{len(prompts_to_use)}")

            # Rotate keys to avoid rate limits
            api_key = settings.next_gemini_key()
            client = genai.Client(api_key=api_key)

            # Enhance prompt for better video quality
            enhanced = _enhance_prompt(prompt)

            operation = client.models.generate_videos(
                model="veo-3.1-generate-preview",
                prompt=enhanced,
                config=types.GenerateVideosConfig(
                    aspect_ratio=aspect_ratio,
                ),
            )

            # Poll until ready (max 5 min per clip)
            max_wait = 300
            waited = 0
            while not operation.done and waited < max_wait:
                time.sleep(10)
                waited += 10
                operation = client.operations.get(operation)
                if waited % 30 == 0:
                    logger.debug(f"  Veo clip {i+1}: waiting... ({waited}s)")

            if not operation.done:
                logger.warning(f"Veo clip {i+1} timed out after {max_wait}s")
                continue

            # Download the clip
            if operation.response and operation.response.generated_videos:
                video = operation.response.generated_videos[0]
                client.files.download(file=video.video)
                video.video.save(str(clip_path))

                if clip_path.exists() and clip_path.stat().st_size > 5000:
                    logger.info(f"✅ Veo clip {i+1} saved: {clip_path.name} ({clip_path.stat().st_size // 1024}KB)")
                    clips.append(clip_path)
                else:
                    logger.warning(f"Veo clip {i+1} file too small or missing")
            else:
                logger.warning(f"Veo clip {i+1}: no video in response")

        except Exception as e:
            logger.warning(f"Veo clip {i+1} failed: {e}")
            continue

    logger.info(f"Veo generated {len(clips)}/{len(prompts_to_use)} clips")
    return clips


def generate_scene_prompts(
    keywords: list[str],
    nicho_nombre: str,
    num_clips: int = 8,
    tono: str = "profesional",
) -> list[str]:
    """Generate scene descriptions from keywords for Veo.

    Converts simple keywords like ['finanzas', 'inversión']
    into cinematic scene prompts for Veo 3.1.
    """
    base_styles = {
        "finanzas": "modern office lighting, corporate atmosphere, clean composition",
        "historia": "cinematic historical recreation, dramatic lighting, period-accurate details",
        "curiosidades": "vibrant colors, macro close-ups, amazing natural phenomena",
        "salud": "clean medical environment, warm natural lighting, wellness atmosphere",
        "recetas": "overhead food photography, steam rising, appetizing presentation, wooden table",
    }

    style = base_styles.get(nicho_nombre.lower(), "professional cinematic lighting, 4K quality")

    prompts = []
    for kw in keywords[:num_clips]:
        prompt = (
            f"Professional vertical video (9:16). {kw}. "
            f"Style: {style}. "
            f"Smooth camera movement, cinematic depth of field, "
            f"high production value, no text overlays, no watermarks."
        )
        prompts.append(prompt)

    # Pad with generic prompts if not enough keywords
    while len(prompts) < num_clips:
        prompts.append(
            f"Professional vertical video about {nicho_nombre}. "
            f"{style}. Smooth camera movement, cinematic quality."
        )

    return prompts


def _enhance_prompt(prompt: str) -> str:
    """Enhance a basic prompt for better Veo output."""
    enhancements = [
        "Professional vertical video",
        "cinematic lighting",
        "smooth camera movement",
        "high production value",
        "no text overlays",
    ]

    # Don't add if already present
    additions = []
    for e in enhancements:
        if e.lower() not in prompt.lower():
            additions.append(e)

    if additions:
        prompt = prompt.rstrip(". ") + ". " + ", ".join(additions) + "."

    return prompt
