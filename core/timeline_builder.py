"""TimelineBuilder — Construye la timeline real del video.

Sin timeline real, el sistema concatena clips sin ritmo.
Este módulo convierte escenas + dirección en una timeline ejecutable
con timing, transiciones y capas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from loguru import logger

from models.scene_plan_model import (
    DirectionDecision,
    SceneSpec,
    Timeline,
    TimelineCut,
    VideoFormat,
    get_format_spec,
)
from core.cinematic_director import CinematicDirector


class TimelineBuilder:
    """Construye timeline ejecutable para el renderer.

    Responsabilidades:
      1. Ajustar duraciones al audio real
      2. Asignar transiciones entre escenas
      3. Generar lista de cortes con efectos
    """

    def __init__(self) -> None:
        self._director = CinematicDirector()

    def build(
        self,
        scenes: list[SceneSpec],
        directions: list[DirectionDecision],
        audio_duration: float,
        format: str = "vertical",
        clip_paths: Optional[list[Path]] = None,
        image_paths: Optional[list[Path]] = None,
    ) -> Timeline:
        """Construye la timeline completa.

        Args:
            scenes: Escenas del plan
            directions: Decisiones de dirección por escena
            audio_duration: Duración total del audio (TTS)
            format: Formato de video (vertical/horizontal/square)
            clip_paths: Clips descargados (1 por escena o menos)
            image_paths: Imágenes generadas (fallbacks y mid-images)

        Returns:
            Timeline ejecutable con cortes, transiciones y efectos
        """
        spec = get_format_spec(format)
        width = spec["w"]
        height = spec["h"]

        # 1. Ajustar duraciones al audio real
        adjusted_scenes = self._adjust_to_audio(scenes, audio_duration)

        # 2. Asignar clips/imágenes a escenas
        cuts = self._assign_media(
            adjusted_scenes, directions, clip_paths or [], image_paths or []
        )

        # 3. Calcular tiempos absolutos en la timeline
        cuts = self._compute_absolute_times(cuts)

        timeline = Timeline(
            total_duration=audio_duration,
            format=format,
            width=width,
            height=height,
            cuts=cuts,
        )

        logger.info(
            f"TimelineBuilder: {timeline.cut_count} cortes, "
            f"{timeline.total_duration:.1f}s, {format} ({width}x{height})"
        )
        return timeline

    def _adjust_to_audio(
        self, scenes: list[SceneSpec], audio_duration: float
    ) -> list[SceneSpec]:
        """Ajusta duraciones de escenas para que sumen = audio_duration.

        Preserva las proporciones relativas de cada escena.
        """
        if not scenes:
            return []

        total_planned = sum(s.duracion for s in scenes) or 1.0
        ratio = audio_duration / total_planned

        adjusted = []
        accumulated = 0.0
        for i, scene in enumerate(scenes):
            new_scene = SceneSpec(
                texto=scene.texto,
                duracion=round(scene.duracion * ratio, 3),
                media=scene.media,
                motion=scene.motion,
                estilo=scene.estilo,
                shot_type=scene.shot_type,
                emotion=scene.emotion,
                transition_in=scene.transition_in,
                transition_out=scene.transition_out,
                keywords=list(scene.keywords),
                scene_number=scene.scene_number or (i + 1),
            )
            # Duración mínima: 1.0s
            new_scene.duracion = max(1.0, new_scene.duracion)
            accumulated += new_scene.duracion
            adjusted.append(new_scene)

        # Ajustar última escena para que total = audio_duration exacto
        if adjusted:
            leftover = audio_duration - sum(s.duracion for s in adjusted[:-1])
            adjusted[-1].duracion = max(1.0, round(leftover, 3))

        return adjusted

    def _assign_media(
        self,
        scenes: list[SceneSpec],
        directions: list[DirectionDecision],
        clip_paths: list[Path],
        image_paths: list[Path],
    ) -> list[TimelineCut]:
        """Asigna clips e imágenes a cada escena, generando TimelineCuts."""
        cuts: list[TimelineCut] = []

        for i, scene in enumerate(scenes):
            direction = directions[i] if i < len(directions) else DirectionDecision()

            # Determinar qué media usar
            clip_path = clip_paths[i] if i < len(clip_paths) else None
            image_path = image_paths[i] if i < len(image_paths) else None

            # Validar que el archivo existe
            if clip_path and not clip_path.exists():
                clip_path = None
            if image_path and not image_path.exists():
                image_path = None

            is_image = clip_path is None and image_path is not None

            cut = TimelineCut(
                clip_path=clip_path,
                image_path=image_path,
                duration=scene.duracion,
                transition_in=direction.transition,
                transition_out="cut",  # Se actualiza en paso siguiente
                transition_duration=direction.transition_duration,
                zoompan_intensity=direction.zoompan_intensity,
                zoompan_direction=direction.zoompan_direction,
                color_grade=direction.color_grade,
                motion_type=direction.motion,
                fade_in=direction.fade_in,
                fade_out=direction.fade_out,
                scene_number=scene.scene_number or (i + 1),
                is_image=is_image,
            )
            cuts.append(cut)

        # Asignar transition_out basado en el siguiente corte
        for i in range(len(cuts) - 1):
            cuts[i].transition_out = cuts[i + 1].transition_in

        return cuts

    def _compute_absolute_times(
        self, cuts: list[TimelineCut]
    ) -> list[TimelineCut]:
        """Calcula tiempos absolutos (start_time, end_time) para cada corte."""
        current_time = 0.0
        for cut in cuts:
            cut.start_time = round(current_time, 3)
            cut.end_time = round(current_time + cut.duration, 3)
            current_time = cut.end_time
        return cuts

    def build_from_legacy(
        self,
        clips: list[Path],
        images: list[Path],
        audio_duration: float,
        format: str = "vertical",
        velocidad: str = "rapido",
        num_clips: int = 8,
        duraciones_clips: Optional[list[float]] = None,
    ) -> Timeline:
        """Construye timeline desde parámetros legacy (backward compat).

        Convierte los parámetros del renderer actual a SceneSpecs
        y genera la timeline con dirección automática.
        """
        # Mapear velocidad a emoción
        velocidad_to_emotion = {
            "ultra_rapido": "energetic",
            "rapido": "tense",
            "moderado": "neutral",
            "lento": "calm",
        }
        emotion = velocidad_to_emotion.get(velocidad, "neutral")

        # Crear escenas a partir de clips
        all_media = clips + images
        total_media = len(all_media) or 1
        scenes = []
        for i in range(total_media):
            dur = (
                duraciones_clips[i]
                if duraciones_clips and i < len(duraciones_clips)
                else audio_duration / total_media
            )
            scenes.append(
                SceneSpec(
                    texto="",
                    duracion=dur,
                    motion="dynamic" if velocidad in ("rapido", "ultra_rapido") else "slow",
                    shot_type="medium",
                    emotion=emotion,
                    scene_number=i + 1,
                )
            )

        directions = self._director.direct_sequence(scenes)

        return self.build(
            scenes=scenes,
            directions=directions,
            audio_duration=audio_duration,
            format=format,
            clip_paths=clips,
            image_paths=images,
        )
