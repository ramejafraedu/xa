"""Pydantic config models for nicho definitions and app-level settings."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# Valid platform identifiers accepted by the pipeline.
VALID_PLATFORMS = {
    "tiktok_reels",
    "tiktok",
    "reels",
    "shorts",
    "facebook",
    "youtube",
    "instagram",
}

# Music genres supported by the pipeline's music providers.
VALID_MUSIC_GENRES = {
    "motivational",
    "dark",
    "ambient",
    "cinematic",
    "epic",
    "sad",
    "corporate",
    "upbeat",
    "chill",
    "electronic",
    "hip-hop",
    "lo-fi",
}


class NichoConfig(BaseModel):
    """Configuration for a single content niche.

    Validated at load time so malformed YAML files are caught immediately
    instead of failing deep inside the pipeline at Stage 6+.
    """

    slug: str
    nombre: str
    tono: str
    plataforma: str = "tiktok_reels"
    genero_musica: str = "motivational"
    num_clips: int = Field(ge=4, le=15, default=8)
    keywords_count: int = Field(ge=1, le=20, default=8)
    tipo_cortes: str = "rapido"
    estilo_narrativo: str = ""
    voz_gemini: str = "Kore"
    voz_edge: str = "es-MX-JorgeNeural"
    rate_tts: str = "+0%"
    pitch_tts: str = "+0Hz"
    horas: list[int] = Field(default_factory=lambda: [7, 15, 23])
    direccion_visual: str = ""

    # --- Validators ---

    @field_validator("plataforma")
    @classmethod
    def _validate_plataforma(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_PLATFORMS:
            raise ValueError(
                f"plataforma '{v}' no válida. "
                f"Opciones: {', '.join(sorted(VALID_PLATFORMS))}"
            )
        return cleaned

    @field_validator("genero_musica")
    @classmethod
    def _validate_genero_musica(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_MUSIC_GENRES:
            # Warn but don't fail — new genres can be added dynamically
            from loguru import logger
            logger.warning(
                f"genero_musica '{v}' no está en la lista conocida "
                f"({', '.join(sorted(VALID_MUSIC_GENRES))}). "
                f"Continuando de todas formas."
            )
        return cleaned

    @field_validator("horas")
    @classmethod
    def _validate_horas(cls, v: list[int]) -> list[int]:
        for h in v:
            if not (0 <= h <= 23):
                raise ValueError(
                    f"Hora {h} fuera de rango. Cada hora debe ser 0-23."
                )
        return v

    @field_validator("rate_tts")
    @classmethod
    def _validate_rate_tts(cls, v: str) -> str:
        import re
        if not re.match(r'^[+\-]?\d+%$', v.strip()):
            raise ValueError(
                f"rate_tts '{v}' no válido. Formato esperado: '+5%', '-10%', '+0%'"
            )
        return v.strip()

    @field_validator("pitch_tts")
    @classmethod
    def _validate_pitch_tts(cls, v: str) -> str:
        import re
        if not re.match(r'^[+\-]?\d+Hz$', v.strip()):
            raise ValueError(
                f"pitch_tts '{v}' no válido. Formato esperado: '+0Hz', '-15Hz', '+5Hz'"
            )
        return v.strip()

    @model_validator(mode="after")
    def _validate_business_rules(self) -> "NichoConfig":
        """Cross-field business rules."""
        if self.keywords_count < self.num_clips:
            from loguru import logger
            logger.warning(
                f"Nicho '{self.slug}': keywords_count ({self.keywords_count}) "
                f"< num_clips ({self.num_clips}). "
                f"Puede generar clips sin keywords suficientes."
            )
        return self




class AppConfig(BaseModel):
    """Top-level application configuration — not env vars, just structural defaults."""

    max_healing_attempts: int = 2
    quality_threshold: float = 7.5
    # Platform upload limits (seconds): TikTok up to 60m, Reels/Shorts up to 3m.
    max_duration_tiktok: float = 3600.0
    max_duration_reels: float = 180.0
    max_duration_shorts: float = 180.0
    max_duration_facebook: float = 120.0
    min_disk_space_gb: float = 2.0
    output_retention_days: int = 0  # 0 = keep forever
