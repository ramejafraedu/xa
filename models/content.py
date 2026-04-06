"""Pydantic models for video content, quality scoring, and pipeline state.

MODULE CONTRACT:
  Input:  Raw dict from AI → VideoContent (validated)
  Output: VideoContent, QualityScores, PipelineResult, JobManifest

All pipeline modules consume and produce these typed contracts.
No raw dicts cross module boundaries.
"""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Platform(str, Enum):
    TIKTOK = "tiktok"
    REELS = "reels"
    SHORTS = "shorts"
    FACEBOOK = "facebook"


class ABVariant(str, Enum):
    A = "A"
    B = "B"


class CutSpeed(str, Enum):
    ULTRA_RAPIDO = "ultra_rapido"
    RAPIDO = "rapido"
    MIXTO = "mixto"
    CINEMATOGRAFICO = "cinematografico"


class JobStatus(str, Enum):
    """Formal pipeline states — not folder names, not strings."""
    PENDING = "pending"
    RUNNING = "running"
    CONTENT_GEN = "completed_content_gen"
    QUALITY_GATE = "completed_quality_gate"
    TTS = "completed_tts"
    SUBTITLES = "completed_subtitles"
    MEDIA = "completed_media"
    COMBINE = "completed_combine"
    VALIDATED = "completed_validated"  # pre-render validation passed
    RENDERED = "completed_render"
    PUBLISHED = "completed_publish"
    SUCCESS = "success"
    ERROR = "error"
    MANUAL_REVIEW = "manual_review"
    DRAFT = "draft"  # generated but not yet validated

    @classmethod
    def order(cls) -> list[str]:
        return [s.value for s in cls]


class FailureType(str, Enum):
    PROMPT = "fix_prompt"
    JSON = "fix_json"
    AUDIO = "fix_audio"
    RENDER = "fix_render"


class ErrorCode(str, Enum):
    """Concrete error codes so the self-healer gets a precise diagnosis,
    not a vague 'something failed' string."""
    # Content
    JSON_SCHEMA_INVALID = "JSON_SCHEMA_INVALID"
    JSON_PARSE_FAILED = "JSON_PARSE_FAILED"
    HOOK_TOO_WEAK = "HOOK_TOO_WEAK"
    DESARROLLO_WEAK = "DESARROLLO_WEAK"
    CIERRE_WEAK = "CIERRE_WEAK"
    QUALITY_BELOW_THRESHOLD = "QUALITY_BELOW_THRESHOLD"
    CONTENT_GEN_API_FAIL = "CONTENT_GEN_API_FAIL"
    # Audio
    TTS_EMPTY_AUDIO = "TTS_EMPTY_AUDIO"
    TTS_GEMINI_FAIL = "TTS_GEMINI_FAIL"
    TTS_EDGE_FAIL = "TTS_EDGE_FAIL"
    TTS_FILTER_FAIL = "TTS_FILTER_FAIL"
    # Render
    FFMPEG_FILTER_FAIL = "FFMPEG_FILTER_FAIL"
    FFMPEG_TIMEOUT = "FFMPEG_TIMEOUT"
    FFMPEG_CONCAT_FAIL = "FFMPEG_CONCAT_FAIL"
    FFMPEG_AUDIO_MIX_FAIL = "FFMPEG_AUDIO_MIX_FAIL"
    # Assets
    ASSET_MISSING = "ASSET_MISSING"
    ASSET_CORRUPT = "ASSET_CORRUPT"
    DURATION_EXCEEDED = "DURATION_EXCEEDED"
    SUBS_INVALID = "SUBS_INVALID"
    DISK_FULL = "DISK_FULL"
    # Generic
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Scored blocks
# ---------------------------------------------------------------------------

class BlockScores(BaseModel):
    hook: float = Field(ge=0, le=10, default=0)
    desarrollo: float = Field(ge=0, le=10, default=0)
    cierre: float = Field(ge=0, le=10, default=0)


# ---------------------------------------------------------------------------
# VideoContent — validated AI output
# ---------------------------------------------------------------------------

class VideoContent(BaseModel):
    """Schema for the JSON returned by the AI content generator.

    MODULE CONTRACT:
      Input:  raw dict from AI response
      Output: validated VideoContent with sanitized text fields

    Replaces the 350-line regex parser from MASTER V13's 'Procesar JSON' node.
    If the AI output doesn't match this schema, Pydantic raises a clear
    ValidationError whose `.errors()` list maps to specific ErrorCodes
    that the self-healer can act on precisely.
    """

    num_clips: int = Field(ge=4, le=15, default=8)
    titulo: str = Field(min_length=3, max_length=120)
    gancho: str = Field(min_length=5, max_length=200)
    gancho_variants: list[str] = Field(default_factory=list, max_length=5)
    hooks_alternos: list[str] = Field(default_factory=list, max_length=5)
    hook_score: float = Field(ge=0, le=10, default=7)
    block_scores: BlockScores = Field(default_factory=BlockScores)
    guion: str = Field(min_length=30)
    cta: str = Field(default="")
    caption: str = Field(default="", max_length=300)
    palabras_clave: list[str] = Field(min_length=2, max_length=15)
    mood_musica: str = Field(default="motivational")
    velocidad_cortes: CutSpeed = Field(default=CutSpeed.RAPIDO)
    prompt_imagen: str = Field(default="")
    duraciones_clips: list[float] = Field(default_factory=list)
    viral_score: float = Field(ge=0, le=10, default=7)

    @field_validator("guion")
    @classmethod
    def sanitize_guion(cls, v: str) -> str:
        import re
        text = str(v)
        text = re.sub(r'[\"\'\\u201C\\u201D\\u2018\\u2019#@\[\]{}()<>|\\*^~`%=+_]', " ", text)
        text = re.sub(r"\.{2,}", " ", text)
        text = re.sub(r" - ", " ", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    @field_validator("titulo", "gancho", "cta", "caption")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        import re
        text = str(v)
        text = re.sub(r'[\"\\\\]', " ", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    @property
    def input_hash(self) -> str:
        """Deterministic hash of the content inputs for idempotency checks."""
        payload = f"{self.guion}|{self.gancho}|{self.titulo}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# QualityScores — computed quality assessment
# ---------------------------------------------------------------------------

class QualityScores(BaseModel):
    """Computed quality assessment — combines model scores with heuristics.

    MODULE CONTRACT:
      Input:  VideoContent
      Output: QualityScores with is_approved flag + concrete error_codes list
    """

    block_scores: BlockScores
    quality_score: float = Field(ge=0, le=10)
    quality_status: str = "rechazado"  # "aprobado" | "rechazado"
    hook_heuristic: float = 0
    desarrollo_heuristic: float = 0
    cierre_heuristic: float = 0
    error_codes: list[ErrorCode] = Field(default_factory=list)

    @property
    def is_approved(self) -> bool:
        return self.quality_status == "aprobado"


# ---------------------------------------------------------------------------
# HealingRecord — single self-healing attempt
# ---------------------------------------------------------------------------

class HealingRecord(BaseModel):
    """Tracks a self-healing attempt for observability.

    MODULE CONTRACT:
      Input:  failure details (error_code, stage, message)
      Output: HealingRecord with success flag
    """

    attempt: int = 1
    failure_type: FailureType
    error_code: ErrorCode = ErrorCode.UNKNOWN
    stage: str
    error_message: str
    original_input: str = ""
    corrected_output: str = ""
    success: bool = False
    model_used: str = ""
    tokens_used: int = 0


# ---------------------------------------------------------------------------
# JobManifest — complete audit trail per video
# ---------------------------------------------------------------------------

class JobManifest(BaseModel):
    """Complete manifest per video, saved as job_manifest_{job_id}.json.

    This is the single source of truth for a job — not just a checkpoint
    but a full audit trail. Enables resume, debugging, and analytics.

    MODULE CONTRACT:
      Created at pipeline start, updated after each stage, persisted to disk.
      Contains every input hash, artifact path, timing, and error record.
    """

    job_id: str
    nicho_slug: str
    timestamp: int
    status: JobStatus = JobStatus.PENDING
    model_version: str = ""

    # Content
    titulo: str = ""
    gancho: str = ""
    guion: str = ""
    cta: str = ""
    caption: str = ""
    input_hash: str = ""  # Hash of guion+gancho+titulo for idempotency

    # Config
    ab_variant: str = "A"
    plataforma: str = "shorts"

    # Scores
    quality_score: float = 0
    viral_score: float = 0
    hook_score: float = 0
    block_scores: BlockScores = Field(default_factory=BlockScores)

    # Artifacts (paths relative to workspace)
    audio_path: str = ""
    subs_path: str = ""
    image_paths: list[str] = Field(default_factory=list)
    clip_paths: list[str] = Field(default_factory=list)
    music_path: str = ""
    sfx_paths: list[str] = Field(default_factory=list)
    video_path: str = ""
    thumbnail_path: str = ""
    drive_link: str = ""

    # Timing (seconds per stage)
    timings: dict[str, float] = Field(default_factory=dict)
    duration_seconds: float = 0
    tts_engine_used: str = ""

    # Error tracking
    error_stage: str = ""
    error_message: str = ""
    error_code: ErrorCode = ErrorCode.UNKNOWN
    retry_count: int = 0
    healing_attempts: list[HealingRecord] = Field(default_factory=list)

    # Post-render QA
    qa_passed: bool = True
    qa_issues: list[str] = Field(default_factory=list)

    class Config:
        use_enum_values = True


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------

PipelineResult = JobManifest
