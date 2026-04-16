"""Duration Validator — enforce platform max duration limits.

TikTok: 60m, Reels: 3m, Shorts: 3m, Facebook: 120s.
If the generated audio exceeds the limit, trim it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from config import app_config


def get_max_duration(platform: str) -> float:
    """Get maximum allowed duration for a platform.

    If multiple targets are encoded in the platform label (e.g. "tiktok_reels"),
    apply the strictest cap among those targets.
    """
    p = (platform or "").lower()

    # Policy floors avoid accidental regressions to legacy 60s caps.
    tiktok_cap = max(float(app_config.max_duration_tiktok), 3600.0)
    reels_cap = max(float(app_config.max_duration_reels), 180.0)
    shorts_cap = max(float(app_config.max_duration_shorts), 180.0)
    facebook_cap = max(float(app_config.max_duration_facebook), 120.0)

    targets: list[float] = []
    if "tiktok" in p:
        targets.append(tiktok_cap)
    if "reel" in p or "instagram" in p:
        targets.append(reels_cap)
    if "short" in p or "youtube" in p:
        targets.append(shorts_cap)
    if "facebook" in p:
        targets.append(facebook_cap)

    if targets:
        return min(targets)
    return shorts_cap


def validate_duration(
    audio_duration: float,
    platform: str,
    audio_path: Path,
    niche_slug: str = "",
    max_duration_override: float = 0.0,
) -> tuple[float, bool]:
    """Validate and potentially trim audio to platform max.

    Returns (final_duration, was_trimmed).
    """
    # Some story-first niches intentionally allow long-form narration.
    if (niche_slug or "").strip().lower() == "historias_reddit":
        logger.info("Skipping duration cap for niche historias_reddit")
        return audio_duration, False

    max_dur = get_max_duration(platform)
    if max_duration_override > 0:
        max_dur = max_duration_override

    if audio_duration <= max_dur:
        logger.debug(f"Duration OK: {audio_duration:.1f}s <= {max_dur:.1f}s ({platform})")
        return audio_duration, False

    logger.warning(
        f"Duration exceeds {platform} limit: {audio_duration:.1f}s > {max_dur:.1f}s. Trimming."
    )

    # Trim audio
    trimmed = audio_path.with_name(f"trimmed_{audio_path.name}")
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_path),
            "-t", str(max_dur),
            "-c", "copy",
            str(trimmed),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and trimmed.exists() and trimmed.stat().st_size > 1000:
            # Replace original
            audio_path.unlink()
            trimmed.rename(audio_path)
            logger.info(f"Audio trimmed to {max_dur:.1f}s")
            return max_dur, True
        else:
            trimmed.unlink(missing_ok=True)
    except Exception as e:
        logger.error(f"Trim failed: {e}")
        trimmed.unlink(missing_ok=True)

    # If trim failed, use original but warn
    return audio_duration, False
