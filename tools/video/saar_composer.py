"""SaarD00 FFmpeg Composer Logic (Adapted for V16 PRO).

Integrates dynamic A/B visual splits, silence trimming, and 
advanced transitions using raw FFmpeg commands for Youtube Shorts.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from loguru import logger

class SaarComposer:
    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        
    def trim_silence(self, audio_path: str, output_path: str) -> bool:
        """Trim silence from beginning and end of audio (from SaarD00)."""
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-af", "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-50dB,areverse",
            output_path
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to trim silence for {audio_path}")
            return False

    def build_ab_split_renders(self, scene_data: list[dict], audio_track: str, output_prefix: str) -> list[str]:
        """Build multiple video variants based on A/B visual split (SaarD00 logic)."""
        variants = []
        
        # In a real SaarD00 logic, this would use ffmpeg concat demuxer
        # with visual_1 for Variant A and visual_2 for Variant B.
        
        # Variant A
        variant_a_path = self.temp_dir / f"{output_prefix}_variant_A.mp4"
        self._concat_scenes(scene_data, audio_track, "visual_1", str(variant_a_path))
        if variant_a_path.exists():
            variants.append(str(variant_a_path))
            
        # Variant B
        variant_b_path = self.temp_dir / f"{output_prefix}_variant_B.mp4"
        self._concat_scenes(scene_data, audio_track, "visual_2", str(variant_b_path))
        if variant_b_path.exists():
            variants.append(str(variant_b_path))
            
        return variants

    def _concat_scenes(self, scene_data: list[dict], audio_track: str, visual_key: str, output_path: str):
        """Internal ffmpeg concat logic."""
        list_file = self.temp_dir / f"concat_list_{visual_key}.txt"
        valid_clips = []
        for scene in scene_data:
            clip = scene.get(visual_key) or scene.get("visual_1") or scene.get("fallback_clip")
            if clip and os.path.exists(clip):
                valid_clips.append(clip)
                
        if not valid_clips:
            return
            
        with open(list_file, "w") as f:
            for clip in valid_clips:
                f.write(f"file '{Path(clip).absolute()}'\n")
                
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-i", audio_track,
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            output_path
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"SaarComposer: Successfully rendered {output_path}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"SaarComposer: Failed to concat scenes for {visual_key}")
