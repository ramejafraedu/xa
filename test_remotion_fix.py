import os
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger

# Add the project root to sys.path
sys.path.append(os.getcwd())

from pipeline.renderer_remotion import render_with_remotion


def _ensure_test_tone_mp3(path: Path) -> None:
    """Create a short sine MP3 with ffmpeg if missing (CI / fresh VMs)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError(
            "workspace/temp/test_tone.mp3 is missing and ffmpeg is not in PATH"
        )
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-nostats",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=5",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "6",
            str(path),
        ],
        check=True,
    )


def test():
    clips = []  # No clips needed for title scene
    audio_path = Path("workspace/temp/test_tone.mp3")
    _ensure_test_tone_mp3(audio_path)
    subtitles_path = None
    music_path = None
    output_path = Path("workspace/temp/test_remotion_final.mp4")
    metadata = {"timestamp": 123456, "titulo": "Test Success", "composition_id": "CinematicRenderer"}
    
    # We'll use a timeline_payload to trigger the logic
    timeline_payload = {
        "scenes": [
            {
                "id": "scene_1",
                "kind": "title",
                "text": "Remotion Fix Verified!",
                "startSeconds": 0,
                "durationSeconds": 5
            }
        ],
        "soundtrack": {
            "src": str(audio_path.resolve()),
            "volume": 1.0,
            "fadeInSeconds": 0.5,
            "fadeOutSeconds": 0.5
        }
    }
    
    logger.info("Starting Remotion render test...")
    success = render_with_remotion(
        clips=clips,
        audio_path=audio_path,
        subtitles_path=subtitles_path,
        music_path=music_path,
        output_path=output_path,
        metadata=metadata,
        timeline_payload=timeline_payload
    )
    
    if success:
        logger.info(f"Test PASSED! Output at {output_path}")
    else:
        logger.error("Test FAILED!")

if __name__ == "__main__":
    test()
