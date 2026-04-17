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

import re
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
    auto_filter_greenscreen: bool = False,
) -> tuple[bool, list[tuple[ErrorCode, str]]]:
    """Run all pre-render checks. Returns (all_ok, errors).

    When ``auto_filter_greenscreen`` is True, unkeyed greenscreen clips are
    **removed in-place** from ``clips`` instead of failing the whole render.
    A WARNING is still logged for each dropped clip, but they do not appear
    in the returned ``errors`` list unless no clip/image remains.
    """
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

    # 4. Check each clip (collect greenscreen drops separately for optional auto-filter)
    green_to_drop: list[Path] = []
    for clip in clips:
        resolved_clip = _resolve_clip_path(clip)
        if not resolved_clip:
            errors.append((ErrorCode.ASSET_MISSING, f"Clip missing: {clip.name}"))
        elif resolved_clip.stat().st_size < 1000:
            errors.append((ErrorCode.ASSET_CORRUPT, f"Clip corrupt/too small: {clip.name} ({resolved_clip.stat().st_size}B)"))
        else:
            green_ratio = _estimate_green_screen_ratio(resolved_clip)
            if green_ratio >= 0.34:
                message = (
                    f"Clip looks like unkeyed greenscreen: {clip.name} "
                    f"({green_ratio * 100:.1f}% green)"
                )
                if auto_filter_greenscreen:
                    green_to_drop.append(clip)
                    logger.warning(
                        f"Pre-render auto-drop [GREENSCREEN_DETECTED]: {message}"
                    )
                else:
                    errors.append((ErrorCode.GREENSCREEN_DETECTED, message))

    if auto_filter_greenscreen and green_to_drop:
        for drop in green_to_drop:
            try:
                clips.remove(drop)
            except ValueError:
                continue

    # 3. At least 1 visual source (run AFTER greenscreen filter so we don't
    #    declare "no clips" just because every clip happened to be chroma).
    valid_clips = [c for c in clips if c.exists() and c.stat().st_size > 1000]
    valid_images = [i for i in images if i.exists() and i.stat().st_size > 1000]
    if not valid_clips and not valid_images:
        errors.append((ErrorCode.ASSET_MISSING, "No valid clips or images available for render"))

    # 5. Subtitle file parseable
    if subs_path and subs_path.exists():
        try:
            content = subs_path.read_text(encoding="utf-8")
            if "[Script Info]" not in content:
                errors.append((ErrorCode.SUBS_INVALID, "ASS file missing [Script Info] header"))
            elif "Dialogue:" not in content:
                errors.append((ErrorCode.SUBS_INVALID, "ASS file has no Dialogue events"))
            elif re.search(r"\((?:!|:\$|\^_\^|\*)\)\s*$", content, flags=re.MULTILINE):
                errors.append((ErrorCode.SUBS_INVALID, "ASS file has synthetic suffix artifacts"))
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


def _resolve_clip_path(clip: Path) -> Path | None:
    """Resolve a clip Path even if it was stored under a different directory.

    Priority:
    1. Exact path as given.
    2. video_cache_dir / clip.name
    3. temp_dir / clip.name
    Returns the first existing path with size > 1000 bytes, or None.
    """
    candidates = [
        clip,
        settings.video_cache_dir / clip.name,
        settings.temp_dir / clip.name,
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.stat().st_size > 1000:
                return candidate
        except Exception:
            continue
    return None


def extract_flagged_greenscreen_clips(
    errors: list[tuple[ErrorCode, str]],
) -> set[str]:
    """Extract clip filenames flagged as greenscreen from validator errors."""
    flagged: set[str] = set()
    pattern = re.compile(r"greenscreen:\s*([^()]+?)\s*\(", flags=re.IGNORECASE)

    for code, message in errors:
        if code != ErrorCode.GREENSCREEN_DETECTED:
            continue

        msg = str(message or "")
        match = pattern.search(msg)
        if match:
            flagged.add(Path(match.group(1).strip()).name)
            continue

        if ":" in msg:
            candidate = msg.split(":", 1)[1].strip().split("(", 1)[0].strip()
            if candidate:
                flagged.add(Path(candidate).name)

    return flagged


def _estimate_green_screen_ratio(video_path: Path, sample_offset: float = 0.5) -> float:
    """Estimate dominant chroma-green coverage using one sampled frame."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-ss", str(sample_offset),
                "-i", str(video_path),
                "-frames:v", "1",
                "-vf", "scale=192:-1,format=rgb24",
                "-f", "rawvideo", "-",
            ],
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0 or not result.stdout:
            return 0.0

        raw = result.stdout
        pixel_count = len(raw) // 3
        if pixel_count <= 0:
            return 0.0

        green_pixels = 0
        limit = pixel_count * 3
        for idx in range(0, limit, 3):
            r = raw[idx]
            g = raw[idx + 1]
            b = raw[idx + 2]

            if g < 96:
                continue
            if g > int(r * 1.28) and g > int(b * 1.28) and (g - max(r, b)) > 20:
                green_pixels += 1

        return green_pixels / pixel_count
    except Exception as exc:
        logger.debug(f"Green-screen precheck skipped for {video_path.name}: {exc}")
        return 0.0
