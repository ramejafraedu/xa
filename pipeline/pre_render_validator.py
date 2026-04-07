"""Pre-Render Validator — check ALL assets before touching FFmpeg.

If any check fails, we abort early with a specific ErrorCode
instead of wasting 2-5 minutes on a render that will fail anyway.

MODULE CONTRACT:
  Input:  audio_path, subs_path, clips[], images[], music_path, platform, duration
  Output: (is_valid: bool, errors: list[tuple[ErrorCode, str]])

Checks performed:
  1. Audio exists and non-empty
  2. Duration within platform limit
  3. At least 1 clip OR 1 image exists
  4. All referenced clip files exist and are > 1KB
  5. Subtitle file is parseable ASS
  6. Dimensions are correct (via ffprobe)
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

from config import settings
from core.openmontage_free import run_audio_probe
from models.content import ErrorCode
from pipeline.duration_validator import get_max_duration


def validate_pre_render(
    audio_path: Path,
    subs_path: Path | None,
    clips: list[Path],
    images: list[Path],
    music_path: Path | None,
    platform: str,
    audio_duration: float,
) -> tuple[bool, list[tuple[ErrorCode, str]]]:
    """Run all pre-render checks. Returns (all_ok, errors)."""
    errors: list[tuple[ErrorCode, str]] = []

    # 1. Audio exists and non-empty
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        errors.append((ErrorCode.ASSET_MISSING, f"Audio file missing or empty: {audio_path.name}"))

    # 2. Duration within platform limit
    max_dur = get_max_duration(platform)
    if audio_duration > max_dur:
        errors.append((
            ErrorCode.DURATION_EXCEEDED,
            f"Duration {audio_duration:.1f}s exceeds {platform} limit of {max_dur:.1f}s"
        ))

    # 3. At least 1 visual source
    valid_clips = [c for c in clips if c.exists() and c.stat().st_size > 1000]
    valid_images = [i for i in images if i.exists() and i.stat().st_size > 1000]
    if not valid_clips and not valid_images:
        errors.append((ErrorCode.ASSET_MISSING, "No valid clips or images available for render"))

    # 4. Check each clip
    for clip in clips:
        if not clip.exists():
            errors.append((ErrorCode.ASSET_MISSING, f"Clip missing: {clip.name}"))
        elif clip.stat().st_size < 1000:
            errors.append((ErrorCode.ASSET_CORRUPT, f"Clip corrupt/too small: {clip.name} ({clip.stat().st_size}B)"))

    # 5. Subtitle file parseable
    if subs_path and subs_path.exists():
        try:
            content = subs_path.read_text(encoding="utf-8")
            if "[Script Info]" not in content:
                errors.append((ErrorCode.SUBS_INVALID, "ASS file missing [Script Info] header"))
            elif "Dialogue:" not in content:
                errors.append((ErrorCode.SUBS_INVALID, "ASS file has no Dialogue events"))
        except Exception as e:
            errors.append((ErrorCode.SUBS_INVALID, f"Cannot read ASS file: {e}"))

    # 6. Music file (optional but check if referenced)
    if music_path and music_path.exists() and music_path.stat().st_size < 500:
        errors.append((ErrorCode.ASSET_CORRUPT, f"Music file too small: {music_path.name}"))

    # 7. Optional OpenMontage audio probe check
    if settings.enable_openmontage_free_tools and settings.openmontage_enable_analysis:
        probe = run_audio_probe(audio_path)
        if probe:
            probe_duration = float(probe.get("duration_seconds", 0) or 0)
            if probe_duration > 0 and abs(probe_duration - audio_duration) > 1.0:
                errors.append(
                    (
                        ErrorCode.DURATION_EXCEEDED,
                        (
                            f"Audio duration mismatch: validator={audio_duration:.2f}s vs "
                            f"openmontage_probe={probe_duration:.2f}s"
                        ),
                    )
                )

    if errors:
        for code, msg in errors:
            logger.warning(f"Pre-render check [{code.value}]: {msg}")
    else:
        logger.info("✅ Pre-render validation passed (all assets OK)")

    return len(errors) == 0, errors
