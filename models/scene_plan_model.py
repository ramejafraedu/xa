"""Scene Plan structured models for OpenMontage composition engine.

Defines the data contract between pipeline stages:
  ScenePlan → CompositionEngine → CinematicDirector → TimelineBuilder → Renderer

All format/resolution logic is centralized here via FORMAT_SPECS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────
# VIDEO FORMAT — centraliza resoluciones y aspect ratios
# ─────────────────────────────────────────────────────────────

class VideoFormat(str, Enum):
    """Formatos de video soportados."""
    VERTICAL = "vertical"        # 1080x1920 (9:16) — Shorts, Reels, TikTok
    HORIZONTAL = "horizontal"    # 1920x1080 (16:9) — YouTube, LinkedIn
    SQUARE = "square"            # 1080x1080 (1:1)  — Instagram Feed


FORMAT_SPECS: dict[str, dict[str, Any]] = {
    "vertical": {
        "w": 1080, "h": 1920,
        "aspect_num": 9, "aspect_den": 16,
        "crop_expr": "if(gt(a,9/16),ih*9/16,iw)",
        "crop_expr_h": "if(gt(a,9/16),ih,iw*16/9)",
        "label": "9:16 Vertical",
    },
    "horizontal": {
        "w": 1920, "h": 1080,
        "aspect_num": 16, "aspect_den": 9,
        "crop_expr": "if(gt(a,16/9),ih*16/9,iw)",
        "crop_expr_h": "if(gt(a,16/9),ih,iw*9/16)",
        "label": "16:9 Horizontal",
    },
    "square": {
        "w": 1080, "h": 1080,
        "aspect_num": 1, "aspect_den": 1,
        "crop_expr": "if(gt(a,1/1),ih,iw)",
        "crop_expr_h": "if(gt(a,1/1),ih,iw)",
        "label": "1:1 Square",
    },
}

# Mapeo de plataforma → formato por defecto
PLATFORM_FORMAT_MAP: dict[str, str] = {
    "youtube_shorts": "vertical",
    "tiktok": "vertical",
    "instagram_reels": "vertical",
    "instagram_feed": "square",
    "youtube": "horizontal",
    "linkedin": "horizontal",
    # Defaults
    "shorts": "vertical",
    "reels": "vertical",
}


def get_format_spec(fmt: str = "vertical") -> dict[str, Any]:
    """Return format spec dict. Falls back to vertical if unknown."""
    return FORMAT_SPECS.get(fmt, FORMAT_SPECS["vertical"])


def get_format_dimensions(fmt: str = "vertical") -> tuple[int, int]:
    """Return (width, height) for a format string."""
    spec = get_format_spec(fmt)
    return spec["w"], spec["h"]


def platform_to_format(platform: str) -> str:
    """Map platform string to video format. Falls back to vertical."""
    return PLATFORM_FORMAT_MAP.get(
        platform.lower().strip().replace(" ", "_"),
        "vertical",
    )


# ─────────────────────────────────────────────────────────────
# SCENE SPEC — especificación por escena
# ─────────────────────────────────────────────────────────────

@dataclass
class SceneSpec:
    """Especificación completa de una escena del plan de composición.

    Cada campo mapea directamente a una decisión visual concreta.
    """
    texto: str                        # Narración/contenido de la escena
    duracion: float = 4.0             # Duración estimada en segundos
    media: str = ""                   # Descripción del asset visual deseado
    motion: str = "slow"              # static/slow/dynamic/pan/timelapse/handheld
    estilo: str = "cinematic"         # cinematic/minimal/energetic/dark/warm
    shot_type: str = "medium"         # close-up/medium/wide/aerial/detail/overhead
    emotion: str = "neutral"          # dramatic/tense/calm/energetic/mysterious/inspiring/neutral
    transition_in: str = "cut"        # Transición de entrada: cut/crossfade/zoom/wipe
    transition_out: str = "cut"       # Transición de salida
    keywords: list[str] = field(default_factory=list)  # Keywords para búsqueda de clips
    scene_number: int = 0             # Posición en la secuencia

    def to_dict(self) -> dict[str, Any]:
        return {
            "texto": self.texto,
            "duracion": self.duracion,
            "media": self.media,
            "motion": self.motion,
            "estilo": self.estilo,
            "shot_type": self.shot_type,
            "emotion": self.emotion,
            "transition_in": self.transition_in,
            "transition_out": self.transition_out,
            "keywords": list(self.keywords),
            "scene_number": self.scene_number,
        }


# ─────────────────────────────────────────────────────────────
# SCENE PLAN — plan completo de composición
# ─────────────────────────────────────────────────────────────

@dataclass
class ScenePlan:
    """Plan de composición completo que el pipeline acepta como input."""
    format: VideoFormat = VideoFormat.VERTICAL
    scenes: list[SceneSpec] = field(default_factory=list)
    estilo_global: str = "cinematic"    # Estilo visual general
    mood_global: str = "professional"   # Mood general del video
    titulo: str = ""                    # Título del video
    nicho: str = ""                     # Nicho del canal

    @property
    def total_duration(self) -> float:
        return sum(s.duracion for s in self.scenes)

    @property
    def width(self) -> int:
        return get_format_spec(self.format.value)["w"]

    @property
    def height(self) -> int:
        return get_format_spec(self.format.value)["h"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format.value,
            "scenes": [s.to_dict() for s in self.scenes],
            "estilo_global": self.estilo_global,
            "mood_global": self.mood_global,
            "titulo": self.titulo,
            "nicho": self.nicho,
            "total_duration": self.total_duration,
            "width": self.width,
            "height": self.height,
        }


# ─────────────────────────────────────────────────────────────
# DIRECTION DECISION — salida del CinematicDirector
# ─────────────────────────────────────────────────────────────

@dataclass
class DirectionDecision:
    """Decisión de dirección visual para una escena.

    Generada por CinematicDirector, consumida por TimelineBuilder y Renderer.
    """
    scene_number: int = 0
    motion: str = "slow"                  # Tipo de movimiento a aplicar
    cut_speed: str = "medium"             # ultra_fast/fast/medium/slow
    color_grade: str = "neutral"          # dark_contrast/desaturated/warm/vibrant/cold_blue/golden/neutral
    transition: str = "cut"               # cut/crossfade/zoom/wipe
    transition_duration: float = 0.4      # Duración de la transición en segundos
    zoompan_intensity: float = 1.06       # Factor de zoom (1.0 = sin zoom)
    zoompan_direction: str = "center"     # center/left/right/up/down
    fade_in: float = 0.15                 # Duración del fade-in
    fade_out: float = 0.15                # Duración del fade-out

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_number": self.scene_number,
            "motion": self.motion,
            "cut_speed": self.cut_speed,
            "color_grade": self.color_grade,
            "transition": self.transition,
            "transition_duration": self.transition_duration,
            "zoompan_intensity": self.zoompan_intensity,
            "zoompan_direction": self.zoompan_direction,
            "fade_in": self.fade_in,
            "fade_out": self.fade_out,
        }


# ─────────────────────────────────────────────────────────────
# TIMELINE — salida del TimelineBuilder para el Renderer
# ─────────────────────────────────────────────────────────────

@dataclass
class TimelineCut:
    """Un corte individual en la timeline del video."""
    clip_path: Optional[Path] = None     # Ruta al clip/imagen
    image_path: Optional[Path] = None    # Ruta a imagen (si es image segment)
    start_time: float = 0.0              # Inicio en la timeline global
    end_time: float = 0.0                # Fin en la timeline global
    duration: float = 0.0                # Duración del corte
    transition_in: str = "cut"           # Transición de entrada
    transition_out: str = "cut"          # Transición de salida
    transition_duration: float = 0.4     # Duración de transición
    zoompan_intensity: float = 1.06      # Factor de zoom
    zoompan_direction: str = "center"    # Dirección del zoom
    color_grade: str = "neutral"         # Color grade a aplicar
    motion_type: str = "slow"            # Tipo de movimiento
    fade_in: float = 0.15               # Fade in duration
    fade_out: float = 0.15              # Fade out duration
    scene_number: int = 0               # Escena a la que pertenece
    is_image: bool = False              # True si es segmento de imagen


@dataclass
class Timeline:
    """Timeline completa ejecutable por el renderer."""
    total_duration: float = 0.0
    format: str = "vertical"
    width: int = 1080
    height: int = 1920
    cuts: list[TimelineCut] = field(default_factory=list)
    global_color_grade: str = "neutral"   # Grade global (se combina con per-cut)

    @property
    def cut_count(self) -> int:
        return len(self.cuts)


# ─────────────────────────────────────────────────────────────
# COMPOSITION RESULT — resultado completo del motor
# ─────────────────────────────────────────────────────────────

@dataclass
class CompositionResult:
    """Resultado completo del CompositionEngine."""
    timeline: Optional[Timeline] = None
    slideshow_risk: dict[str, Any] = field(default_factory=dict)
    quality_evaluation: dict[str, Any] = field(default_factory=dict)
    directions: list[DirectionDecision] = field(default_factory=list)
    format: str = "vertical"
    width: int = 1080
    height: int = 1920
    slideshow_score: float = 0.0
    verdict: str = "unknown"             # strong/acceptable/revise/fail
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
