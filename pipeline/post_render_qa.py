"""Post-Render QA — Validates final video after rendering.

Runs ffprobe + analysis to ensure the rendered video is not corrupted,
has expected resolution, audio levels are OK, etc.

MODULE CONTRACT:
  Input:  Path to rendered MP4
  Output: (passed: bool, issues: list[str])
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loguru import logger


def post_render_qa(
    video_path: Path,
    expected_width: int = 1080,
    expected_height: int = 1920,
    min_duration: float = 15.0,
    max_duration: float = 120.0,
    min_size_kb: int = 500,
) -> tuple[bool, list[str]]:
    """Run post-render quality assurance on a video file.

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
            issues.append(f"Resolution mismatch: {w}x{h} (expected {expected_width}x{expected_height})")

        # Check codec
        codec = video_stream.get("codec_name", "unknown")
        if codec not in ("h264", "h265", "hevc", "vp9", "av1"):
            issues.append(f"Unusual codec: {codec}")

        # Check FPS
        fps_str = video_stream.get("r_frame_rate", "0/1")
        try:
            num, den = fps_str.split("/")
            fps = int(num) / max(int(den), 1)
            if fps < 24 or fps > 61:
                issues.append(f"FPS out of range: {fps:.1f} (expected 24-60)")
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
