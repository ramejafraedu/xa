import os
import subprocess
from pathlib import Path
from loguru import logger

def apply_post_processing(video_path: Path, temp_dir: Path) -> Path:
    """Apply film grain, vignette and color grading to achieve an Old Money / Vintage aesthetic."""
    if not video_path.exists():
        return video_path
        
    output_path = temp_dir / f"post_processed_{video_path.name}"
    logger.info(f"Applying post-processing to {video_path}")
    
    # FFmpeg filter complex:
    # 1. eq=contrast=1.1:brightness=-0.02:saturation=0.85 (Color grading)
    # 2. noise=alls=5:allf=t+u (Film grain)
    # 3. vignette=PI/4 (Vignette)
    filter_complex = "eq=contrast=1.1:brightness=-0.02:saturation=0.85,noise=alls=5:allf=t+u,vignette=PI/4"
    
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(video_path),
        "-vf", filter_complex,
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "copy",
        str(output_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Replace original with processed if successful
        if output_path.exists() and output_path.stat().st_size > 0:
            import shutil
            shutil.copy2(str(output_path), str(video_path))
            output_path.unlink()
            logger.info("Post-processing applied successfully")
            return video_path
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg post-processing failed: {e.stderr}")
    except Exception as e:
        logger.error(f"Error during post-processing: {e}")
        
    return video_path
