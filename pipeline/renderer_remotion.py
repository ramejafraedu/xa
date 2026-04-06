"""Remotion Renderer — Premium video composition via Remotion.

Calls the Remotion CLI to render videos with programmatic
animations, transitions, and overlays. Falls back to FFmpeg.

MODULE CONTRACT:
  Input:  video project config (clips, audio, subtitles, metadata)
  Output: Path to rendered MP4

Provider hierarchy:
  1. Remotion (Node.js/React) → premium animations
  2. FFmpeg (existing renderer.py) → reliable fallback
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings


# Path to Remotion project (relative to video_factory/)
REMOTION_DIR = Path(__file__).resolve().parent.parent / "remotion-composer"


def is_remotion_available() -> bool:
    """Check if Remotion is installed and ready."""
    if not settings.use_remotion:
        return False

    if not REMOTION_DIR.exists():
        return False

    package_json = REMOTION_DIR / "package.json"
    if not package_json.exists():
        return False

    node_modules = REMOTION_DIR / "node_modules"
    if not node_modules.exists():
        logger.warning("Remotion node_modules missing. Run: cd remotion-composer && npm install")
        return False

    # Check Node.js is available
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False
        version = result.stdout.strip()
        logger.debug(f"Node.js: {version}")
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("Node.js not found in PATH")
        return False


def render_with_remotion(
    clips: list[Path],
    audio_path: Path,
    subtitles_path: Optional[Path],
    music_path: Optional[Path],
    output_path: Path,
    metadata: dict,
) -> bool:
    """Render video using Remotion.

    Args:
        clips: Paths to video clips / images.
        audio_path: TTS narration audio.
        subtitles_path: ASS subtitles file.
        music_path: Background music.
        output_path: Final video output.
        metadata: Video metadata (title, nicho, etc).

    Returns:
        True if render was successful.
    """
    if not is_remotion_available():
        logger.info("Remotion not available, falling back to FFmpeg")
        return False

    try:
        # Build input props for Remotion
        props = _build_remotion_props(
            clips, audio_path, subtitles_path, music_path, metadata
        )

        # Write props to temp file
        props_file = output_path.parent / f"remotion_props_{metadata.get('timestamp', 0)}.json"
        props_file.write_text(json.dumps(props, indent=2), encoding="utf-8")

        logger.info(f"🎥 Remotion: Rendering with {len(clips)} clips...")

        # Run Remotion render
        cmd = [
            "npx", "remotion", "render",
            "Main",  # composition ID
            str(output_path.resolve()),
            "--props", str(props_file.resolve()),
            "--codec", "h264",
            "--concurrency", "2",  # Limit CPU usage
        ]

        result = subprocess.run(
            cmd,
            cwd=str(REMOTION_DIR),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max
        )

        # Cleanup props file
        props_file.unlink(missing_ok=True)

        if result.returncode == 0 and output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ Remotion render complete: {output_path.name} ({size_mb:.1f}MB)")
            return True

        logger.warning(f"Remotion render failed (exit {result.returncode})")
        if result.stderr:
            logger.debug(f"Remotion stderr: {result.stderr[:500]}")
        return False

    except subprocess.TimeoutExpired:
        logger.error("Remotion render timed out (10 min)")
        return False
    except Exception as e:
        logger.warning(f"Remotion render error: {e}")
        return False


def render_with_fallback(
    clips: list[Path],
    audio_path: Path,
    subtitles_path: Optional[Path],
    music_path: Optional[Path],
    output_path: Path,
    metadata: dict,
) -> bool:
    """Try Remotion first, fall back to FFmpeg.

    This is the main entry point — replaces direct calls to renderer.
    """
    # Try Remotion
    if render_with_remotion(clips, audio_path, subtitles_path, music_path, output_path, metadata):
        return True

    # Fallback: FFmpeg
    logger.info("Falling back to FFmpeg renderer")
    try:
        from pipeline.renderer import render_video
        return render_video(clips, audio_path, subtitles_path, music_path, output_path)
    except Exception as e:
        logger.error(f"FFmpeg fallback also failed: {e}")
        return False


def _build_remotion_props(
    clips: list[Path],
    audio_path: Path,
    subtitles_path: Optional[Path],
    music_path: Optional[Path],
    metadata: dict,
) -> dict:
    """Build Remotion input props JSON."""
    return {
        "clips": [str(p.resolve().as_posix()) for p in clips if p.exists()],
        "audioPath": str(audio_path.resolve().as_posix()),
        "subtitlesPath": str(subtitles_path.resolve().as_posix()) if subtitles_path and subtitles_path.exists() else None,
        "musicPath": str(music_path.resolve().as_posix()) if music_path and music_path.exists() else None,
        "title": metadata.get("titulo", ""),
        "nicho": metadata.get("nicho", ""),
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "durationInSeconds": metadata.get("duration", 60),
        "transitionStyle": metadata.get("transition", "crossfade"),
    }


def setup_remotion_project() -> bool:
    """Initialize the Remotion project if not already set up.

    Run this once to create the remotion-composer project.
    """
    if REMOTION_DIR.exists() and (REMOTION_DIR / "node_modules").exists():
        logger.debug("Remotion project already initialized")
        return True

    try:
        logger.info("🔧 Setting up Remotion project...")

        # Create the directory
        REMOTION_DIR.mkdir(parents=True, exist_ok=True)

        # Initialize with npx
        result = subprocess.run(
            ["npx", "-y", "create-video@latest", str(REMOTION_DIR), "--template", "blank"],
            capture_output=True, text=True, timeout=120,
        )

        if result.returncode == 0:
            logger.info("✅ Remotion project initialized")
            return True

        logger.warning(f"Remotion setup failed: {result.stderr[:300]}")
        return False

    except Exception as e:
        logger.error(f"Remotion setup error: {e}")
        return False
