"""CompositionEngine — Orquestador principal de composición visual.

Integra todos los componentes sin duplicar su lógica:
  - OpenMontageAdapter → media profiles, slideshow risk, shot prompts
  - CinematicDirector → dirección automática basada en emoción
  - TimelineBuilder → timeline real con timing y transiciones
  - composition_master → selección de clips frescos (existente)
  - om_scene_evaluator → evaluación de calidad (existente)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from loguru import logger

from models.scene_plan_model import (
    CompositionResult,
    DirectionDecision,
    ScenePlan,
    SceneSpec,
    Timeline,
    VideoFormat,
    get_format_spec,
    platform_to_format,
)
from core.cinematic_director import CinematicDirector
from core.openmontage_adapter import OpenMontageAdapter
from core.timeline_builder import TimelineBuilder


class CompositionEngine:
    """Motor de composición que orquesta OpenMontage + dirección cinematográfica.

    Flujo:
      1. Validar con slideshow_risk (OM)
      2. Dirección cinematográfica automática
      3. Enriquecer prompts con shot_prompt_builder (OM)
      4. Construir timeline real
      5. Evaluar calidad con om_scene_evaluator
    """

    def __init__(self, om_root: Optional[Path] = None) -> None:
        self.adapter = OpenMontageAdapter(om_root)
        self.director = CinematicDirector()
        self.timeline_builder = TimelineBuilder()

    # ── Flujo principal ─────────────────────────────────────────

    def compose(
        self,
        plan: ScenePlan,
        audio_duration: float = 0.0,
        clip_paths: Optional[list[Path]] = None,
        image_paths: Optional[list[Path]] = None,
    ) -> CompositionResult:
        """Ejecuta el flujo completo de composición.

        Args:
            plan: ScenePlan estructurado con formato, escenas, estilo
            audio_duration: Duración del audio TTS (para ajustar timeline)
            clip_paths: Clips descargados (optional — se asignan a escenas)
            image_paths: Imágenes generadas (optional — fallback visual)

        Returns:
            CompositionResult con timeline, riesgo, calidad y direcciones
        """
        result = CompositionResult(
            format=plan.format.value,
            width=plan.width,
            height=plan.height,
        )

        if not plan.scenes:
            result.errors.append("Plan sin escenas")
            result.verdict = "fail"
            result.slideshow_score = 5.0
            return result

        # ── 1. Evaluar riesgo de slideshow ──
        scenes_dict = [s.to_dict() for s in plan.scenes]
        risk = self.adapter.score_slideshow_risk(scenes_dict)
        result.slideshow_risk = risk
        result.slideshow_score = risk.get("average", 0.0)
        result.verdict = risk.get("verdict", "unknown")

        if risk.get("verdict") == "fail":
            logger.warning(
                f"CompositionEngine: Slideshow risk FAIL (score={result.slideshow_score}). "
                f"Auto-fixing scenes."
            )
            plan.scenes = self._auto_fix_slideshow(plan.scenes)
            # Re-evaluate after fix
            scenes_dict = [s.to_dict() for s in plan.scenes]
            risk = self.adapter.score_slideshow_risk(scenes_dict)
            result.slideshow_risk = risk
            result.slideshow_score = risk.get("average", 0.0)
            result.verdict = risk.get("verdict", "unknown")
            result.warnings.append("Escenas auto-corregidas por riesgo de slideshow")

        # ── 2. Dirección cinematográfica automática ──
        directions = self.director.direct_sequence(plan.scenes)
        result.directions = directions

        # ── 3. Enriquecer prompts con shot_prompt_builder ──
        style_context = {
            "mood": plan.mood_global,
            "visual_language": {"aesthetic": plan.estilo_global},
        }
        for scene in plan.scenes:
            scene_dict = scene.to_dict()
            # Map SceneSpec fields to OM shot_language format
            scene_dict["description"] = scene.media or scene.texto
            scene_dict["shot_language"] = {
                "shot_size": scene.shot_type,
                "camera_movement": scene.motion,
            }
            enriched_prompt = self.adapter.build_shot_prompt(scene_dict, style_context)
            # Store enriched media description back
            if enriched_prompt and len(enriched_prompt) > len(scene.media or ""):
                scene.media = enriched_prompt

        # ── 4. Evaluar calidad con om_scene_evaluator existente ──
        try:
            from pipeline.om_scene_evaluator import evaluate_composition_plan
            eval_result = evaluate_composition_plan(scenes_dict)
            result.quality_evaluation = eval_result
            if eval_result.get("is_slideshow_risk"):
                result.warnings.append(
                    f"om_scene_evaluator: riesgo slideshow detectado "
                    f"(score={eval_result.get('score', 0):.2f})"
                )
        except ImportError:
            logger.debug("om_scene_evaluator not available, skipping quality eval")
        except Exception as exc:
            logger.debug(f"om_scene_evaluator failed: {exc}")

        # ── 5. Construir timeline real ──
        effective_duration = audio_duration or plan.total_duration
        timeline = self.timeline_builder.build(
            scenes=plan.scenes,
            directions=directions,
            audio_duration=effective_duration,
            format=plan.format.value,
            clip_paths=clip_paths,
            image_paths=image_paths,
        )
        result.timeline = timeline

        logger.info(
            f"CompositionEngine: composición completa — "
            f"{len(plan.scenes)} escenas, {plan.format.value} "
            f"({plan.width}x{plan.height}), "
            f"slideshow_risk={result.slideshow_score:.2f} [{result.verdict}]"
        )

        return result

    # ── Evaluación rápida (sin clips/timeline) ───────────────────

    def evaluate(self, plan: ScenePlan) -> CompositionResult:
        """Evalúa un plan sin generar timeline ni asignar clips.

        Útil para validar antes de descargar assets.
        """
        result = CompositionResult(
            format=plan.format.value,
            width=plan.width,
            height=plan.height,
        )

        if not plan.scenes:
            result.errors.append("Plan sin escenas")
            result.verdict = "fail"
            result.slideshow_score = 5.0
            return result

        scenes_dict = [s.to_dict() for s in plan.scenes]

        # Slideshow risk
        risk = self.adapter.score_slideshow_risk(scenes_dict)
        result.slideshow_risk = risk
        result.slideshow_score = risk.get("average", 0.0)
        result.verdict = risk.get("verdict", "unknown")

        # Direction preview
        directions = self.director.direct_sequence(plan.scenes)
        result.directions = directions

        return result

    # ── Auto-fix ─────────────────────────────────────────────────

    def _auto_fix_slideshow(self, scenes: list[SceneSpec]) -> list[SceneSpec]:
        """Auto-corrige escenas que causan efecto slideshow.

        Estrategias:
          - Variar shot_types repetidos
          - Agregar motion a escenas estáticas
          - Diversificar emociones
        """
        if not scenes:
            return scenes

        # Pool de variaciones
        shot_cycle = ["wide", "close-up", "medium", "detail", "aerial", "medium", "wide", "close-up"]
        motion_cycle = ["slow", "dynamic", "pan", "slow", "handheld", "timelapse", "slow", "dynamic"]
        emotion_cycle = ["dramatic", "neutral", "tense", "calm", "energetic", "mysterious"]

        fixed = []
        for i, scene in enumerate(scenes):
            new_scene = SceneSpec(
                texto=scene.texto,
                duracion=scene.duracion,
                media=scene.media,
                motion=scene.motion,
                estilo=scene.estilo,
                shot_type=scene.shot_type,
                emotion=scene.emotion,
                transition_in=scene.transition_in,
                transition_out=scene.transition_out,
                keywords=list(scene.keywords),
                scene_number=scene.scene_number,
            )

            # Fix: variar shot_type si hay 2+ previos iguales
            if i >= 2 and fixed[-1].shot_type == fixed[-2].shot_type == scene.shot_type:
                new_scene.shot_type = shot_cycle[i % len(shot_cycle)]

            # Fix: agregar motion a escenas estáticas
            if scene.motion in ("static", "", "unspecified"):
                new_scene.motion = motion_cycle[i % len(motion_cycle)]

            # Fix: variar emoción si hay 3+ neutrales seguidos
            if (
                i >= 2
                and scene.emotion == "neutral"
                and fixed[-1].emotion == "neutral"
                and fixed[-2].emotion == "neutral"
            ):
                new_scene.emotion = emotion_cycle[i % len(emotion_cycle)]

            fixed.append(new_scene)

        return fixed

    # ── Helpers para el pipeline v14/v15 ─────────────────────────

    @staticmethod
    def plan_from_composition_master_specs(
        scene_specs: list[Any],
        format: str = "vertical",
        estilo: str = "cinematic",
        mood: str = "professional",
        titulo: str = "",
        nicho: str = "",
    ) -> ScenePlan:
        """Convierte SceneClipSpec del composition_master existente a ScenePlan.

        Bridge de compatibilidad — permite usar el nuevo sistema con
        los datos que ya genera composition_master.py.
        """
        scenes = []
        for spec in scene_specs:
            # Handle both dataclass and dict
            if hasattr(spec, "clip_description"):
                scenes.append(SceneSpec(
                    texto=getattr(spec, "narration_snippet", ""),
                    duracion=getattr(spec, "duration", 4.0),
                    media=getattr(spec, "clip_description", ""),
                    motion=getattr(spec, "motion", "slow"),
                    estilo=estilo,
                    shot_type=getattr(spec, "shot_type", "medium"),
                    emotion=getattr(spec, "emotion", "neutral"),
                    keywords=list(getattr(spec, "keywords_primary", [])),
                    scene_number=getattr(spec, "scene_number", len(scenes) + 1),
                ))
            elif isinstance(spec, dict):
                scenes.append(SceneSpec(
                    texto=spec.get("narration_snippet", ""),
                    duracion=spec.get("duration", 4.0),
                    media=spec.get("clip_description", ""),
                    motion=spec.get("motion", "slow"),
                    estilo=estilo,
                    shot_type=spec.get("shot_type", "medium"),
                    emotion=spec.get("emotion", "neutral"),
                    keywords=spec.get("keywords_primary", []),
                    scene_number=spec.get("scene_number", len(scenes) + 1),
                ))

        return ScenePlan(
            format=VideoFormat(format),
            scenes=scenes,
            estilo_global=estilo,
            mood_global=mood,
            titulo=titulo,
            nicho=nicho,
        )
