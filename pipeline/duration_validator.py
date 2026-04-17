"""Duration Validator — enforce platform min/max duration limits.

V16.2 PRO: When ``settings.enforce_duration_hard_limit`` is enabled (default),
the pipeline caps every non long-form niche at
``settings.max_video_duration`` (default 90s) regardless of platform. Target
narration lives in the 62-90s band so output always crosses TikTok Creator
Rewards' 60-second monetisation floor.

Legacy behaviour (long-form) is preserved when the hard limit is disabled.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from config import app_config, settings


def get_max_duration(platform: str) -> float:
    """Get maximum allowed duration for a platform.

    If multiple targets are encoded in the platform label (e.g. "tiktok_reels"),
    apply the strictest cap among those targets. Under V16 PRO the global
    ``max_video_duration`` cap is applied on top of platform limits.
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

    platform_cap = min(targets) if targets else shorts_cap

    # V16 PRO global hard cap for high-retention short-form output.
    if getattr(settings, "enforce_duration_hard_limit", False):
        global_cap = float(getattr(settings, "max_video_duration", 60))
        return min(platform_cap, global_cap)

    return platform_cap


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
    hard_limit = bool(getattr(settings, "enforce_duration_hard_limit", False))
    auto_trim = bool(getattr(settings, "auto_trim_if_over", True))

    # Some story-first niches intentionally allow long-form narration — unless
    # the operator explicitly turned on the V16 PRO hard limit.
    if (niche_slug or "").strip().lower() == "historias_reddit" and not hard_limit:
        logger.info("Skipping duration cap for niche historias_reddit")
        return audio_duration, False

    max_dur = get_max_duration(platform)
    if max_duration_override > 0:
        max_dur = max_duration_override
    if hard_limit:
        max_dur = min(max_dur, float(getattr(settings, "max_video_duration", 60)))

    # Creator-Rewards floor: warn loudly when narration is below the
    # monetisable minute. The pipeline cannot re-generate audio here safely,
    # but emitting a WARNING surfaces it in logs + manifest so the job is
    # flagged and the next run tightens the word budget automatically.
    min_narration = float(getattr(settings, "min_narration_seconds", 0.0))
    if hard_limit and min_narration > 0 and audio_duration < min_narration:
        logger.warning(
            f"Narration {audio_duration:.1f}s < {min_narration:.1f}s floor "
            f"({platform}). Video may not qualify for TikTok Creator Rewards."
        )

    if audio_duration <= max_dur:
        logger.debug(f"Duration OK: {audio_duration:.1f}s <= {max_dur:.1f}s ({platform})")
        return audio_duration, False

    if not auto_trim:
        logger.warning(
            f"Duration exceeds {platform} limit: {audio_duration:.1f}s > {max_dur:.1f}s "
            "but AUTO_TRIM_IF_OVER=false — keeping original."
        )
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
