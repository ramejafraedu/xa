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


def render_video_with_fallback(
    clips: list[Path],
    audio_path: Path,
    subs_path: Optional[Path],
    music_path: Optional[Path],
    images: list[Path],
    timestamp: int,
    temp_dir: Path,
    output_dir: Path,
    nicho_slug: str,
    gancho: str,
    titulo: str,
    duracion_audio: float,
    velocidad: str = "rapido",
    num_clips: int = 8,
    duraciones_clips: Optional[list[float]] = None,
    render_fixes: Optional[dict] = None,
) -> tuple[Optional[Path], Optional[Path], str]:
    """Try Remotion first (when enabled), then fallback to FFmpeg renderer API."""
    from pipeline.renderer import render_video

    if settings.use_remotion:
        output_dir.mkdir(parents=True, exist_ok=True)
        final_name = f"{_slugify(nicho_slug, 24)}_{_slugify(gancho, 38)}_{timestamp}.mp4"
        remotion_output = output_dir / final_name

        # If there are no stock clips, Remotion can still work with generated images.
        remotion_inputs = clips if clips else images
        remotion_meta = {
            "timestamp": timestamp,
            "titulo": titulo,
            "nicho": nicho_slug,
            "duration": duracion_audio,
            "transition": "crossfade",
        }

        if render_with_remotion(
            remotion_inputs,
            audio_path,
            subs_path,
            music_path,
            remotion_output,
            remotion_meta,
        ):
            thumb = _extract_thumbnail(remotion_output, temp_dir, timestamp)
            return remotion_output, thumb, ""

        logger.info("Remotion unavailable/failed, using FFmpeg fallback")

    return render_video(
        clips=clips,
        audio_path=audio_path,
        subs_path=subs_path,
        music_path=music_path,
        images=images,
        timestamp=timestamp,
        temp_dir=temp_dir,
        output_dir=output_dir,
        nicho_slug=nicho_slug,
        gancho=gancho,
        titulo=titulo,
        duracion_audio=duracion_audio,
        velocidad=velocidad,
        num_clips=num_clips,
        duraciones_clips=duraciones_clips,
        render_fixes=render_fixes,
    )


def render_with_fallback(
    clips: list[Path],
    audio_path: Path,
    subtitles_path: Optional[Path],
    music_path: Optional[Path],
    output_path: Path,
    metadata: dict,
) -> bool:
    """Backward-compatible wrapper kept for older callers."""
    return render_with_remotion(clips, audio_path, subtitles_path, music_path, output_path, metadata)


def _extract_thumbnail(video_path: Path, temp_dir: Path, timestamp: int) -> Optional[Path]:
    """Extract a thumbnail frame from a rendered Remotion video."""
    thumb = temp_dir / f"thumb_{timestamp}.jpg"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "00:00:02",
                "-i", str(video_path.as_posix()),
                "-frames:v", "1",
                str(thumb.as_posix()),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and thumb.exists() and thumb.stat().st_size > 100:
            return thumb
    except Exception:
        pass
    return None


def _slugify(text: str, max_len: int = 32) -> str:
    """Convert text to a filename-safe slug."""
    import re

    slug = (text or "").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = slug.strip("_")
    return slug[:max_len] or "video"


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
