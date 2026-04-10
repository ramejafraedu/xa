"""Gemini Control Plane — centralized provider coordination for V16.

This module emits stage-level provider decisions so pipeline stages can share
one deterministic policy for TTS, image, and music routing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from config import settings
from core.provider_decision_maker import ProviderDecisionMaker, QualityTier


_TTS_ALL = ["piper", "edge-tts", "gemini", "google_tts", "elevenlabs"]
_IMAGE_ALL = ["pexels", "pixabay", "pollinations", "leonardo", "gemini-2.5-flash", "gemini-1.5-pro"]
_MUSIC_ALL = ["jamendo", "pixabay", "lyria", "suno", "gemini-audio"]
_STOCK_ALL = ["pexels", "pixabay", "coverr"]


@dataclass
class ControlDecision:
    """One control-plane decision for a stage/provider family."""

    stage: str
    selected_provider: str
    provider_order: list[str] = field(default_factory=list)
    reason: str = ""
    quality_tier: str = "balanced"
    estimated_cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "label": f"ControlPlane selected {self.selected_provider}",
            "detail": self.reason,
            "severity": "info",
            "metadata": {
                "control_plane": "gemini",
                "quality_tier": self.quality_tier,
                "provider_order": list(self.provider_order),
                "estimated_cost_usd": round(float(self.estimated_cost_usd), 4),
                **self.metadata,
            },
        }


class GeminiControlPlane:
    """Builds deterministic provider decisions for media and narration."""

    def __init__(self) -> None:
        self._maker = ProviderDecisionMaker(
            {
                "quality_tier": str(settings.gemini_control_plane_quality_default or "pro"),
            }
        )

    def _quality_tier(self, execution_mode: str = "") -> QualityTier:
        mode = str(execution_mode or settings.execution_mode_label()).strip().lower()
        configured = str(settings.gemini_control_plane_quality_default or "balanced").strip().lower()

        if mode in {"free", "freemium"}:
            return QualityTier.BUDGET
        if getattr(settings, "v15_strict_free_media_tools", False) or execution_mode == "free":
            return QualityTier.FREE
            
        # V16 PRO: Force Gemini models to be the default orchestrators and providers if PRO
        if execution_mode == "pro":
            return QualityTier.PRO
            
        if configured == "pro":
            return QualityTier.PRO
        if configured == QualityTier.PREMIUM.value:
            return QualityTier.PREMIUM
        if configured == QualityTier.BUDGET.value:
            return QualityTier.BUDGET
        return QualityTier.BALANCED

    @staticmethod
    def _normalize_provider_alias(provider: str, stage: str) -> str:
        name = str(provider or "").strip().lower()
        if stage == "tts":
            if name == "edge_tts":
                return "edge-tts"
            if name == "piper_tts":
                return "piper"
        if stage == "image":
            if name == "leonardo_ai":
                return "leonardo"
        if stage == "music":
            if name == "suno_api":
                return "suno"
            if name == "epidemic_sound":
                return "jamendo"
        return name

    @staticmethod
    def _merge_order(preferred: list[str], universe: list[str], usage: str) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()

        for candidate in list(preferred) + list(universe):
            provider = str(candidate or "").strip().lower()
            if not provider or provider in seen:
                continue
            if not settings.provider_allowed(provider, usage=usage):
                continue
            seen.add(provider)
            merged.append(provider)

        if merged:
            return merged

        # Safety net in case policy excludes everything unexpectedly.
        fallback = [p for p in universe if settings.provider_allowed(p, usage=usage)]
        return fallback or list(universe)

    def plan_media(
        self,
        script_text: str,
        execution_mode: str,
        image_count: int,
        music_count: int = 1,
    ) -> dict[str, Any]:
        """Return centralized provider decisions for assets + TTS."""
        tier = self._quality_tier(execution_mode)

        tts_decision = self._maker.select_tts(script_text, quality=tier.value)
        image_decision = self._maker.select_images(image_count, quality=tier.value)
        music_decision = self._maker.select_music(music_count, quality=tier.value)

        tts_selected = self._normalize_provider_alias(tts_decision.provider_name, "tts")
        img_selected = self._normalize_provider_alias(image_decision.provider_name, "image")
        mus_selected = self._normalize_provider_alias(music_decision.provider_name, "music")

        tts_fallbacks = [
            self._normalize_provider_alias(x, "tts")
            for x in (tts_decision.fallback_providers or [])
        ]
        img_fallbacks = [
            self._normalize_provider_alias(x, "image")
            for x in (image_decision.fallback_providers or [])
        ]
        mus_fallbacks = [
            self._normalize_provider_alias(x, "music")
            for x in (music_decision.fallback_providers or [])
        ]

        tts_order = self._merge_order([tts_selected] + tts_fallbacks, _TTS_ALL, usage="media")
        image_order = self._merge_order([img_selected] + img_fallbacks, _IMAGE_ALL, usage="media")
        music_order = self._merge_order([mus_selected] + mus_fallbacks, _MUSIC_ALL, usage="media")
        stock_order = self._merge_order([img_selected, "pexels", "pixabay"], _STOCK_ALL, usage="media")

        decisions = [
            ControlDecision(
                stage="tts",
                selected_provider=tts_order[0],
                provider_order=tts_order,
                reason=tts_decision.reason,
                quality_tier=tier.value,
                estimated_cost_usd=tts_decision.cost_usd,
            ),
            ControlDecision(
                stage="image",
                selected_provider=image_order[0],
                provider_order=image_order,
                reason=image_decision.reason,
                quality_tier=tier.value,
                estimated_cost_usd=image_decision.cost_usd,
                metadata={"image_count": int(image_count)},
            ),
            ControlDecision(
                stage="music",
                selected_provider=music_order[0],
                provider_order=music_order,
                reason=music_decision.reason,
                quality_tier=tier.value,
                estimated_cost_usd=music_decision.cost_usd,
            ),
            ControlDecision(
                stage="stock_video",
                selected_provider=stock_order[0],
                provider_order=stock_order,
                reason="Stock order aligned with image provider strategy",
                quality_tier=tier.value,
            ),
        ]

        return {
            "quality_tier": tier.value,
            "provider_order_tts": tts_order,
            "provider_order_image_generation": image_order,
            "provider_order_music_generation": music_order,
            "provider_order_stock_video": stock_order,
            "estimated_cost_usd": round(
                float(tts_decision.cost_usd + image_decision.cost_usd + music_decision.cost_usd),
                4,
            ),
            "decisions": [d.to_event() for d in decisions],
        }


_control_plane: Optional[GeminiControlPlane] = None


def get_gemini_control_plane() -> GeminiControlPlane:
    global _control_plane
    if _control_plane is None:
        _control_plane = GeminiControlPlane()
    return _control_plane
