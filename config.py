"""Video Factory V14 — Configuration loader.

Loads .env, defines all 5 nichos, and provides the global config singleton.
Uses pathlib for all paths — Windows/Linux portable.
"""
from __future__ import annotations

import platform
import shutil
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from loguru import logger
from pydantic import Field
from pydantic_settings import BaseSettings

from models.config_models import NichoConfig, AppConfig


# ---------------------------------------------------------------------------
# Load .env from the video_factory directory
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent
_ENV_FILE = _THIS_DIR / ".env"
if not _ENV_FILE.exists():
    _ENV_FILE = _THIS_DIR.parent / ".env"  # fallback: parent dir
load_dotenv(_ENV_FILE, override=False)


# Module-level counter for Gemini key rotation (mutable list trick)
_gemini_rotation_counter: list[int] = [0]


class Settings(BaseSettings):
    """All environment variables in one place — validated by Pydantic."""

    # AI / Inference
    github_token: str = ""
    inference_api_url: str = "https://models.inference.ai.azure.com/chat/completions"
    inference_model: str = "gpt-4.1"
    inference_fallback_model: str = "gpt-4.1"

    # Gemini (up to 4 keys for rotation)
    gemini_api_key: str = ""
    gemini_api_key2: str = ""
    gemini_api_key3: str = ""
    gemini_api_key4: str = ""

    # Pexels (up to 4 keys)
    pexels_api_key: str = ""
    pexels_api_key2v: str = ""
    pexels_api_key3v: str = ""
    pexels_api_key4v: str = ""

    # Pixabay
    pixabay_api_key: str = ""

    # Music
    jamendo_client_id: str = "61b41aa8"

    # AssemblyAI
    assemblyai_api_key: str = ""

    # Freesound (SFX)
    freesound_api_key: str = ""

    # Image Gen
    pollinations_base: str = "https://image.pollinations.ai"
    leonardo_api_key: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""

    # Google Drive/Sheets
    use_drive: bool = False
    google_drive_folder_id: str = "root"
    google_sheets_id: str = ""

    # TikTok Trending
    rapidapi_key: str = ""

    # --- MEGA Upgrade: Provider Toggles ---
    # Lyria 3 (AI music via Gemini)
    use_lyria_music: bool = True

    # WhisperX (local word-level subtitles)
    use_whisperx: bool = True

    # Remotion (premium renderer)
    use_remotion: bool = False  # False by default — needs npx remotion setup first

    # --- V16 rollout feature flags ---
    free_mode: bool = False
    allow_freemium_in_free_mode: bool = True
    enable_web_research_plus: bool = False
    enable_reference_driven: bool = False
    enable_cost_governance: bool = False

    # Budget governance (USD)
    daily_budget_usd: float = 0.0

    # Stage estimates used by cost governance
    est_cost_research_usd: float = 0.0
    est_cost_script_usd: float = 0.0
    est_cost_scene_usd: float = 0.0
    est_cost_assets_usd: float = 0.10
    est_cost_tts_usd: float = 0.03
    est_cost_render_usd: float = 0.0

    # Backup Gemini API key (for rotation/rate limits)
    gemini_api_key_backup: str = ""

    # Workspace
    workspace_dir: str = "./workspace"
    output_retention_days: int = 0
    min_disk_space_gb: float = 2.0

    # Hashtags
    default_hashtags: str = "#viral #fyp #faceless"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    # --- Gemini key rotation ---

    def get_gemini_keys(self) -> list[str]:
        """Return all non-empty Gemini API keys."""
        keys = [
            self.gemini_api_key,
            self.gemini_api_key2,
            self.gemini_api_key3,
            self.gemini_api_key4,
            self.gemini_api_key_backup,
        ]
        return [k for k in keys if k]

    def next_gemini_key(self) -> str:
        """Get next Gemini key in round-robin rotation.

        Distributes requests across all 4 keys to avoid rate limits.
        Usage: client = genai.Client(api_key=settings.next_gemini_key())
        """
        keys = self.get_gemini_keys()
        if not keys:
            return ""
        key = keys[_gemini_rotation_counter[0] % len(keys)]
        _gemini_rotation_counter[0] += 1
        return key

    def get_gemini_key(self) -> str:
        """Backward-compatible alias used by V15 agents.

        Returns the next key in rotation to spread requests across keys.
        """
        return self.next_gemini_key()

    # --- Rollout / policy helpers ---

    def active_feature_flags(self) -> dict[str, bool]:
        """Expose all rollout flags in a single dict for auditing."""
        return {
            "free_mode": self.free_mode,
            "allow_freemium_in_free_mode": self.allow_freemium_in_free_mode,
            "use_remotion": self.use_remotion,
            "enable_web_research_plus": self.enable_web_research_plus,
            "enable_reference_driven": self.enable_reference_driven,
            "enable_cost_governance": self.enable_cost_governance,
        }

    def execution_mode_label(self) -> str:
        """Human-readable execution mode used in job manifests."""
        if self.free_mode:
            if self.allow_freemium_in_free_mode:
                return "freemium"
            return "free"
        if self.enable_reference_driven:
            return "reference"
        return "normal"

    def provider_tier(self, provider: str) -> str:
        """Classify providers as free, freemium, or premium."""
        name = (provider or "").strip().lower()
        if not name:
            return "free"

        free_providers = {
            "edge_tts",
            "edge-tts",
            "pixabay",
            "jamendo",
            "coverr",
            "pollinations",
            "freesound",
        }
        freemium_providers = {
            "gemini",
            "lyria",
            "pexels",
            "leonardo",
            "assemblyai",
        }
        premium_providers = {
            "azure_inference",
            "azure_openai",
            "veo",
        }

        if name in free_providers:
            return "free"
        if name in freemium_providers:
            return "freemium"
        if name in premium_providers:
            return "premium"
        return "freemium"

    def provider_is_paid(self, provider: str) -> bool:
        """Best-effort provider classification for budget policy."""
        return self.provider_tier(provider) != "free"

    def provider_allowed(self, provider: str) -> bool:
        """Return True when current policy allows using this provider."""
        if not self.free_mode:
            return True

        tier = self.provider_tier(provider)
        if tier == "free":
            return True
        if tier == "freemium":
            return self.allow_freemium_in_free_mode
        return False

    def stage_estimated_cost_usd(self, stage: str) -> float:
        """Return stage-level estimate consumed by cost governance."""
        stage_key = (stage or "").strip().lower()
        mapping = {
            "research": self.est_cost_research_usd,
            "script": self.est_cost_script_usd,
            "scene_plan": self.est_cost_scene_usd,
            "assets": self.est_cost_assets_usd,
            "tts": self.est_cost_tts_usd,
            "render": self.est_cost_render_usd,
        }
        return max(0.0, float(mapping.get(stage_key, 0.0)))

    # --- Derived paths (pathlib) ---

    @property
    def base_dir(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def workspace(self) -> Path:
        p = Path(self.workspace_dir)
        if not p.is_absolute():
            p = self.base_dir / p
        return p.resolve()

    @property
    def temp_dir(self) -> Path:
        return self.workspace / "temp"

    @property
    def output_dir(self) -> Path:
        return self.workspace / "output"

    @property
    def review_dir(self) -> Path:
        return self.workspace / "output" / "review_manual"

    @property
    def budget_state_path(self) -> Path:
        return self.temp_dir / "daily_budget_state.json"

    @property
    def video_cache_dir(self) -> Path:
        return self.workspace / "video_cache"

    max_cache_size_gb: float = 50.0

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def pexels_keys(self) -> list[str]:
        return [k for k in [
            self.pexels_api_key,
            self.pexels_api_key2v,
            self.pexels_api_key3v,
            self.pexels_api_key4v,
        ] if k]

    @property
    def is_windows(self) -> bool:
        return platform.system() == "Windows"

    def ensure_dirs(self) -> None:
        """Create all workspace directories."""
        for d in [self.temp_dir, self.output_dir, self.review_dir, self.logs_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def check_disk_space(self) -> bool:
        """Check if there's enough free disk space."""
        usage = shutil.disk_usage(self.workspace)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < self.min_disk_space_gb:
            logger.error(f"Low disk space: {free_gb:.1f} GB free (min: {self.min_disk_space_gb} GB)")
            return False
        logger.debug(f"Disk space OK: {free_gb:.1f} GB free")
        return True

    def check_ffmpeg(self) -> bool:
        """Verify FFmpeg is installed and in PATH."""
        import subprocess
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version_line = result.stdout.split("\n")[0]
                logger.info(f"FFmpeg found: {version_line}")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        logger.error("FFmpeg not found in PATH. Install from https://ffmpeg.org/download.html")
        return False

    def validate_required_keys(self) -> list[str]:
        """Return list of missing keys as warnings."""
        missing = []
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.pexels_keys:
            missing.append("PEXELS_API_KEY (at least one)")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN (notifications won't work)")
        return missing

    def fail_fast_validate(self) -> None:
        """Fail immediately if critical variables are missing.

        Call this at startup, NOT during import.
        Critical = pipeline cannot function at all without these.
        """
        critical_missing = []
        if not self.github_token:
            critical_missing.append("GITHUB_TOKEN (needed for AI content generation via Azure)")
        if not self.pexels_keys:
            critical_missing.append("PEXELS_API_KEY (needed for stock videos — at least 1 of 4)")
        if not self.get_gemini_keys():
            critical_missing.append("GEMINI_API_KEY (needed for TTS + ScriptAgent — at least 1 of 4)")

        if critical_missing:
            msg = (
                "\n❌ CRITICAL: Cannot start Video Factory.\n"
                "Missing required environment variables:\n"
            )
            for k in critical_missing:
                msg += f"  • {k}\n"
            msg += (
                "\nCopy .env.example → .env and fill in the values.\n"
                "See: README.md for setup instructions.\n"
            )
            logger.error(msg)
            raise SystemExit(msg)


# ---------------------------------------------------------------------------
# 5 Nichos — exact match with MASTER V13 Config nodes
# ---------------------------------------------------------------------------
NICHOS: dict[str, NichoConfig] = {
    "finanzas": NichoConfig(
        slug="finanzas",
        nombre="finanzas personales y emprendimiento",
        tono="motivacional y energico",
        plataforma="tiktok_reels",
        genero_musica="motivational",
        num_clips=8,
        keywords_count=8,
        tipo_cortes="lentos y cinematograficos",
        estilo_narrativo="afirmaciones secas e imponentes con ritmo lento y pausado estilo Old Money. En 2026 usa datos de inflacion cripto y libertad financiera.",
        voz_gemini="Fenrir",
        voz_edge="es-MX-JorgeNeural",
        rate_tts="-10%",
        pitch_tts="-5Hz",
        horas=[7, 15, 23],
    ),
    "historia": NichoConfig(
        slug="historia",
        nombre="historia oscura y crimenes reales y conspiraciones",
        tono="misterioso y narrativo",
        plataforma="tiktok_reels",
        genero_musica="dark",
        num_clips=8,
        keywords_count=8,
        tipo_cortes="rapidos en tension lentos en revelacion",
        estilo_narrativo="tension incremental con preguntas abiertas y pausas dramaticas estilo misterio. En 2026 usa casos de IA y vigilancia global.",
        voz_gemini="Charon",
        voz_edge="es-ES-AlvaroNeural",
        rate_tts="+0%",
        pitch_tts="-15Hz",
        horas=[8, 16, 0],
    ),
    "curiosidades": NichoConfig(
        slug="curiosidades",
        nombre="curiosidades del mundo y datos psicologicos",
        tono="misterioso y curioso",
        plataforma="tiktok_reels",
        genero_musica="dark",
        num_clips=8,
        keywords_count=8,
        tipo_cortes="ultra dinamicos y variados",
        estilo_narrativo="curioso y sorprendente con datos impactantes y giros inesperados. En 2026 incluye neuromarketing y psicologia del comportamiento.",
        voz_gemini="Kore",
        voz_edge="es-MX-JorgeNeural",
        rate_tts="+5%",
        pitch_tts="+0Hz",
        horas=[9, 17, 1],
    ),
    "salud": NichoConfig(
        slug="salud",
        nombre="habitos saludables y nutricion",
        tono="calido y directo",
        plataforma="facebook",
        genero_musica="ambient",
        num_clips=6,
        keywords_count=6,
        tipo_cortes="suaves y fluidos",
        estilo_narrativo="calido y directo con consejos practicos. En 2026 usa longevidad biohacking y medicina preventiva.",
        voz_gemini="Aoede",
        voz_edge="es-MX-DaliaNeural",
        rate_tts="+0%",
        pitch_tts="+0Hz",
        horas=[10, 18, 2],
    ),
    "recetas": NichoConfig(
        slug="recetas",
        nombre="recetas de cocina faciles",
        tono="calido y cercano",
        plataforma="facebook",
        genero_musica="ambient",
        num_clips=6,
        keywords_count=6,
        tipo_cortes="rapidos y energicos",
        estilo_narrativo="cercano y entusiasta como compartir con un amigo. En 2026 usa tendencias de alimentacion plant-based y recetas virales.",
        voz_gemini="Aoede",
        voz_edge="es-MX-DaliaNeural",
        rate_tts="+5%",
        pitch_tts="+3Hz",
        horas=[11, 19, 3],
    ),
}


# Singleton
settings = Settings()
app_config = AppConfig()
