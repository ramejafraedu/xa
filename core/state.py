"""Video Factory V15 — StoryState (Global Narrative Memory).

This is the brain of V15. Every agent reads and writes to StoryState,
ensuring coherence across script, visuals, audio, and editing.

MODULE CONTRACT:
  Created at pipeline start by Director.
  Updated after each agent stage.
  Persisted inside JobManifest for crash recovery.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Style Profiles — platform-specific optimization
# ---------------------------------------------------------------------------

class PlatformStyle(str, Enum):
    TIKTOK_VIRAL = "tiktok_viral"
    YOUTUBE_SHORTS = "youtube_shorts"
    REELS_AESTHETIC = "reels_aesthetic"
    FACEBOOK_ENGAGE = "facebook_engage"


class StyleProfile(BaseModel):
    """Visual + editorial rules per platform."""

    hook_max_seconds: float = 1.8
    total_duration: tuple[int, int] = (15, 60)
    cut_speed: str = "rapido"
    subtitle_style: str = "bold_animated"
    music_volume: float = 0.12
    transitions: list[str] = Field(default_factory=lambda: ["cut", "fade"])
    visual_base: str = "cinematic vertical, high contrast, no text overlays"
    aspect_ratio: str = "9:16"


STYLE_PROFILES: dict[str, StyleProfile] = {
    "tiktok_viral": StyleProfile(
        hook_max_seconds=1.8,
        total_duration=(15, 60),
        cut_speed="ultra_rapido",
        subtitle_style="bold_animated",
        music_volume=0.15,
        transitions=["whip", "zoom_cut", "cut"],
        visual_base="cinematic vertical, high contrast, dramatic lighting, premium depth, no text overlays",
    ),
    "youtube_shorts": StyleProfile(
        hook_max_seconds=2.0,
        total_duration=(30, 58),
        cut_speed="rapido",
        subtitle_style="clean_modern",
        music_volume=0.12,
        transitions=["fade", "cut"],
        visual_base="clean cinematic vertical, balanced lighting, professional quality, no text overlays",
    ),
    "reels_aesthetic": StyleProfile(
        hook_max_seconds=2.0,
        total_duration=(15, 90),
        cut_speed="mixto",
        subtitle_style="minimal_elegant",
        music_volume=0.14,
        transitions=["dissolve", "fade", "cut"],
        visual_base="aesthetic editorial vertical, soft lighting, warm tones, aspirational, no text overlays",
    ),
    "facebook_engage": StyleProfile(
        hook_max_seconds=3.0,
        total_duration=(60, 120),
        cut_speed="cinematografico",
        subtitle_style="readable_large",
        music_volume=0.10,
        transitions=["dissolve", "fade"],
        visual_base="warm editorial vertical, natural lighting, approachable, no text overlays",
    ),
}


def get_style_for_platform(plataforma: str) -> StyleProfile:
    """Map nicho.plataforma to a StyleProfile."""
    mapping = {
        "tiktok": "tiktok_viral",
        "tiktok_reels": "tiktok_viral",
        "reels": "reels_aesthetic",
        "shorts": "youtube_shorts",
        "facebook": "facebook_engage",
    }
    key = mapping.get(plataforma.lower(), "tiktok_viral")
    return STYLE_PROFILES[key]


# ---------------------------------------------------------------------------
# Character Profile — for visual consistency
# ---------------------------------------------------------------------------

class CharacterProfile(BaseModel):
    """Describes a visual character/element that must stay consistent."""

    role: str = ""            # "narrator", "protagonist", "object"
    appearance: str = ""      # "short dark hair, hoodie, glasses"
    visual_style: str = ""    # "warm cinematic lighting, earth tones"


# ---------------------------------------------------------------------------
# Scene Blueprint — single scene in the video
# ---------------------------------------------------------------------------

class SceneBlueprint(BaseModel):
    """One scene in the video — produced by SceneAgent."""

    scene_number: int = 0
    text: str = ""                    # Narration text for this scene
    visual_prompt: str = ""           # Enhanced prompt for Veo/image gen
    mood: str = "neutral"             # "tense", "revelatory", "inspiring", etc.
    duration_seconds: float = 2.5     # Target duration
    camera_notes: str = ""            # "slow zoom in", "static", "pan left"
    transition_in: str = "cut"        # How we enter this scene
    transition_out: str = "cut"       # How we exit to next scene


# ---------------------------------------------------------------------------
# Research Brief — output of ResearchAgent
# ---------------------------------------------------------------------------

class ResearchBrief(BaseModel):
    """Research output that feeds into script generation."""

    trending_topics: list[str] = Field(default_factory=list)
    recommended_angles: list[str] = Field(default_factory=list)
    avoid_topics: list[str] = Field(default_factory=list)   # Already covered
    hook_suggestions: list[str] = Field(default_factory=list)
    audience_insight: str = ""
    trending_context_raw: str = ""     # For backward compat with V14 prompts
    web_sources: list[str] = Field(default_factory=list)
    reference_signals: list[str] = Field(default_factory=list)
    precedence_rule: str = "RESEARCH > NICHO_DEFAULT"


# ---------------------------------------------------------------------------
# StoryState — THE GLOBAL BRAIN
# ---------------------------------------------------------------------------

class StoryState(BaseModel):
    """Global narrative state shared by all agents.

    This is the single most important model in V15.
    Every agent reads from it and writes back to it,
    ensuring coherence across the entire video.
    """

    # --- Identity ---
    topic: str = ""
    tone: str = ""
    audience: str = ""                         # "hombres 18-35, interesados en dinero"
    platform: str = "tiktok"
    nicho_slug: str = ""
    manual_ideas: list[str] = Field(default_factory=list)
    niche_memory_entries: list[str] = Field(default_factory=list)

    # --- Research ---
    research: ResearchBrief = Field(default_factory=ResearchBrief)

    # --- Narrative ---
    hook: str = ""
    hook_variants: list[str] = Field(default_factory=list)
    script_full: str = ""                      # Full narration text
    narrative_arc: str = "tension → revelation → payoff"
    key_points: list[str] = Field(default_factory=list)
    cta: str = ""
    caption: str = ""

    # --- Scenes ---
    scenes: list[SceneBlueprint] = Field(default_factory=list)

    # --- Visual Coherence ---
    characters: list[CharacterProfile] = Field(default_factory=list)
    style_profile: StyleProfile = Field(default_factory=StyleProfile)
    visual_direction: str = ""                 # Global visual style string
    color_palette: str = ""                    # "warm sepia, emerald accents"
    reference_url: str = ""
    reference_title: str = ""
    reference_summary: str = ""
    reference_key_points: list[str] = Field(default_factory=list)
    reference_delivery_promise: str = ""
    reference_hook_seconds: float = 0.0
    reference_avg_cut_seconds: float = 0.0
    reference_video_available: bool = False
    precedence_rule: str = "RESEARCH > NICHO_DEFAULT"

    # --- Scores (from ReviewerAgent / QualityGate) ---
    hook_score: float = 0
    script_score: float = 0
    coherence_score: float = 0
    overall_score: float = 0

    # --- Metadata ---
    feedback_iterations: int = 0
    revision_notes: list[str] = Field(default_factory=list)

    def total_duration(self) -> float:
        """Sum of all scene durations."""
        return sum(s.duration_seconds for s in self.scenes)

    def scene_texts_joined(self) -> str:
        """Full narration from scene texts (for TTS)."""
        return " ".join(s.text for s in self.scenes if s.text)

    def visual_prompts(self) -> list[str]:
        """All scene visual prompts (for Veo/image gen)."""
        return [s.visual_prompt for s in self.scenes if s.visual_prompt]

    def has_reference(self) -> bool:
        """Whether a reference context was attached to this story."""
        return bool(self.reference_url and (self.reference_summary or self.reference_key_points))

    def to_context_string(self) -> str:
        """Compact string for LLM context injection."""
        chars = "; ".join(
            f"{c.role}: {c.appearance}" for c in self.characters
        ) if self.characters else "No specific characters"

        manual = " | ".join(self.manual_ideas[:6]) if self.manual_ideas else "N/A"
        niche_memory = " | ".join(self.niche_memory_entries[:6]) if self.niche_memory_entries else "N/A"

        return (
            f"TOPIC: {self.topic}\n"
            f"TONE: {self.tone}\n"
            f"PLATFORM: {self.platform}\n"
            f"AUDIENCE: {self.audience}\n"
            f"MANUAL IDEAS PRIORITY: {manual}\n"
            f"NICHE MEMORY NOTES: {niche_memory}\n"
            f"NARRATIVE ARC: {self.narrative_arc}\n"
            f"KEY POINTS: {', '.join(self.key_points)}\n"
            f"CHARACTERS: {chars}\n"
            f"VISUAL STYLE: {self.visual_direction}\n"
            f"COLOR PALETTE: {self.color_palette}\n"
            f"PRECEDENCE RULE: {self.precedence_rule}\n"
            f"REFERENCE URL: {self.reference_url or 'N/A'}\n"
            f"REFERENCE TITLE: {self.reference_title or 'N/A'}\n"
            f"REFERENCE SUMMARY: {self.reference_summary[:240] if self.reference_summary else 'N/A'}\n"
            f"REFERENCE PROMISE: {self.reference_delivery_promise or 'N/A'}\n"
            f"REFERENCE HOOK SEC: {self.reference_hook_seconds or 0:.2f}\n"
            f"REFERENCE AVG CUT SEC: {self.reference_avg_cut_seconds or 0:.2f}\n"
        )
