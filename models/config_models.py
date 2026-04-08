"""Pydantic config models for nicho definitions and app-level settings."""
from __future__ import annotations

from pydantic import BaseModel, Field


class NichoConfig(BaseModel):
    """Configuration for a single content niche."""

    slug: str
    nombre: str
    tono: str
    plataforma: str = "tiktok_reels"
    genero_musica: str = "motivational"
    num_clips: int = Field(ge=4, le=15, default=8)
    keywords_count: int = 8
    tipo_cortes: str = "rapido"
    estilo_narrativo: str = ""
    voz_gemini: str = "Kore"
    voz_edge: str = "es-MX-JorgeNeural"
    rate_tts: str = "+0%"
    pitch_tts: str = "+0Hz"
    horas: list[int] = Field(default_factory=lambda: [7, 15, 23])

    @property
    def direccion_visual(self) -> str:
        mapping = {
            "finanzas": "old money editorial, linen texture, classic wristwatch, neoclassical architecture, sepia and emerald green palette, luxury cinematic lighting, premium depth",
            "historia": "vintage noir documentary, aged paper texture, baroque shadows, desaturated sepia with steel blue accents, dramatic cinematic haze",
            "curiosidades": "surreal documentary macro, unusual perspective, textured background, teal-amber contrast, high-detail cinematic realism",
            "historias_reddit": "reddit confessional aesthetic, neon noir atmosphere, smartphone chat overlays, dark cinematic contrast, emotional close-ups, suspense pacing",
            "ia_herramientas": "futuristic productivity studio, glassmorphism dashboards, AI workflow overlays, high-contrast UI motion, premium tech cinematic look",
        }
        return mapping.get(self.slug, "cinematic editorial vertical, premium texture, subtle warm palette, filmic depth")


class AppConfig(BaseModel):
    """Top-level application configuration — not env vars, just structural defaults."""

    max_healing_attempts: int = 2
    quality_threshold: float = 7.5
    max_duration_tiktok: float = 60.0
    max_duration_reels: float = 90.0
    max_duration_shorts: float = 60.0
    max_duration_facebook: float = 120.0
    min_disk_space_gb: float = 2.0
    output_retention_days: int = 0  # 0 = keep forever
