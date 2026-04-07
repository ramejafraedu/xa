"""Video Factory V15 — Scene Agent.

Takes the approved script and breaks it into SceneBlueprints.
Each scene gets:
  - Narration text
  - Visual prompt (with character/style consistency)
  - Mood + camera notes
  - Duration + transitions

This is what makes V15 visually coherent (not random).
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from loguru import logger

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

        system = f"""Eres un director de producción audiovisual especializado en videos cortos virales.

Tu trabajo: dividir un guión en ESCENAS cinematográficas.

CONTEXTO:
{state.to_context_string()}

STYLE PROFILE:
- Plataforma: {state.platform}
- Velocidad de corte: {state.style_profile.cut_speed}
- Transiciones preferidas: {', '.join(state.style_profile.transitions)}
- Visual base: {state.style_profile.visual_base}

REGLAS DE ESCENAS:
- Cada escena dura entre 1.5 y 4 segundos
- El hook debe ser la escena 1 (máx 2s)
- Incluye mood emocional por escena (tense, calm, revelatory, inspiring, shock)
- Incluye nota de cámara (slow zoom in, static, pan left, dutch angle, close up)
- Incluye tipo de transición (cut, fade, whip, zoom_cut)
- Las escenas deben progresar narrativamente
- Si hay conflicto de fuentes, respeta: {state.precedence_rule}
{correction_block}
{reference_block}

Devuelve SOLO JSON válido. Formato:
[
  {{
    "scene_number": 1,
    "text": "texto de narración para esta escena",
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
                f"No text overlays, no watermarks, cinematic quality."
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
        full_text = " ".join(filter(None, [state.hook, state.script_full, state.cta]))
        sentences = re.split(r"[.!?]+", full_text)
        sentences = [s.strip() for s in sentences if s.strip() and len(s.strip()) > 5]

        scenes = []
        for i, text in enumerate(sentences[:10]):  # Max 10 scenes
            words = len(text.split())
            dur = max(1.5, min(4.0, words * 0.25))  # ~0.25s per word

            scenes.append(SceneBlueprint(
                scene_number=i + 1,
                text=text,
                mood="neutral",
                duration_seconds=round(dur, 1),
                camera_notes="static" if i > 0 else "slow zoom in",
                transition_out="cut",
            ))

        return scenes

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
