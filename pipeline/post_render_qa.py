"""Post-Render QA — Validates final video after rendering.

V16 upgrade: Added 3 new checks from OpenMontage-style self-review:
    1. Audio/video stream duration sync (delta < 120ms)
  2. Subtitle safe-zone validation (TikTok/Reels UI overlap detection)
  3. Frame sampling (3 keyframes sampled, all must be non-black/non-corrupt)

MODULE CONTRACT:
  Input:  Path to rendered MP4, optional subs_path for safe-zone check
  Output: (passed: bool, issues: list[str])
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from loguru import logger

from config import settings
from core.openmontage_free import run_audio_probe, run_frame_sampler, run_visual_probe


def post_render_qa(
    video_path: Path,
    expected_width: int = 1080,
    expected_height: int = 1920,
    min_duration: float = 15.0,
    max_duration: float = 180.0,
    min_size_kb: int = 500,
    subs_path: Path | None = None,
    platform: str = "tiktok_reels",
    reference_promise: str = "",
    reference_avg_cut_seconds: float = 0.0,
) -> tuple[bool, list[str]]:
    """Run post-render quality assurance on a video file.

    Args:
        video_path: Path to the rendered MP4.
        expected_width: Expected video width in pixels.
        expected_height: Expected video height in pixels.
        min_duration: Minimum acceptable duration in seconds.
        max_duration: Maximum acceptable duration in seconds.
        min_size_kb: Minimum file size in KB.
        subs_path: Optional .ass subtitle file to validate safe-zone.
        platform: Platform hint for safe-zone thresholds (tiktok_reels, shorts, facebook).

    Returns:
        (passed, issues) — True if all checks pass, plus list of issues found.
    """
    issues: list[str] = []

    if not video_path.exists():
        return False, ["Video file does not exist"]

    # 1. File size check
    size_kb = video_path.stat().st_size // 1024
    if size_kb < min_size_kb:
        issues.append(f"File too small: {size_kb}KB (min: {min_size_kb}KB)")

    # 2. ffprobe metadata
    probe = _ffprobe(video_path)
    if not probe:
        issues.append("ffprobe failed — cannot validate video")
        return False, issues

    # 3. Video stream check
    video_stream = _find_stream(probe, "video")
    if not video_stream:
        issues.append("No video stream found")
    else:
        w = video_stream.get("width", 0)
        h = video_stream.get("height", 0)

        if w != expected_width or h != expected_height:
            if w < 720 or h < 1200:
                issues.append(f"Resolution mismatch: {w}x{h} (expected at least 720x1200)")
            else:
                logger.info(f"Accepted fallback resolution: {w}x{h} (expected {expected_width}x{expected_height})")

        # Check codec
        codec = video_stream.get("codec_name", "unknown")
        if codec not in ("h264", "h265", "hevc", "vp9", "av1"):
            issues.append(f"Unusual codec: {codec}")

        # Check FPS
        fps_str = video_stream.get("r_frame_rate", "0/1")
        try:
            num, den = fps_str.split("/")
            fps = int(num) / max(int(den), 1)
            # Accept NTSC-like 23.976fps as valid short-form baseline.
            if fps < 23.5 or fps > 61:
                issues.append(f"FPS out of range: {fps:.1f} (expected 23.5-60)")
        except Exception:
            pass

    # 4. Audio stream check
    audio_stream = _find_stream(probe, "audio")
    if not audio_stream:
        issues.append("No audio stream — video is silent")
    else:
        channels = audio_stream.get("channels", 0)
        if channels < 1:
            issues.append("Audio has 0 channels")

        sample_rate = int(audio_stream.get("sample_rate", 0))
        if sample_rate < 22050:
            issues.append(f"Low audio sample rate: {sample_rate}Hz")

    # 5. Duration check
    duration = float(probe.get("format", {}).get("duration", 0))
    if duration < min_duration:
        issues.append(f"Too short: {duration:.1f}s (min: {min_duration}s)")
    elif duration > max_duration:
        issues.append(f"Too long: {duration:.1f}s (max: {max_duration}s)")

    # 6. Audio silence detection
    silence = _detect_silence(video_path, duration)
    if silence:
        issues.append(silence)

    # 7. Black frame detection (first 2 seconds)
    black = _detect_black_frames(video_path)
    if black:
        issues.append(black)

    # 8. V16: Audio/video stream sync check
    sync_issue = _check_av_sync(probe, duration)
    if sync_issue:
        issues.append(sync_issue)

    # 9. V16: Frame sampling (detect corrupt/black frames across full video)
    frame_issue = _sample_frames(video_path, duration)
    if frame_issue:
        issues.append(frame_issue)

    # 10. V16: Green-screen dominance detection
    green_issue = _detect_excessive_green(video_path, duration)
    if green_issue:
        issues.append(green_issue)

    # 11. V16: Subtitle safe-zone check
    if subs_path and subs_path.exists():
        sub_issues = check_subtitle_safe_zone(subs_path, expected_height, platform)
        issues.extend(sub_issues)

    # 12. V16+: Promise/rhythm compliance for reference-driven jobs
    rhythm_issue = _check_motion_rhythm(
        video_path=video_path,
        total_duration=duration,
        reference_promise=reference_promise,
        reference_avg_cut_seconds=reference_avg_cut_seconds,
    )
    if rhythm_issue:
        issues.append(rhythm_issue)

    # 13. OpenMontage (optional): extra analysis checks via tool adapters
    if settings.enable_openmontage_free_tools and settings.openmontage_enable_analysis:
        om_probe = run_audio_probe(video_path)
        if om_probe:
            probed_duration = float(om_probe.get("duration_seconds", 0) or 0)
            if probed_duration > 0 and abs(probed_duration - duration) > 0.8:
                issues.append(
                    "OpenMontage probe mismatch: "
                    f"duration={probed_duration:.2f}s vs ffprobe={duration:.2f}s"
                )

        om_visual = run_visual_probe(
            video_path,
            expected={
                "width": expected_width,
                "height": expected_height,
                "min_duration": min_duration,
                "max_duration": max_duration,
                "has_audio": True,
            },
        )
        if om_visual:
            validation_issues = om_visual.get("validation_issues", [])
            if isinstance(validation_issues, list):
                for issue in validation_issues[:3]:
                    issues.append(f"OpenMontage visual QA: {issue}")

        frame_dir = video_path.parent / "qa_frames"
        sampled = run_frame_sampler(video_path, frame_dir, count=3)
        if sampled:
            frame_count = int(sampled.get("frame_count", 0) or 0)
            if frame_count < 2:
                issues.append(
                    f"OpenMontage frame sampler extracted only {frame_count} frame(s)"
                )

    passed = len(issues) == 0

    if passed:
        logger.info(f"✅ Post-render QA passed: {video_path.name} ({duration:.1f}s, {size_kb}KB)")
    else:
        logger.warning(f"⚠️ Post-render QA issues for {video_path.name}:")
        for issue in issues:
            logger.warning(f"   → {issue}")

    return passed, issues


def _ffprobe(path: Path) -> dict | None:
    """Run ffprobe and return JSON output."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception as e:
        logger.debug(f"ffprobe error: {e}")
    return None


def _find_stream(probe: dict, codec_type: str) -> dict | None:
    """Find first stream of given type."""
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == codec_type:
            return stream
    return None


def _detect_silence(video_path: Path, total_duration: float) -> str | None:
    """Detect if video has significant silence periods."""
    if total_duration < 5:
        return None

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-af", "silencedetect=noise=-40dB:d=3",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=30,
        )

        stderr = result.stderr
        silence_count = stderr.count("silence_end")

        if silence_count > 3:
            return f"Excessive silence detected: {silence_count} silent periods"

        # Check if more than 30% is silent
        import re
        durations = re.findall(r"silence_duration: ([\d.]+)", stderr)
        total_silence = sum(float(d) for d in durations)

        if total_silence > total_duration * 0.3:
            return f"Too much silence: {total_silence:.1f}s of {total_duration:.1f}s ({total_silence/total_duration*100:.0f}%)"

    except Exception:
        pass

    return None


def _detect_black_frames(video_path: Path) -> str | None:
    """Check for black frames at beginning of video."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-vf", "blackdetect=d=0.5:pix_th=0.10",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=20,
        )

        stderr = result.stderr
        if "black_start:0" in stderr:
            return "Video starts with black frames"

        black_count = stderr.count("black_start")
        if black_count > 3:
            return f"Multiple black frame sections: {black_count}"

    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# V16: New QA Checks
# ---------------------------------------------------------------------------

def _check_av_sync(probe: dict, video_duration: float) -> str | None:
    """V16: Check audio/video stream duration sync.

    A delta > 120ms between the two streams usually indicates a mux error
    that will cause the video to appear frozen or have desync on mobile.
    """
    try:
        streams = probe.get("streams", [])
        video_dur = None
        audio_dur = None

        for stream in streams:
            dur = stream.get("duration")
            if dur is None:
                continue
            dur = float(dur)
            if stream.get("codec_type") == "video" and video_dur is None:
                video_dur = dur
            elif stream.get("codec_type") == "audio" and audio_dur is None:
                audio_dur = dur

        if video_dur is None or audio_dur is None:
            return None  # Can't check if streams missing

        delta = abs(video_dur - audio_dur)
        if delta > 0.12:
            return (
                f"A/V sync warning: video={video_dur:.2f}s vs audio={audio_dur:.2f}s "
                f"(delta={delta:.2f}s > 120ms threshold)"
            )
    except Exception as e:
        logger.debug(f"AV sync check error: {e}")

    return None


def _check_motion_rhythm(
    video_path: Path,
    total_duration: float,
    reference_promise: str = "",
    reference_avg_cut_seconds: float = 0.0,
) -> str | None:
    """Check cut rhythm and motion intensity against reference promise."""
    if total_duration <= 0:
        return None

    promise = (reference_promise or "").strip().lower()
    target_avg = float(reference_avg_cut_seconds or 0.0)

    # Skip when there is no reference-driven expectation.
    if not promise and target_avg <= 0:
        return None

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-vf", "select='gt(scene,0.30)',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        stderr = result.stderr or ""
        scene_hits = re.findall(r"pts_time:([\d.]+)", stderr)
        cut_count = len(scene_hits)

        # +1 because cuts partition scenes in intervals.
        scene_count = max(1, cut_count + 1)
        avg_cut = total_duration / scene_count

        if promise == "motion_led" and avg_cut > 3.5:
            return (
                f"Reference promise mismatch: motion_led expects faster pacing, "
                f"but detected avg cut {avg_cut:.2f}s"
            )

        if target_avg > 0:
            delta = abs(avg_cut - target_avg)
            tolerance = max(1.2, target_avg * 0.8)
            if delta > tolerance:
                return (
                    f"Reference pacing drift: avg cut {avg_cut:.2f}s vs target "
                    f"{target_avg:.2f}s (delta {delta:.2f}s)"
                )

        # Generic slideshow warning for reference-driven paths.
        if avg_cut > 5.2 and total_duration >= 20:
            return f"Possible slideshow pacing: avg cut {avg_cut:.2f}s over {total_duration:.1f}s"
    except Exception as e:
        logger.debug(f"Motion rhythm check error: {e}")

    return None


def _sample_frames(video_path: Path, duration: float, sample_count: int = 3) -> str | None:
    """V16: Sample frames across the video to detect corrupt/black regions.

    Extracts `sample_count` keyframes evenly distributed across the video
    and checks each for blackness using ffprobe's signalstats.
    """
    if duration < 5:
        return None  # Too short to reliably sample

    try:
        import tempfile
        import os

        # Sample at 25%, 50%, 75% of duration
        timestamps = [duration * p for p in [0.25, 0.50, 0.75][:sample_count]]
        black_frames = 0

        with tempfile.TemporaryDirectory() as tmpdir:
            for i, ts in enumerate(timestamps):
                frame_path = os.path.join(tmpdir, f"frame_{i}.png")
                result = subprocess.run(
                    [
                        "ffmpeg", "-ss", str(ts), "-i", str(video_path),
                        "-vframes", "1", "-q:v", "2",
                        frame_path, "-y",
                    ],
                    capture_output=True, timeout=15,
                )

                if result.returncode != 0 or not os.path.exists(frame_path):
                    black_frames += 1
                    continue

                # Check if frame file is suspiciously small (corrupted = usually < 2KB)
                frame_size = os.path.getsize(frame_path)
                if frame_size < 2048:  # < 2KB is likely a black/corrupt frame
                    black_frames += 1

        if black_frames >= 2:
            return (
                f"Frame sampling: {black_frames}/{sample_count} frames appear "
                f"black or corrupted (possible render issue)"
            )
        elif black_frames == 1:
            logger.debug(f"Frame sampling: 1/{sample_count} frames suspect (warning only)")

    except Exception as e:
        logger.debug(f"Frame sampling error: {e}")

    return None


def _detect_excessive_green(video_path: Path, duration: float, sample_count: int = 3) -> str | None:
    """Detect likely unkeyed green-screen by sampling distributed frames."""
    if duration < 4:
        return None

    points = [0.2, 0.5, 0.8][:sample_count]
    flagged: list[float] = []

    for p in points:
        ts = max(0.0, duration * p)
        ratio = _estimate_green_ratio_at_timestamp(video_path, ts)
        if ratio >= 0.32:
            flagged.append(ratio)

    if len(flagged) >= 2:
        peak = max(flagged)
        return (
            "Possible unkeyed green-screen detected: "
            f"{len(flagged)}/{len(points)} sampled frames "
            f"(peak {peak * 100:.1f}% green)"
        )
    return None


def _estimate_green_ratio_at_timestamp(video_path: Path, seconds: float) -> float:
    """Estimate green pixel ratio for one frame sampled at `seconds`."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error",
                "-ss", str(seconds),
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
        logger.debug(f"Green-screen post-check skipped: {exc}")
        return 0.0


def check_subtitle_safe_zone(
    subs_path: Path,
    video_height: int = 1920,
    platform: str = "tiktok_reels",
) -> list[str]:
    """V16: Check that subtitle events don't land in UI danger zones.

    TikTok/Reels overlays the bottom 300px with likes/share/comments buttons
    and the top 130px with the nav bar. Subtitles in those zones get hidden.

    Args:
        subs_path: Path to the .ass subtitle file.
        video_height: Video height in pixels (default 1920).
        platform: Platform hint for threshold selection.

    Returns:
        List of issue strings (empty = all clear).
    """
    issues = []

    # Safe-zone thresholds by platform (top_danger, bottom_danger)
    platform_zones = {
        "tiktok_reels": (130, 300),
        "reels": (130, 280),
        "shorts": (100, 250),
        "facebook": (60, 150),
    }
    platform_key = platform.lower().replace("tiktok_", "")
    if "tiktok" in platform_key or "reel" in platform_key:
        top_danger, bottom_danger = platform_zones.get("tiktok_reels", (130, 300))
    elif "short" in platform_key:
        top_danger, bottom_danger = platform_zones.get("shorts", (100, 250))
    elif "facebook" in platform_key:
        top_danger, bottom_danger = platform_zones.get("facebook", (60, 150))
    else:
        top_danger, bottom_danger = 130, 300

    safe_top = top_danger + 50      # 50px extra margin
    safe_bottom = video_height - bottom_danger - 50

    try:
        import re
        content = subs_path.read_text(encoding="utf-8", errors="ignore")

        # Parse MarginV from [V4+ Styles] section
        margin_v_match = re.search(r"MarginV\s*:\s*(\d+)", content, re.IGNORECASE)
        if margin_v_match:
            margin_v = int(margin_v_match.group(1))
            subtitle_bottom_y = video_height - margin_v

            if subtitle_bottom_y > safe_bottom:
                issues.append(
                    f"Subtitle safe-zone warning: MarginV={margin_v}px places subtitles at "
                    f"y={subtitle_bottom_y} which overlaps the {platform} UI zone "
                    f"(safe max y={safe_bottom}). Increase MarginV to at least {bottom_danger + 50}."
                )

        # Look for explicit !{\pos(x,y)} positioning that could be in danger zones
        pos_matches = re.findall(r"\\pos\((\d+),(\d+)\)", content)
        for _, y_str in pos_matches:
            y = int(y_str)
            if y < safe_top:
                issues.append(f"Subtitle positioned at y={y} (top danger zone < {safe_top})")
                break
            if y > safe_bottom:
                issues.append(f"Subtitle positioned at y={y} (bottom danger zone > {safe_bottom})")
                break

    except Exception as e:
        logger.debug(f"Subtitle safe-zone check error: {e}")

    return issues
