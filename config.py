"""Video Factory V14 — Configuration loader.

Loads .env, defines all 5 nichos, and provides the global config singleton.
Uses pathlib for all paths — Windows/Linux portable.
"""
from __future__ import annotations

import json
import os
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


def _normalize_google_application_credentials() -> None:
    """Resolve GOOGLE_APPLICATION_CREDENTIALS to absolute path when possible.

    This avoids ADC failures when the process runs from a different cwd.
    """
    raw = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if not raw:
        return

    path = Path(raw)
    if path.is_absolute():
        return

    for candidate in [(_THIS_DIR / path).resolve(), (_THIS_DIR.parent / path).resolve()]:
        if candidate.exists():
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(candidate)
            return


_normalize_google_application_credentials()


# Module-level counter for Gemini key rotation (mutable list trick)
_gemini_rotation_counter: list[int] = [0]


class Settings(BaseSettings):
    """All environment variables in one place — validated by Pydantic."""

    # AI / Inference
    github_token: str = ""
    inference_api_url: str = "https://models.inference.ai.azure.com/chat/completions"
    inference_model: str = "gpt-4.1"
    inference_fallback_model: str = "gpt-4.1"
    openrouter_api_key: str = ""
    openrouter_api_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_model: str = "openai/gpt-4.1-mini"
    openrouter_fallback_model: str = "openai/gpt-4o-mini"
    openrouter_max_tokens: int = 4096
    openrouter_site_url: str = ""
    openrouter_app_name: str = "video_factory"

    # Gemini (up to 4 keys for rotation)
    gemini_api_key: str = ""
    gemini_api_key2: str = ""
    gemini_api_key3: str = ""
    gemini_api_key4: str = ""
    gemini_chat_models: str = "gemini-3.1-pro-preview,gemini-2.5-pro,gemini-2.5-flash,gemini-2.0-flash-001"
    # PRIMARY_LLM is accepted as compatibility alias when GEMINI_TEXT_MODEL is not set.
    gemini_text_model: str = os.getenv("PRIMARY_LLM", "gemini-3.1-pro-preview")
    gemini_vision_model: str = "gemini-2.5-pro"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    image_generation_model: str = "gemini-2.0-flash-preview-image-generation"
    gemini_key_cooldown_seconds: int = 25
    gemini_model_cooldown_seconds: int = 600
    gemini_enable_usage_stats: bool = True

    # ElevenLabs TTS
    elevenlabs_api_key: str = ""
    elevenlabs_api_url: str = "https://api.elevenlabs.io/v1/text-to-speech"
    elevenlabs_voice_id: str = "EXAVITQu4vr4xnSDxMaL"
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_stability: float = 0.45
    elevenlabs_similarity_boost: float = 0.75

    # Google Cloud TTS (premium provider)
    use_google_tts: bool = False
    google_tts_api_key: str = ""
    google_tts_service_account_json: str = ""
    google_tts_voice_name: str = "es-US-Neural2-A"
    google_tts_language_code: str = "es-US"
    google_tts_speaking_rate: float = 1.0
    google_tts_pitch: float = 0.0
    google_tts_timeout_seconds: int = 45

    # Pexels (up to 4 keys)
    pexels_api_key: str = ""
    pexels_api_key2v: str = ""
    pexels_api_key3v: str = ""
    pexels_api_key4v: str = ""

    # Pixabay
    pixabay_api_key: str = ""

    # Music
    jamendo_client_id: str = "61b41aa8"
    suno_api_key: str = ""
    suno_api_url: str = "https://api.sunoapi.org/api/v1/generate"
    suno_status_api_url: str = ""

    # AssemblyAI
    assemblyai_api_key: str = ""

    # Freesound (SFX)
    freesound_api_key: str = ""

    # Image Gen
    pollinations_base: str = "https://image.pollinations.ai"
    leonardo_api_key: str = ""
    prefer_stock_images: bool = True

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_videos_table: str = "videos"
    supabase_performance_table: str = "video_performance"

    # Google Drive/Sheets
    use_drive: bool = False
    google_drive_folder_id: str = "root"
    google_sheets_id: str = ""

    # TikTok Trending
    rapidapi_key: str = ""
    enable_tiktok_trending_api: bool = False

    # --- MEGA Upgrade: Provider Toggles ---
    # Lyria 3 (AI music via Gemini)
    use_lyria_music: bool = True
    use_suno_music: bool = True
    use_veo_clips: bool = False
    gemini_everywhere_mode: bool = False
    gemini_visual_boost_prompt: str = (
        "dynamic composition, expressive emotion, vibrant cinematic color grading, "
        "playful storytelling energy, premium polished detail"
    )

    # WhisperX (local word-level subtitles)
    use_whisperx: bool = True

    # Keep narration/subtitles locked to the canonical script text.
    tts_use_script_text: bool = True
    subtitles_use_script_text: bool = True

    # SaarD00-style A/B visual split
    enable_ab_visual_split: bool = False
    ab_visual_split_multiplier: int = 2
    enable_saar_composer: bool = False
    saar_composer_use_winner: bool = False

    # SaarD00-style post TTS processing
    enable_smart_silence_trim: bool = False
    audio_trim_silence_db: float = -40.0
    audio_trim_min_silence_seconds: float = 0.20
    enable_post_tts_loudnorm: bool = False
    post_tts_loudnorm_i: float = -16.0
    post_tts_loudnorm_lra: float = 11.0
    post_tts_loudnorm_tp: float = -1.5

    # Piper (offline TTS fallback)
    use_piper_tts: bool = False
    piper_model_path: str = ""

    # Remotion (premium renderer)
    use_remotion: bool = True
    force_ffmpeg_renderer: bool = False
    # V16 policy: Remotion is mandatory by default; FFmpeg is emergency-only.
    require_remotion: bool = True
    allow_ffmpeg_fallback: bool = False

    # --- V16 rollout feature flags ---
    free_mode: bool = False
    allow_freemium_in_free_mode: bool = True
    enable_web_research_plus: bool = False
    enable_reference_driven: bool = False
    enable_cost_governance: bool = False
    
    # --- V16.1: Variedad de Subtemas Anti-Repeticion ---
    force_subtopic_variety: bool = True
    subtopic_history_limit: int = 20  # Cuántos subtemas recordar por nicho
    subtopic_similarity_threshold: float = 0.7  # Umbral de similitud para considerar repetido
    
    # --- V16.1: Desactivar Caches para Forzar Variedad ---
    disable_stock_cache: bool = True
    disable_suno_cache: bool = True
    disable_image_cache: bool = True
    force_fresh_assets: bool = True
    
    # Provider Cascade (V16 PRO)
    enable_provider_cascade: bool = True
    provider_cascade_cooldown_seconds: int = 1800
    provider_cascade_max_failures: int = 5
    
    # CrewAI Quality Gate (V16 PRO)
    enable_crew_quality_gate: bool = True
    crew_max_debate_rounds: int = 3
    
    # Manim Animations (V16 PRO)
    enable_manim_animations: bool = True
    manim_enabled_nichos: str = "finanzas"
    manim_render_quality: str = "medium_quality"
    manim_timeout_seconds: int = 120
    
    # Remotion - Provider Principal (V16 PRO)
    force_remotion_primary: bool = True  # Forzar Remotion sin fallback a FFmpeg
    remotion_concurrency: int = 8  # Workers para 8 vCPU (auto-cap por CPU en runtime)
    remotion_timeout_seconds: int = 900
    remotion_quality: int = 80
    remotion_preset: str = "ultrafast"
    remotion_composition_id: str = "UniversalCommercial"
    remotion_theme: str = ""
    remotion_layout_variant: str = ""
    remotion_kinetic_level: str = ""
    remotion_transition_preset: str = ""
    remotion_feature_card_mode: str = ""
    
    # Fact verification (NEW - protects Faceless Channels from misinformation)
    enable_fact_verification: bool = True
    fact_verification_mode: str = "blocking"  # "blocking" | "warning" | "info"
    fact_verification_min_score: int = 60  # Minimum score (0-100) to auto-approve
    fact_verification_skip_for_nichos: str = ""  # Comma-separated slugs to skip
    
    # Memory management (RAM limit disabled - use full available memory)
    max_ram_percent_per_job: float = 85.0
    enable_memory_streaming: bool = False
    frame_buffer_seconds: int = 120
    force_gc_between_stages: bool = False

    # OpenMontage free-tools rollout (V15)
    enable_openmontage_free_tools: bool = True
    openmontage_root_dir: str = "OpenMontage-main"
    openmontage_default_playbook: str = "clean-professional"
    openmontage_enable_styles: bool = True
    openmontage_enable_analysis: bool = True
    openmontage_enable_subtitle: bool = True
    openmontage_enable_enhancement: bool = False
    openmontage_enable_video_utilities: bool = False
    v15_strict_free_media_tools: bool = True

    # --- V16 Integration: Playbook System ---
    playbook_validation_enabled: bool = True
    playbook_dir: str = "./nichos"
    playbook_quality_gate_enabled: bool = True
    playbook_enforce_colors: bool = True
    playbook_enforce_typography: bool = True
    playbook_enforce_motion: bool = False  # advisory-only for now
    playbook_acceptable_score_threshold: float = 8.0
    
    # --- V16 Integration: Provider Selector ---
    provider_selector_enabled: bool = True
    provider_routing_config: str = "core/selector_logic.yaml"
    provider_show_decisions: bool = True
    provider_log_level: str = "info"  # debug | info | warning

    # --- V16 Integration: Gemini Control Plane ---
    gemini_control_plane_enabled: bool = True
    gemini_control_plane_enforce_orders: bool = True
    gemini_control_plane_quality_default: str = "balanced"  # budget | balanced | premium
    
    # Provider-specific toggles
    tts_smart_routing_enabled: bool = True
    image_smart_routing_enabled: bool = True
    music_smart_routing_enabled: bool = True
    
    # Cost optimization targets
    target_cost_per_video_usd: float = 0.20
    prefer_free_providers: bool = True
    
    # --- V16 Integration: Avatar Pipeline (Future) ---
    avatar_pipeline_enabled: bool = False
    avatar_provider: str = "heygen"  # heygen | wan | d-id
    avatar_provider_keys: str = ""  # JSON string with credentials
    avatar_voice_id: str = "default"
    presenter_mode_enabled: bool = False
    
    # --- V16 Integration: Clip-Factory (Future) ---
    clipfactory_enabled: bool = False
    clipfactory_default_clip_count: int = 5
    clipfactory_platforms: str = "tiktok,reels,youtube_shorts"
    batch_mode_enabled: bool = False
    
    # --- V16 Integration: Review System (Future) ---
    reviewer_enabled: bool = True
    reviewer_skills_dir: str = "skills/review"
    reviewer_show_advisory: bool = True
    reviewer_block_on_issues: bool = False  # advisory-only by default
    
    # --- V16 Integration: Reference Analysis (Future) ---
    reference_analysis_depth: str = "moderate"  # basic | moderate | deep
    reference_color_extraction_enabled: bool = True
    reference_pacing_analysis_enabled: bool = True

    # Budget governance (USD)
    daily_budget_usd: float = 0.0
    monthly_budget_usd: float = 1.0

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
    niches_config_path: str = ""
    
    # Vertex AI (Enterprise)
    use_vertex_ai: bool = False
    vertex_project_id: str = ""
    vertex_location: str = "global"
    enable_image_cache: bool = True
    media_cache_ttl_days: int = 7
    generated_images_count: int = 6

    # Scheduler rollout
    scheduler_canary_mode: bool = False
    scheduler_canary_nichos: str = ""
    scheduler_use_v15: bool = True

    # Hashtags
    default_hashtags: str = "#viral #fyp #faceless"

    # Public URL (domain) used in logs/notifications instead of raw IPs.
    public_app_url: str = ""

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

    def get_gemini_chat_models(self) -> list[str]:
        """Return ordered Gemini chat models allowed for this deployment."""
        default_models = [
            self.gemini_text_model or os.getenv("PRIMARY_LLM") or "gemini-3.1-pro-preview",
            "gemini-3.1-pro-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash-001",
        ]
        configured = [m.strip() for m in (self.gemini_chat_models or "").split(",") if m.strip()]
        models = configured or default_models

        # De-duplicate while preserving order to avoid redundant attempts.
        deduped: list[str] = []
        seen: set[str] = set()
        for model in models:
            lowered = model.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(model)
        return deduped

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

    def resolved_piper_model_path(self) -> Path:
        """Return Piper model path resolved against project base dir."""
        model_cfg = (self.piper_model_path or "").strip()
        if not model_cfg:
            return Path("")
        model_path = Path(model_cfg)
        if not model_path.is_absolute():
            model_path = self.base_dir / model_path
        return model_path

    def piper_ready(self) -> bool:
        """Whether Piper offline TTS is configured and model file exists."""
        if not self.use_piper_tts:
            return False
        if not (self.piper_model_path or "").strip():
            return False
        model_path = self.resolved_piper_model_path()
        return model_path.exists() and model_path.is_file()

    def resolved_google_tts_service_account_path(self) -> Path:
        """Return Google TTS service account path resolved against project base dir."""
        cfg = (self.google_tts_service_account_json or "").strip()
        if not cfg:
            return Path("")
        path = Path(cfg)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def google_tts_service_account_configured(self) -> bool:
        """Whether a valid local service account JSON path is configured."""
        if not (self.google_tts_service_account_json or "").strip():
            return False
        path = self.resolved_google_tts_service_account_path()
        return path.exists() and path.is_file()

    def google_tts_effective_enabled(self) -> bool:
        """Google TTS can run when enabled and has at least one auth path hint.

        Auth hints accepted:
        - GOOGLE_TTS_API_KEY
        - GOOGLE_TTS_SERVICE_ACCOUNT_JSON
        - GOOGLE_APPLICATION_CREDENTIALS (external env)
        - Vertex mode with project configured
        """
        if not self.use_google_tts:
            return False
        if self.google_tts_api_key:
            return True
        if self.google_tts_service_account_configured():
            return True
        if (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip():
            return True
        if self.use_vertex_ai and bool(self.vertex_project_id):
            return True
        return False

    # --- Rollout / policy helpers ---

    def active_feature_flags(self) -> dict[str, bool]:
        """Expose all rollout flags in a single dict for auditing."""
        return {
            "free_mode": self.free_mode,
            "allow_freemium_in_free_mode": self.allow_freemium_in_free_mode,
            "use_remotion": self.use_remotion,
            "remotion_composition_id": self.remotion_composition_id,
            "remotion_theme": self.remotion_theme,
            "remotion_layout_variant": self.remotion_layout_variant,
            "remotion_kinetic_level": self.remotion_kinetic_level,
            "remotion_transition_preset": self.remotion_transition_preset,
            "remotion_feature_card_mode": self.remotion_feature_card_mode,
            "force_ffmpeg_renderer": self.force_ffmpeg_renderer,
            "require_remotion": self.require_remotion,
            "allow_ffmpeg_fallback": self.allow_ffmpeg_fallback,
            "use_piper_tts": self.use_piper_tts,
            "use_google_tts": self.use_google_tts,
            "google_tts_effective_enabled": self.google_tts_effective_enabled(),
            "use_vertex_ai": self.use_vertex_ai,
            "prefer_stock_images": self.prefer_stock_images,
            "enable_image_cache": self.enable_image_cache,
            "tts_use_script_text": self.tts_use_script_text,
            "subtitles_use_script_text": self.subtitles_use_script_text,
            "enable_ab_visual_split": self.enable_ab_visual_split,
            "enable_saar_composer": self.enable_saar_composer,
            "saar_composer_use_winner": self.saar_composer_use_winner,
            "enable_smart_silence_trim": self.enable_smart_silence_trim,
            "enable_post_tts_loudnorm": self.enable_post_tts_loudnorm,
            "enable_web_research_plus": self.enable_web_research_plus,
            "enable_reference_driven": self.enable_reference_driven,
            "enable_cost_governance": self.enable_cost_governance,
            "enable_provider_cascade": self.enable_provider_cascade,
            "gemini_control_plane_enabled": self.gemini_control_plane_enabled,
            "gemini_control_plane_enforce_orders": self.gemini_control_plane_enforce_orders,
            "gemini_control_plane_quality_default": self.gemini_control_plane_quality_default,
            "gemini_everywhere_mode": self.gemini_everywhere_mode,
            "gemini_visual_boost_prompt": bool(self.gemini_visual_boost_prompt),
            "enable_crew_quality_gate": self.enable_crew_quality_gate,
            "enable_manim_animations": self.enable_manim_animations,
            "gemini_usage_stats": self.gemini_enable_usage_stats,
            "openrouter_enabled": bool(self.openrouter_api_key),
            "elevenlabs_enabled": bool(self.elevenlabs_api_key),
            "suno_enabled": bool(self.suno_api_key and self.use_suno_music),
            "scheduler_canary_mode": self.scheduler_canary_mode,
            "scheduler_use_v15": self.scheduler_use_v15,
            "enable_tiktok_trending_api": self.enable_tiktok_trending_api,
            "use_veo_clips": self.use_veo_clips,
            "enable_openmontage_free_tools": self.enable_openmontage_free_tools,
            "openmontage_enable_styles": self.openmontage_enable_styles,
            "openmontage_enable_analysis": self.openmontage_enable_analysis,
            "openmontage_enable_subtitle": self.openmontage_enable_subtitle,
            "openmontage_enable_enhancement": self.openmontage_enable_enhancement,
            "openmontage_enable_video_utilities": self.openmontage_enable_video_utilities,
            "v15_strict_free_media_tools": self.v15_strict_free_media_tools,
            # Fact verification flags
            "enable_fact_verification": self.enable_fact_verification,
            "fact_verification_mode": self.fact_verification_mode,
            "fact_verification_min_score": self.fact_verification_min_score,
            # Memory management flags
            "max_ram_percent_per_job": self.max_ram_percent_per_job,
            "enable_memory_streaming": self.enable_memory_streaming,
            "force_gc_between_stages": self.force_gc_between_stages,
        }

    def openmontage_root(self) -> Path:
        """Resolve OpenMontage workspace root.

        Kept as a helper to avoid scattering path logic across adapters.
        """
        root_cfg = (self.openmontage_root_dir or "OpenMontage-main").strip()
        root = Path(root_cfg)
        if not root.is_absolute():
            root = self.base_dir / root
        return root.resolve()

    def cost_governance_enabled(self) -> bool:
        """Enable governance explicitly or by freemium execution mode."""
        return self.enable_cost_governance or self.execution_mode_label() == "freemium"

    def fact_verification_should_block(self, nicho_slug: str = "") -> bool:
        """Determine if verification failures should block pipeline.
        
        Returns True only if:
        - Verification is enabled
        - Mode is 'blocking'
        - Niche is not in skip list
        """
        if not self.enable_fact_verification:
            return False
        
        if self.fact_verification_mode == "info":
            return False
            
        skip_nichos = [s.strip() for s in self.fact_verification_skip_for_nichos.split(",") if s.strip()]
        if nicho_slug and nicho_slug in skip_nichos:
            return False
            
        return self.fact_verification_mode == "blocking"

    def fact_verification_should_warn(self, nicho_slug: str = "") -> bool:
        """Determine if verification should show warnings (non-blocking)."""
        if not self.enable_fact_verification:
            return False
        if self.fact_verification_mode == "blocking":
            return False  # Already handled by blocking
        
        skip_nichos = [s.strip() for s in self.fact_verification_skip_for_nichos.split(",") if s.strip()]
        if nicho_slug and nicho_slug in skip_nichos:
            return False
            
        return self.fact_verification_mode in ("warning", "info")

    def resolve_scheduler_nichos(self, all_slugs: list[str]) -> list[str]:
        """Resolve target nichos for scheduler, supporting canary rollout."""
        if not self.scheduler_canary_mode:
            return all_slugs

        configured = [s.strip() for s in self.scheduler_canary_nichos.split(",") if s.strip()]
        selected = [slug for slug in configured if slug in all_slugs]
        if selected:
            return selected

        # Safe fallback: schedule only the first niche in canary mode.
        return all_slugs[:1]

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
            "suno",
            "pexels",
            "leonardo",
            "assemblyai",
        }
        premium_providers = {
            "azure_inference",
            "azure_openai",
            "openrouter",
            "elevenlabs",
            "google_tts",
            "google-cloud-tts",
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

    def provider_allowed(self, provider: str, usage: str = "") -> bool:
        """Return True when current policy allows using this provider.

        `usage` is optional and backward compatible. When provided for media
        and rendering contexts in V15, strict-free policy can be enforced even
        outside FREE_MODE.
        """
        usage_key = (usage or "").strip().lower()
        strict_usages = {
            "media",
            "render",
            "render_tools",
            "analysis",
            "subtitle",
            "enhancement",
            "video_tools",
            "video_post",
        }
        tier = self.provider_tier(provider)
        if self.v15_strict_free_media_tools and usage_key in strict_usages:
            return tier == "free"

        if not self.free_mode:
            return True

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
    def gemini_usage_stats_path(self) -> Path:
        return self.temp_dir / "gemini_usage_stats.json"

    @property
    def video_cache_dir(self) -> Path:
        return self.workspace / "video_cache"

    @property
    def image_cache_dir(self) -> Path:
        return self.workspace / "image_cache"

    @property
    def music_cache_dir(self) -> Path:
        return self.workspace / "music_cache"

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
        for d in [
            self.temp_dir,
            self.output_dir,
            self.review_dir,
            self.logs_dir,
            self.video_cache_dir,
            self.image_cache_dir,
            self.music_cache_dir,
        ]:
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
        if (
            not self.get_gemini_keys()
            and not (self.use_vertex_ai and bool(self.vertex_project_id))
            and not self.elevenlabs_api_key
            and not self.piper_ready()
            and not self.use_google_tts
        ):
            missing.append("GEMINI_API_KEY or ELEVENLABS_API_KEY or enable USE_GOOGLE_TTS")
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
        if (
            not self.get_gemini_keys()
            and not (self.use_vertex_ai and bool(self.vertex_project_id))
            and not self.elevenlabs_api_key
            and not self.piper_ready()
            and not self.use_google_tts
        ):
            critical_missing.append(
                "GEMINI_API_KEY (at least 1 of 4) or ELEVENLABS_API_KEY (TTS) "
                "or enable USE_GOOGLE_TTS "
                "or enable USE_PIPER_TTS with a valid PIPER_MODEL_PATH"
            )

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
# 5 Nichos — default hardcoded fallback
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
        estilo_narrativo="afirmaciones secas e imponentes con ritmo lento estilo Old Money, gancho llamativo, contraste polemico controlado y climax con accion concreta sobre inflacion, cripto y libertad financiera.",
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
        estilo_narrativo="tension incremental con preguntas abiertas y pausas dramaticas estilo misterio, gancho inquietante, polemica controlada y climax de revelacion con casos de IA y vigilancia global.",
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
        estilo_narrativo="curioso y sorprendente con datos impactantes y giros inesperados, gancho fuerte, debate inteligente y climax memorable sobre neuromarketing y psicologia del comportamiento.",
        voz_gemini="Kore",
        voz_edge="es-MX-JorgeNeural",
        rate_tts="+5%",
        pitch_tts="+0Hz",
        horas=[9, 17, 1],
    ),
    "historias_reddit": NichoConfig(
        slug="historias_reddit",
        nombre="historias de reddit impactantes",
        tono="narrativo intenso y adictivo",
        plataforma="tiktok_reels",
        genero_musica="dark",
        num_clips=10,
        keywords_count=10,
        tipo_cortes="ritmo progresivo con picos de tension",
        estilo_narrativo="narracion inmersiva con hook extremo, escalada emocional, plot twist y cierre abierto para comentarios, priorizando retencion y continuidad.",
        voz_gemini="Charon",
        voz_edge="es-ES-AlvaroNeural",
        rate_tts="+4%",
        pitch_tts="-8Hz",
        horas=[12, 18, 22],
    ),
    "ia_herramientas": NichoConfig(
        slug="ia_herramientas",
        nombre="ia aplicada y herramientas para ganar dinero",
        tono="directo, estrategico y convincente",
        plataforma="tiktok_reels",
        genero_musica="motivational",
        num_clips=8,
        keywords_count=8,
        tipo_cortes="rapidos con demostracion visual",
        estilo_narrativo="promesa monetizable, metodo en 3 pasos y resultado medible con CTA accionable para emprendedores y creadores.",
        voz_gemini="Fenrir",
        voz_edge="es-MX-JorgeNeural",
        rate_tts="+6%",
        pitch_tts="-2Hz",
        horas=[9, 14, 20],
    ),
}


def _load_nichos_from_file(
    base_nichos: dict[str, NichoConfig],
    config_path: str,
    base_dir: Path,
) -> dict[str, NichoConfig]:
    """Load niche templates from JSON file with backward-compatible fallback."""
    path_value = (config_path or "").strip()
    if not path_value:
        return base_nichos

    path = Path(path_value)
    if not path.is_absolute():
        path = base_dir / path

    if not path.exists():
        logger.warning(f"Niches config file not found: {path}")
        return base_nichos

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed reading niches config: {exc}")
        return base_nichos

    try:
        loaded: dict[str, NichoConfig] = {}

        if isinstance(raw, dict) and isinstance(raw.get("nichos"), list):
            items = raw["nichos"]
            for item in items:
                if not isinstance(item, dict):
                    continue
                nicho = NichoConfig(**item)
                loaded[nicho.slug] = nicho

        elif isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                nicho = NichoConfig(**item)
                loaded[nicho.slug] = nicho

        elif isinstance(raw, dict):
            for slug, payload in raw.items():
                if not isinstance(payload, dict):
                    continue
                payload = dict(payload)
                payload.setdefault("slug", str(slug))
                nicho = NichoConfig(**payload)
                loaded[nicho.slug] = nicho

        if not loaded:
            logger.warning(f"Niches config has no valid entries: {path}")
            return base_nichos

        logger.info(f"Loaded {len(loaded)} niche templates from {path}")
        return loaded

    except Exception as exc:
        logger.warning(f"Failed parsing niches config schema: {exc}")
        return base_nichos


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
settings = Settings()
app_config = AppConfig()

# Load nichos: YAML manifests (/nichos/*.yaml) + JSON file + hardcoded fallback
# Priority: nichos/*.yaml > NICHES_CONFIG_PATH (JSON) > hardcoded dict
try:
    from nichos._loader import load_nichos_from_yaml_dir as _yaml_loader
    NICHOS = _yaml_loader(NICHOS)
except Exception as _e:
    logger.debug(f"YAML niche loader not available ({_e}), trying JSON config path...")

# JSON override on top (if NICHES_CONFIG_PATH is set)
if settings.niches_config_path:
    NICHOS = _load_nichos_from_file(NICHOS, settings.niches_config_path, settings.base_dir)
