"""Video Factory V15 — Editor Agent.

Makes intelligent editing decisions based on scene mood,
camera notes, and transitions from the ScenePlanner.

Replaces the hardcoded FFmpeg parameters in V14 with
scene-aware dynamic editing.

MODULE CONTRACT:
  Input:  StoryState.scenes + raw clips/images
  Output: Editing decisions (durations, transitions, effects per clip)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from loguru import logger

from core.state import SceneBlueprint, StoryState
from models.config_models import NichoConfig


class EditDecision:
    """Editing instructions for a single clip/scene."""

    def __init__(
        self,
        clip_index: int,
        duration: float,
        zoom_type: str = "none",      # "zoom_in", "zoom_out", "ken_burns", "none"
        zoom_intensity: float = 0.0,
        fade_in: float = 0.15,
        fade_out: float = 0.15,
        transition_out: str = "cut",   # "cut", "fade", "whip"
        color_grade: str = "default",  # "warm", "cold", "dark", "default"
        speed_factor: float = 1.0,     # 1.0 = normal, <1 = slow-mo, >1 = timelapse
    ):
        self.clip_index = clip_index
        self.duration = duration
        self.zoom_type = zoom_type
        self.zoom_intensity = zoom_intensity
        self.fade_in = fade_in
        self.fade_out = fade_out
        self.transition_out = transition_out
        self.color_grade = color_grade
        self.speed_factor = speed_factor


class EditorAgent:
    """Generate intelligent editing decisions from scene plan."""

    def run(
        self,
        state: StoryState,
        nicho: NichoConfig,
        num_clips: int,
        audio_duration: float,
    ) -> list[EditDecision]:
        """Create per-clip editing decisions based on scene mood/cameras.

        Args:
            state: StoryState with approved scenes.
            nicho: Niche config.
            num_clips: Actual number of available clips.
            audio_duration: Total audio duration.

        Returns:
            List of EditDecision, one per clip.
        """
        t0 = time.time()
        decisions = []

        scenes = state.scenes
        if num_clips <= 0:
            # No clip decisions to make when render will be image-only.
            logger.warning("EditorAgent: no clips available, skipping clip edit decisions")
            return []

        if not scenes:
            # Fallback: even distribution with default settings
            return self._default_decisions(num_clips, audio_duration, nicho)

        # Map scenes to actual clips (scenes may != clips)
        clip_scenes = self._map_scenes_to_clips(scenes, num_clips, audio_duration)

        for i, (scene, duration) in enumerate(clip_scenes):
            decision = self._scene_to_decision(i, scene, duration)
            decisions.append(decision)

        elapsed = round(time.time() - t0, 2)
        logger.info(
            f"🎞️ Edit decisions: {len(decisions)} clips, "
            f"total {sum(d.duration for d in decisions):.1f}s ({elapsed}s)"
        )
        return decisions

    def _scene_to_decision(
        self,
        index: int,
        scene: SceneBlueprint,
        duration: float,
    ) -> EditDecision:
        """Convert a scene's creative direction into FFmpeg-compatible parameters."""

        # --- Zoom from camera notes ---
        zoom_type = "none"
        zoom_intensity = 0.0

        camera = scene.camera_notes.lower()
        if "zoom in" in camera:
            zoom_type = "zoom_in"
            zoom_intensity = 0.05
        elif "zoom out" in camera:
            zoom_type = "zoom_out"
            zoom_intensity = 0.05
        elif "pan" in camera:
            zoom_type = "ken_burns"
            zoom_intensity = 0.03
        elif "dutch" in camera:
            zoom_type = "zoom_in"  # Simulated with slight zoom
            zoom_intensity = 0.03
        elif "close up" in camera:
            zoom_type = "zoom_in"
            zoom_intensity = 0.08

        # --- Fade from mood ---
        mood = scene.mood.lower()
        fade_in = 0.15
        fade_out = 0.15

        if mood in ("tense", "shock"):
            fade_in = 0.05   # Quick cuts for tension
            fade_out = 0.05
        elif mood in ("calm", "inspiring"):
            fade_in = 0.3    # Slow dissolves for calm
            fade_out = 0.3
        elif mood == "revelatory":
            fade_in = 0.2
            fade_out = 0.1   # Quick exit after reveal

        # --- Color grade from mood ---
        color_grades = {
            "tense": "dark",
            "shock": "cold",
            "calm": "warm",
            "inspiring": "warm",
            "revelatory": "default",
            "neutral": "default",
        }
        color_grade = color_grades.get(mood, "default")

        # --- Speed from mood ---
        speed = 1.0
        if mood == "tense":
            speed = 1.05   # Slightly faster for tension
        elif mood == "calm":
            speed = 0.95   # Slightly slower for calm

        # --- Transition ---
        transition = scene.transition_out.lower()
        if transition not in ("cut", "fade", "whip", "zoom_cut"):
            transition = "cut"

        return EditDecision(
            clip_index=index,
            duration=round(duration, 3),
            zoom_type=zoom_type,
            zoom_intensity=zoom_intensity,
            fade_in=fade_in,
            fade_out=fade_out,
            transition_out=transition,
            color_grade=color_grade,
            speed_factor=speed,
        )

    def _map_scenes_to_clips(
        self,
        scenes: list[SceneBlueprint],
        num_clips: int,
        audio_duration: float,
    ) -> list[tuple[SceneBlueprint, float]]:
        """Map N scenes to M clips with proportional duration allocation.

        If scenes > clips: merge short scenes into single clips.
        If clips > scenes: repeat last scene's settings for extra clips.
        """
        if not scenes or num_clips <= 0:
            return []

        # Calculate target duration per clip
        total_scene_dur = sum(s.duration_seconds for s in scenes) or 1

        # Intro offset (2s for intro image)
        usable_duration = max(4.0, audio_duration - 2.0)

        result = []

        if len(scenes) <= num_clips:
            # More clips than scenes: assign proportionally, pad with last scene
            for scene in scenes:
                ratio = scene.duration_seconds / total_scene_dur
                dur = round(ratio * usable_duration, 3)
                dur = max(1.5, min(5.0, dur))
                result.append((scene, dur))

            # Pad remaining clips with last scene's settings
            if len(result) < num_clips:
                last_scene = scenes[-1]
                remaining = usable_duration - sum(d for _, d in result)
                extra_count = num_clips - len(result)
                extra_dur = max(1.5, remaining / max(extra_count, 1))
                for _ in range(extra_count):
                    result.append((last_scene, round(extra_dur, 3)))
        else:
            # More scenes than clips: merge adjacent scenes
            chunk_size = len(scenes) / num_clips
            for i in range(num_clips):
                start_idx = int(i * chunk_size)
                end_idx = int((i + 1) * chunk_size)
                chunk = scenes[start_idx:end_idx]

                # Use the first scene's creative direction, sum durations
                primary = chunk[0]
                dur = sum(s.duration_seconds for s in chunk)
                ratio = dur / total_scene_dur
                actual_dur = round(ratio * usable_duration, 3)
                actual_dur = max(1.5, min(5.0, actual_dur))
                result.append((primary, actual_dur))

        # Normalize to fit audio duration
        total = sum(d for _, d in result)
        if total > 0:
            scale = usable_duration / total
            result = [(scene, round(dur * scale, 3)) for scene, dur in result]

        return result

    def _default_decisions(
        self,
        num_clips: int,
        audio_duration: float,
        nicho: NichoConfig,
    ) -> list[EditDecision]:
        """Fallback: V14-compatible even distribution."""
        usable = max(4.0, audio_duration - 2.0)
        dur_each = round(usable / max(num_clips, 1), 3)

        zoom = "zoom_in" if "cinemat" in nicho.tipo_cortes.lower() else "none"

        return [
            EditDecision(
                clip_index=i,
                duration=dur_each,
                zoom_type=zoom,
                zoom_intensity=0.05 if zoom != "none" else 0,
            )
            for i in range(num_clips)
        ]

    @staticmethod
    def decision_to_zoompan(decision: EditDecision) -> str:
        """Convert an EditDecision to FFmpeg zoompan filter string."""
        if decision.zoom_type == "none" or decision.zoom_intensity == 0:
            return ""

        intensity = decision.zoom_intensity
        dur_frames = int(decision.duration * 30)  # 30fps

        if decision.zoom_type == "zoom_in":
            return (
                f"zoompan=z='if(lte(on,{dur_frames}),"
                f"min(zoom+{intensity / dur_frames:.6f},1+{intensity}),zoom)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={dur_frames}:s=1080x1920:fps=30"
            )
        elif decision.zoom_type == "zoom_out":
            return (
                f"zoompan=z='if(lte(on,{dur_frames}),"
                f"max(zoom-{intensity / dur_frames:.6f},1.0),zoom)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={dur_frames}:s=1080x1920:fps=30"
            )
        elif decision.zoom_type == "ken_burns":
            # Gentle zoom + horizontal pan
            return (
                f"zoompan=z='if(lte(on,{dur_frames}),"
                f"min(zoom+{intensity / dur_frames:.6f},1+{intensity}),zoom)'"
                f":x='iw/2-(iw/zoom/2)+on*0.5':y='ih/2-(ih/zoom/2)'"
                f":d={dur_frames}:s=1080x1920:fps=30"
            )

        return ""

    @staticmethod
    def decision_to_color_grade(decision: EditDecision) -> str:
        """Convert color_grade to FFmpeg eq filter adjustments."""
        grades = {
            "warm": "eq=saturation=1.1:contrast=1.05:brightness=0.02:gamma=0.95",
            "cold": "eq=saturation=0.85:contrast=1.1:brightness=-0.01:gamma=1.0",
            "dark": "eq=saturation=0.8:contrast=1.15:brightness=-0.02:gamma=0.9",
            "default": "eq=saturation=0.90:contrast=1.06:brightness=0.012:gamma=0.97",
        }
        return grades.get(decision.color_grade, grades["default"])
