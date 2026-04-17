"""Video Factory V15/V16 PRO — Scene Agent.

Takes the approved script and breaks it into SceneBlueprints.
Each scene gets:
  - Narration text
  - Visual prompt (with character/style consistency)
  - Mood + camera notes
  - Duration + transitions

V16 PRO: enforces Shorts rhythm — hook scene <=2s, max 10-12 scenes,
duration per scene between ``short_scene_min_seconds`` and
``short_scene_max_seconds`` (default 2.5-5.0s), and total time capped
by ``settings.max_video_duration``.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from loguru import logger

from config import settings
from core.state import SceneBlueprint, StoryState
from models.config_models import NichoConfig
from services.llm_router import call_llm_primary_gemini


class SceneAgent:
    """Split approved script into cohesive, directorial scene plan."""

    def run(
        self,
        state: StoryState,
        nicho: NichoConfig,
        correction_notes: str = "",
    ) -> list[SceneBlueprint]:
        """Generate scene plan from the approved script.

        Args:
            state: Current StoryState (must have script_full set).
            nicho: Niche config.
            correction_notes: If regenerating, what to fix.

        Returns:
            List of SceneBlueprints (also updates state.scenes).
        """
        t0 = time.time()

        if not state.script_full:
            logger.error("SceneAgent: no script in StoryState")
            return []

        raw_scenes = self._generate_scenes(state, nicho, correction_notes)

        if not raw_scenes:
            # Fallback: simple split by sentences
            raw_scenes = self._fallback_split(state)

        # V16 PRO: enforce Shorts pacing, hook <=2s, and hard cap on scene count.
        raw_scenes = self._enforce_short_form_rhythm(raw_scenes)

        # Enrich with visual prompts
        scenes = self._enrich_visuals(raw_scenes, state, nicho)

        # Update state
        state.scenes = scenes

        elapsed = round(time.time() - t0, 2)
        total_dur = sum(s.duration_seconds for s in scenes)
        logger.info(
            f"🎬 Scene plan: {len(scenes)} scenes, "
            f"{total_dur:.1f}s total ({elapsed}s)"
        )
        return scenes

    def _generate_scenes(
        self,
        state: StoryState,
        nicho: NichoConfig,
        correction_notes: str,
    ) -> list[SceneBlueprint]:
        """LLM-powered scene splitting."""
        reference_block = self._build_reference_scene_block(state)

        correction_block = ""
        if correction_notes:
            correction_block = f"\n⚠️ CORRECCIONES: {correction_notes}\n"

        scene_min = float(getattr(settings, "short_scene_min_seconds", 2.5))
        scene_max = float(getattr(settings, "short_scene_max_seconds", 5.0))
        max_scenes = int(getattr(settings, "short_max_scenes", 12))
        min_scenes = int(getattr(settings, "short_min_scenes", 8))
        hook_max = float(getattr(settings, "short_hook_max_seconds", 2.0))
        target_total = int(getattr(settings, "target_duration_seconds", 40))
        max_total = int(getattr(settings, "max_video_duration", 60))

        min_total = int(getattr(settings, "min_video_duration", 62))
        system = f"""Eres un director de produccion audiovisual especializado en SHORTS virales >60s (TikTok Creator Rewards, {min_total}-{max_total}s).

Tu trabajo: dividir un guion en ESCENAS cinematograficas para un video corto de alta retencion.

CONTEXTO:
{state.to_context_string()}

STYLE PROFILE:
- Plataforma: {state.platform}
- Velocidad de corte: ultra_rapido (V16 PRO shorts)
- Transiciones preferidas: {', '.join(state.style_profile.transitions)}
- Visual base: {state.style_profile.visual_base}

REGLAS V16.2 PRO (OBLIGATORIAS):
- Maximo {max_scenes} escenas, minimo {min_scenes}.
- Escena 1 = HOOK: dura MAX {hook_max:.1f} segundos.
- Cada escena restante dura entre {scene_min:.1f} y {scene_max:.1f} segundos (ritmo rapido).
- Duracion total del video: entre {min_total} y {max_total} segundos (objetivo {target_total}s). NUNCA mas de {max_total}s y NUNCA menos de {min_total}s.
- La ultima escena debe ser el MICRO-LOOP (frase de curiosidad, NO un CTA tipico).
- Cambio visual/camara cada 3-5s.
- Incluye mood emocional por escena (tense, calm, revelatory, inspiring, shock)
- Incluye nota de camara (slow zoom in, static, pan left, dutch angle, close up)
- Incluye tipo de transicion (cut, fade, whip, zoom_cut). Prioriza cortes secos y whip.
- Las escenas deben progresar narrativamente.
- Si hay conflicto de fuentes, respeta: {state.precedence_rule}
{correction_block}
{reference_block}

Devuelve SOLO JSON valido. Formato:
[
  {{
    "scene_number": 1,
    "text": "texto de narracion para esta escena",
    "mood": "shock",
    "duration_seconds": 1.8,
    "camera_notes": "slow zoom in on subject",
    "transition_out": "whip"
  }}
]"""

        user = f"""GUIÓN COMPLETO:
{state.script_full}

HOOK:
{state.hook}

CTA:
{state.cta}

Divide en escenas cinematográficas. Incluye el hook como escena 1 y el CTA como última escena."""

        text = self._call_llm(system, user)
        return self._parse_scenes(text)

    def _enrich_visuals(
        self,
        scenes: list[SceneBlueprint],
        state: StoryState,
        nicho: NichoConfig,
    ) -> list[SceneBlueprint]:
        """Add visual prompts to each scene for Veo/image generation.

        Uses character consistency + style direction from StoryState.
        """
        # Base visual direction
        visual_base = state.visual_direction or nicho.direccion_visual

        # Character descriptions for consistency
        char_desc = ""
        if state.characters:
            char_desc = ". ".join(
                f"{c.role}: {c.appearance}, {c.visual_style}"
                for c in state.characters
            )

        mood_visuals = {
            "shock": "dramatic contrast, intense shadows, urgent composition",
            "tense": "low key lighting, narrow depth of field, dark edges",
            "calm": "soft diffused light, open composition, gentle tones",
            "revelatory": "spotlight effect, emerging from shadow, golden hour feel",
            "inspiring": "backlit silhouette, wide angle, warm ascending light",
            "neutral": "clean balanced lighting, centered composition",
        }

        camera_visuals = {
            "slow zoom in": "slight push in, increasing intimacy",
            "static": "locked frame, stable composition",
            "pan left": "gentle horizontal drift, revealing new element",
            "pan right": "lateral movement, following action",
            "dutch angle": "tilted frame, 15-degree angle, unease",
            "close up": "tight crop on subject, bokeh background",
            "wide": "establishing shot, full environment visible",
        }

        reference_hint = ""
        if state.has_reference():
            key_anchor = state.reference_key_points[0] if state.reference_key_points else state.reference_summary[:140]
            cadence_hint = ""
            if state.reference_avg_cut_seconds > 0:
                cadence_hint = f" Avg cut target {state.reference_avg_cut_seconds:.2f}s."
            promise_hint = ""
            if state.reference_delivery_promise:
                promise_hint = f" Delivery promise {state.reference_delivery_promise}."
            reference_hint = f" Reference anchor: {key_anchor}.{promise_hint}{cadence_hint}"

        context_blob = " ".join(
            filter(
                None,
                [
                    state.topic,
                    state.hook,
                    state.script_full,
                    state.reference_summary,
                    " ".join(state.key_points[:4]),
                ],
            )
        ).lower()
        guardrails = "No text overlays, no watermarks, cinematic quality."
        if re.search(r"\b(18\d{2}|19\d{2})\b", context_blob) or any(
            token in context_blob for token in {"historia", "historical", "vintage", "tribu", "fbi", "osage"}
        ):
            guardrails += " Period-accurate wardrobe and locations, no modern skyscrapers, no corporate offices."
        if any(token in context_blob for token in {"misterio", "oscuro", "asesinato", "crimen", "conspiracion", "surveillance"}):
            guardrails += " No cheerful sunshine, no family celebration mood, no peaceful park ending."
        guardrails += " No cartoon, no animation, no 3D characters."

        for scene in scenes:
            mood_extra = mood_visuals.get(scene.mood, "")
            cam_extra = camera_visuals.get(scene.camera_notes.lower(), "")

            scene.visual_prompt = (
                f"Professional vertical video (9:16). "
                f"{scene.text}. "
                f"Style: {visual_base}. "
                f"{mood_extra}. "
                f"{cam_extra}. "
                f"{char_desc}. "
                f"{reference_hint} "
                f"{guardrails}"
            ).strip()

        return scenes

    def _build_reference_scene_block(self, state: StoryState) -> str:
        """Build reference constraints block for scene decomposition."""
        if not state.has_reference():
            return "REFERENCE: N/A"

        lines = [
            f"REFERENCE_URL: {state.reference_url}",
            f"REFERENCE_TITLE: {state.reference_title or 'N/A'}",
            f"REFERENCE_SUMMARY: {state.reference_summary[:260] if state.reference_summary else 'N/A'}",
        ]
        if state.reference_delivery_promise:
            lines.append(f"REFERENCE_DELIVERY_PROMISE: {state.reference_delivery_promise}")
        if state.reference_hook_seconds > 0:
            lines.append(f"REFERENCE_HOOK_SECONDS: {state.reference_hook_seconds:.2f}")
        if state.reference_avg_cut_seconds > 0:
            lines.append(f"REFERENCE_AVG_CUT_SECONDS: {state.reference_avg_cut_seconds:.2f}")
        if state.reference_key_points:
            lines.append("REFERENCE_KEY_POINTS: " + " | ".join(state.reference_key_points[:4]))
        return "\n".join(lines)

    def _fallback_split(self, state: StoryState) -> list[SceneBlueprint]:
        """Simple sentence-based splitting when LLM fails."""
        max_scenes = int(getattr(settings, "short_max_scenes", 12))
        scene_min = float(getattr(settings, "short_scene_min_seconds", 2.5))
        scene_max = float(getattr(settings, "short_scene_max_seconds", 5.0))
        hook_max = float(getattr(settings, "short_hook_max_seconds", 2.0))

        full_text = " ".join(filter(None, [state.hook, state.script_full, state.cta]))
        sentences = re.split(r"[.!?]+", full_text)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]

        scenes: list[SceneBlueprint] = []
        for i, text in enumerate(sentences[:max_scenes]):
            words = len(text.split())
            dur = max(scene_min, min(scene_max, words * 0.28))  # ~0.28s per word
            if i == 0:
                dur = min(dur, hook_max)

            scenes.append(SceneBlueprint(
                scene_number=i + 1,
                text=text,
                mood="shock" if i == 0 else "neutral",
                duration_seconds=round(dur, 2),
                camera_notes="slow zoom in" if i == 0 else "static",
                transition_out="whip" if i == 0 else "cut",
            ))

        return scenes

    def _enforce_short_form_rhythm(
        self,
        scenes: list[SceneBlueprint],
    ) -> list[SceneBlueprint]:
        """Clamp LLM-proposed scenes to the V16 PRO short-form contract.

        - At most ``short_max_scenes`` scenes.
        - Hook scene duration <= ``short_hook_max_seconds``.
        - Remaining scenes clamped to ``[short_scene_min_seconds, short_scene_max_seconds]``.
        - Total duration <= ``max_video_duration`` (excess scaled down proportionally).
        """
        if not scenes:
            return scenes

        max_scenes = int(getattr(settings, "short_max_scenes", 12))
        scene_min = float(getattr(settings, "short_scene_min_seconds", 2.5))
        scene_max = float(getattr(settings, "short_scene_max_seconds", 5.0))
        hook_max = float(getattr(settings, "short_hook_max_seconds", 2.0))
        max_total = float(getattr(settings, "max_video_duration", 60))

        clamped = scenes[:max_scenes]
        for idx, scene in enumerate(clamped):
            try:
                dur = float(scene.duration_seconds or 0.0)
            except (TypeError, ValueError):
                dur = 2.5

            if idx == 0:
                dur = min(max(0.8, dur), hook_max)
            else:
                dur = max(scene_min, min(scene_max, dur))

            scene.duration_seconds = round(dur, 2)
            scene.scene_number = idx + 1

        total = sum(float(s.duration_seconds or 0.0) for s in clamped)
        if total > max_total and total > 0:
            scale = max_total / total
            for idx, scene in enumerate(clamped):
                floor_dur = hook_max if idx == 0 else scene_min
                scaled = max(floor_dur * 0.8, scene.duration_seconds * scale)
                scene.duration_seconds = round(scaled, 2)
            logger.info(
                f"🎯 V16 PRO: scaled scene durations by {scale:.2f} to fit {max_total:.0f}s cap "
                f"(from {total:.1f}s)"
            )

        if len(clamped) < len(scenes):
            logger.info(
                f"🎯 V16 PRO: trimmed scene count {len(scenes)} → {len(clamped)} "
                f"(max {max_scenes} for shorts)"
            )

        return clamped

    def _parse_scenes(self, raw: str) -> list[SceneBlueprint]:
        """Parse scene JSON from LLM response."""
        text = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text).strip()
        text = re.sub(r"[\x00-\x1f]", " ", text)

        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            logger.warning("SceneAgent: no JSON array found")
            return []

        text = text[start:end + 1]
        text = re.sub(r",\s*([}\]])", r"\1", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning(f"SceneAgent JSON parse failed: {e}")
            return []

        scenes = []
        for item in data:
            try:
                scenes.append(SceneBlueprint(
                    scene_number=item.get("scene_number", len(scenes) + 1),
                    text=item.get("text", ""),
                    mood=item.get("mood", "neutral"),
                    duration_seconds=float(item.get("duration_seconds", 2.5)),
                    camera_notes=item.get("camera_notes", "static"),
                    transition_out=item.get("transition_out", "cut"),
                ))
            except Exception:
                continue

        return scenes

    def _call_llm(self, system: str, user: str) -> str:
        """Call LLM with Gemini primary and GPT fallback."""
        try:
            text, _model_used = call_llm_primary_gemini(
                system_prompt=system,
                user_prompt=user,
                temperature=0.7,
                timeout=45,
                max_retries=2,
                purpose="scene_agent",
            )
            return text
        except Exception as e:
            logger.error(f"SceneAgent LLM error: {e}")
            return ""
