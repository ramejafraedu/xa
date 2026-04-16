"""CinematicDirector — Dirección cinematográfica automática.

Convierte emoción + contexto narrativo en decisiones visuales concretas.
Esto es lo que separa un slideshow de contenido espectacular.

Reglas de ritmo:
  - Nunca 3+ cortes consecutivos con la misma dirección
  - Alternar velocidades (fast→slow→medium)
  - Hero moments = slow_zoom + longer duration
  - Pre-climax = cortes rápidos → climax = slow dramatic
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from models.scene_plan_model import DirectionDecision, SceneSpec


# ─────────────────────────────────────────────────────────────
# DIRECTION MATRIX — emoción → decisiones visuales
# ─────────────────────────────────────────────────────────────

DIRECTION_MATRIX: dict[str, dict[str, Any]] = {
    "dramatic": {
        "motion": "slow_zoom",
        "cut_speed": "medium",
        "color_grade": "dark_contrast",
        "transition": "crossfade",
        "transition_duration": 0.6,
        "zoompan_intensity": 1.08,
        "zoompan_direction": "center",
        "fade_in": 0.2,
        "fade_out": 0.2,
    },
    "tense": {
        "motion": "handheld",
        "cut_speed": "fast",
        "color_grade": "desaturated",
        "transition": "cut",
        "transition_duration": 0.15,
        "zoompan_intensity": 1.12,
        "zoompan_direction": "center",
        "fade_in": 0.1,
        "fade_out": 0.1,
    },
    "calm": {
        "motion": "slow_pan",
        "cut_speed": "slow",
        "color_grade": "warm",
        "transition": "crossfade",
        "transition_duration": 0.8,
        "zoompan_intensity": 1.04,
        "zoompan_direction": "center",
        "fade_in": 0.3,
        "fade_out": 0.3,
    },
    "energetic": {
        "motion": "dynamic",
        "cut_speed": "ultra_fast",
        "color_grade": "vibrant",
        "transition": "cut",
        "transition_duration": 0.1,
        "zoompan_intensity": 1.15,
        "zoompan_direction": "center",
        "fade_in": 0.08,
        "fade_out": 0.08,
    },
    "mysterious": {
        "motion": "slow_zoom",
        "cut_speed": "medium",
        "color_grade": "cold_blue",
        "transition": "crossfade",
        "transition_duration": 0.7,
        "zoompan_intensity": 1.06,
        "zoompan_direction": "center",
        "fade_in": 0.25,
        "fade_out": 0.25,
    },
    "inspiring": {
        "motion": "crane_up",
        "cut_speed": "medium",
        "color_grade": "golden",
        "transition": "crossfade",
        "transition_duration": 0.5,
        "zoompan_intensity": 1.10,
        "zoompan_direction": "up",
        "fade_in": 0.2,
        "fade_out": 0.2,
    },
    "neutral": {
        "motion": "slow",
        "cut_speed": "medium",
        "color_grade": "neutral",
        "transition": "cut",
        "transition_duration": 0.3,
        "zoompan_intensity": 1.05,
        "zoompan_direction": "center",
        "fade_in": 0.15,
        "fade_out": 0.15,
    },
    "sad": {
        "motion": "slow_pan",
        "cut_speed": "slow",
        "color_grade": "desaturated",
        "transition": "crossfade",
        "transition_duration": 0.8,
        "zoompan_intensity": 1.03,
        "zoompan_direction": "down",
        "fade_in": 0.3,
        "fade_out": 0.3,
    },
    "angry": {
        "motion": "dynamic",
        "cut_speed": "fast",
        "color_grade": "dark_contrast",
        "transition": "cut",
        "transition_duration": 0.1,
        "zoompan_intensity": 1.14,
        "zoompan_direction": "center",
        "fade_in": 0.08,
        "fade_out": 0.08,
    },
    "hopeful": {
        "motion": "crane_up",
        "cut_speed": "medium",
        "color_grade": "warm",
        "transition": "crossfade",
        "transition_duration": 0.5,
        "zoompan_intensity": 1.08,
        "zoompan_direction": "up",
        "fade_in": 0.2,
        "fade_out": 0.2,
    },
}

# Velocidades de corte → duraciones aproximadas por clip
CUT_SPEED_DURATIONS: dict[str, tuple[float, float]] = {
    "ultra_fast": (1.5, 2.5),
    "fast": (2.0, 3.5),
    "medium": (3.5, 5.5),
    "slow": (5.0, 8.0),
}

# Color grades → filtros FFmpeg
COLOR_GRADE_FILTERS: dict[str, str] = {
    "neutral": (
        "eq=saturation=0.90:contrast=1.06:brightness=0.012:gamma=0.97"
    ),
    "dark_contrast": (
        "eq=saturation=0.80:contrast=1.18:brightness=-0.02:gamma=0.90,"
        "colorbalance=rs=0.01:gs=-0.01:bs=0.03"
    ),
    "desaturated": (
        "eq=saturation=0.65:contrast=1.10:brightness=0.005:gamma=0.95,"
        "colorbalance=rs=-0.01:gs=-0.01:bs=0.02"
    ),
    "warm": (
        "eq=saturation=1.05:contrast=1.05:brightness=0.02:gamma=1.02,"
        "colorbalance=rs=0.04:gs=0.02:bs=-0.03"
    ),
    "vibrant": (
        "eq=saturation=1.25:contrast=1.08:brightness=0.015:gamma=1.00,"
        "colorbalance=rs=0.02:gs=0.01:bs=0.01"
    ),
    "cold_blue": (
        "eq=saturation=0.85:contrast=1.10:brightness=-0.01:gamma=0.95,"
        "colorbalance=rs=-0.03:gs=-0.01:bs=0.05"
    ),
    "golden": (
        "eq=saturation=1.10:contrast=1.04:brightness=0.03:gamma=1.03,"
        "colorbalance=rs=0.05:gs=0.03:bs=-0.04"
    ),
}


class CinematicDirector:
    """Director automático de cinematografía.

    Analiza escenas y devuelve decisiones de dirección visual
    basadas en emoción, ritmo y contexto narrativo.
    """

    def __init__(self) -> None:
        self._matrix = DIRECTION_MATRIX

    def direct_single(self, emotion: str) -> DirectionDecision:
        """Genera decisión de dirección para una emoción."""
        emotion_key = emotion.lower().strip()
        params = self._matrix.get(emotion_key, self._matrix["neutral"])
        return DirectionDecision(
            motion=params["motion"],
            cut_speed=params["cut_speed"],
            color_grade=params["color_grade"],
            transition=params["transition"],
            transition_duration=params["transition_duration"],
            zoompan_intensity=params["zoompan_intensity"],
            zoompan_direction=params["zoompan_direction"],
            fade_in=params["fade_in"],
            fade_out=params["fade_out"],
        )

    def direct(self, scene: SceneSpec) -> DirectionDecision:
        """Analiza una escena completa y devuelve decisión de dirección."""
        decision = self.direct_single(scene.emotion)
        decision.scene_number = scene.scene_number

        # Si la escena ya tiene motion explícito distinto a default, respetarlo
        if scene.motion and scene.motion not in ("slow", ""):
            decision.motion = scene.motion

        # Si la escena tiene transiciones explícitas, respetarlas
        if scene.transition_in and scene.transition_in != "cut":
            decision.transition = scene.transition_in

        return decision

    def direct_sequence(
        self, scenes: list[SceneSpec]
    ) -> list[DirectionDecision]:
        """Dirige una secuencia completa de escenas.

        Asegura ritmo y variedad aplicando reglas de dirección:
        - Nunca 3+ cortes consecutivos iguales
        - Alternar velocidades
        - Hero moments detectados y resaltados
        """
        if not scenes:
            return []

        decisions = [self.direct(scene) for scene in scenes]
        decisions = self._apply_rhythm_rules(decisions)
        decisions = self._apply_hero_moments(decisions, scenes)
        return decisions

    def _apply_rhythm_rules(
        self, decisions: list[DirectionDecision]
    ) -> list[DirectionDecision]:
        """Evita monotonía forzando variedad en la secuencia."""
        if len(decisions) < 3:
            return decisions

        for i in range(2, len(decisions)):
            prev1 = decisions[i - 1]
            prev2 = decisions[i - 2]
            current = decisions[i]

            # Regla: nunca 3 transiciones iguales seguidas
            if (
                current.transition == prev1.transition == prev2.transition
                and current.transition != "cut"
            ):
                current.transition = (
                    "cut" if prev1.transition == "crossfade" else "crossfade"
                )

            # Regla: nunca 3 cut_speeds iguales seguidos
            if current.cut_speed == prev1.cut_speed == prev2.cut_speed:
                speeds = ["ultra_fast", "fast", "medium", "slow"]
                current_idx = speeds.index(current.cut_speed) if current.cut_speed in speeds else 2
                # Elegir una velocidad diferente adyacente
                alt_idx = (current_idx + 1) % len(speeds)
                current.cut_speed = speeds[alt_idx]

            # Regla: nunca 3 zoompan_intensity idénticos
            if (
                abs(current.zoompan_intensity - prev1.zoompan_intensity) < 0.01
                and abs(prev1.zoompan_intensity - prev2.zoompan_intensity) < 0.01
            ):
                # Variar ±0.03
                if current.zoompan_intensity > 1.08:
                    current.zoompan_intensity -= 0.04
                else:
                    current.zoompan_intensity += 0.04

        return decisions

    def _apply_hero_moments(
        self,
        decisions: list[DirectionDecision],
        scenes: list[SceneSpec],
    ) -> list[DirectionDecision]:
        """Detecta y resalta momentos 'hero' de la narrativa.

        Hero = escena con emoción fuerte (dramatic/inspiring) que merece
        tratamiento especial: zoom más lento, corte más largo, crossfade.
        """
        hero_emotions = {"dramatic", "inspiring", "hopeful"}

        for i, (decision, scene) in enumerate(zip(decisions, scenes)):
            if scene.emotion in hero_emotions:
                # Hero moment: asegurar tratamiento premium
                decision.transition = "crossfade"
                decision.transition_duration = max(
                    decision.transition_duration, 0.5
                )
                decision.fade_in = max(decision.fade_in, 0.2)
                decision.fade_out = max(decision.fade_out, 0.2)

                # Pre-hero: si la escena anterior es rápida, crea contraste
                if i > 0 and decisions[i - 1].cut_speed in ("fast", "ultra_fast"):
                    decision.cut_speed = "slow"

        return decisions

    @staticmethod
    def get_color_grade_filter(grade_name: str) -> str:
        """Retorna el filtro FFmpeg para un color grade."""
        return COLOR_GRADE_FILTERS.get(
            grade_name, COLOR_GRADE_FILTERS["neutral"]
        )

    @staticmethod
    def get_cut_duration_range(speed: str) -> tuple[float, float]:
        """Retorna el rango de duración (min, max) para una velocidad de corte."""
        return CUT_SPEED_DURATIONS.get(speed, CUT_SPEED_DURATIONS["medium"])
