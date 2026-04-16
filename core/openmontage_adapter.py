"""OpenMontage Adapter — importlib-based bridge to OM's visual engine.

Imports OM modules cleanly via importlib (no sys.path contamination).
Delegates to real OM libs — never duplicates their logic.

Exposed capabilities:
  - Media profiles (format → resolution/codec/fps)
  - Slideshow risk scoring (6-dimensional)
  - Shot prompt building (5-layer cinematography prompts)
  - Playbook generation (visual style systems)
  - FFmpeg output args
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from loguru import logger

from models.scene_plan_model import VideoFormat, get_format_spec


# ─────────────────────────────────────────────────────────────
# MODULE LOADER — import limpio sin sys.path
# ─────────────────────────────────────────────────────────────

def _import_module_from_path(module_name: str, file_path: Path) -> Optional[ModuleType]:
    """Import a module from an absolute file path using importlib.

    This avoids contaminating sys.path — each module is loaded in isolation.
    Returns None if the import fails.
    """
    if not file_path.exists():
        logger.debug(f"OpenMontageAdapter: module file not found: {file_path}")
        return None

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # Register in sys.modules so internal relative imports can work
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.debug(f"OpenMontageAdapter: failed to import {module_name}: {exc}")
        return None


class OpenMontageAdapter:
    """Adaptador limpio a OpenMontage. No duplica lógica — delega.

    Carga módulos de OpenMontage-main/lib/ via importlib:
      - media_profiles: formatos y resoluciones por plataforma
      - slideshow_risk: scoring de riesgo slideshow (6 dimensiones)
      - shot_prompt_builder: prompts cinematográficos por capas
      - playbook_generator: sistema de estilos visuales
    """

    def __init__(self, om_root: Optional[Path] = None) -> None:
        if om_root is None:
            try:
                from config import settings
                om_root = settings.openmontage_root()
            except Exception:
                om_root = Path(__file__).resolve().parent.parent / "OpenMontage-main"

        self._om_root = Path(om_root).resolve()
        self._lib_dir = self._om_root / "lib"

        # Load OM modules lazily
        self._media_profiles: Optional[ModuleType] = None
        self._slideshow_risk: Optional[ModuleType] = None
        self._shot_prompt: Optional[ModuleType] = None
        self._playbook_gen: Optional[ModuleType] = None
        self._scoring: Optional[ModuleType] = None

        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load all OM modules on first use."""
        if self._loaded:
            return
        self._loaded = True

        self._media_profiles = _import_module_from_path(
            "om_media_profiles",
            self._lib_dir / "media_profiles.py",
        )
        self._slideshow_risk = _import_module_from_path(
            "om_slideshow_risk",
            self._lib_dir / "slideshow_risk.py",
        )
        self._shot_prompt = _import_module_from_path(
            "om_shot_prompt_builder",
            self._lib_dir / "shot_prompt_builder.py",
        )
        self._playbook_gen = _import_module_from_path(
            "om_playbook_generator",
            self._lib_dir / "playbook_generator.py",
        )
        self._scoring = _import_module_from_path(
            "om_scoring_engine",
            self._lib_dir / "scoring.py",
        )

        loaded = sum(1 for m in [
            self._media_profiles, self._slideshow_risk,
            self._shot_prompt, self._playbook_gen, self._scoring,
        ] if m is not None)
        logger.info(f"OpenMontageAdapter: {loaded}/5 modules loaded from {self._lib_dir}")

    # ── Media Profiles ─────────────────────────────────────────

    def get_media_profile(self, format: VideoFormat | str) -> Optional[Any]:
        """Get OM MediaProfile for a video format.

        Maps our VideoFormat enum to OM's profile names.
        """
        self._ensure_loaded()
        if self._media_profiles is None:
            return None

        format_to_profile = {
            "vertical": "youtube_shorts",
            "horizontal": "youtube_landscape",
            "square": "instagram_feed",
        }
        fmt_str = format.value if isinstance(format, VideoFormat) else format
        profile_name = format_to_profile.get(fmt_str, "youtube_shorts")

        try:
            return self._media_profiles.get_profile(profile_name)
        except (ValueError, AttributeError):
            return None

    def get_ffmpeg_output_args(self, format: VideoFormat | str) -> list[str]:
        """Get FFmpeg output arguments from OM's MediaProfile."""
        self._ensure_loaded()
        profile = self.get_media_profile(format)
        if profile is not None and self._media_profiles is not None:
            try:
                return self._media_profiles.ffmpeg_output_args(profile)
            except Exception:
                pass

        # Fallback: generate from our own FORMAT_SPECS
        fmt_str = format.value if isinstance(format, VideoFormat) else format
        spec = get_format_spec(fmt_str)
        return [
            "-c:v", "libx264",
            "-c:a", "aac",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-vf", f"scale={spec['w']}:{spec['h']}",
        ]

    # ── Slideshow Risk ─────────────────────────────────────────

    def score_slideshow_risk(
        self,
        scenes: list[dict[str, Any]],
        edit_decisions: Optional[dict] = None,
        renderer_family: Optional[str] = None,
    ) -> dict[str, Any]:
        """Score slideshow risk using OM's 6-dimensional analysis.

        Falls back to a simple local heuristic if OM module is unavailable.
        """
        self._ensure_loaded()
        if self._slideshow_risk is not None:
            try:
                return self._slideshow_risk.score_slideshow_risk(
                    scenes, edit_decisions, renderer_family
                )
            except Exception as exc:
                logger.debug(f"OM slideshow_risk failed: {exc}")

        # Local fallback: basic analysis
        return self._fallback_slideshow_risk(scenes)

    def _fallback_slideshow_risk(self, scenes: list[dict]) -> dict[str, Any]:
        """Simple slideshow risk when OM module is unavailable."""
        if not scenes:
            return {"average": 5.0, "verdict": "fail", "dimensions": {}}

        # Check motion variety
        motions = [s.get("motion", "static") for s in scenes]
        static_count = sum(1 for m in motions if m in ("static", "unspecified", ""))
        static_ratio = static_count / max(len(scenes), 1)

        # Check shot variety
        from collections import Counter
        shot_types = [s.get("shot_type", "medium") for s in scenes]
        most_common_ratio = Counter(shot_types).most_common(1)[0][1] / max(len(scenes), 1)

        score = 0.0
        if static_ratio > 0.6:
            score += 2.0
        if most_common_ratio > 0.6:
            score += 1.5

        if score < 2.0:
            verdict = "strong"
        elif score < 3.0:
            verdict = "acceptable"
        elif score < 4.0:
            verdict = "revise"
        else:
            verdict = "fail"

        return {"average": round(score, 2), "verdict": verdict, "dimensions": {}}

    # ── Shot Prompt Builder ─────────────────────────────────────

    def build_shot_prompt(
        self,
        scene: dict[str, Any],
        style_context: Optional[dict[str, Any]] = None,
    ) -> str:
        """Build cinematographic shot prompt using OM's 5-layer framework.

        Falls back to simple concatenation if OM module is unavailable.
        """
        self._ensure_loaded()
        if self._shot_prompt is not None:
            try:
                return self._shot_prompt.build_shot_prompt(scene, style_context)
            except Exception as exc:
                logger.debug(f"OM shot_prompt_builder failed: {exc}")

        # Fallback: simple prompt
        parts = []
        if scene.get("shot_type"):
            parts.append(f"{scene['shot_type']} shot")
        if scene.get("media") or scene.get("description"):
            parts.append(scene.get("media") or scene.get("description", ""))
        if scene.get("motion"):
            parts.append(f"{scene['motion']} motion")
        if scene.get("estilo") or scene.get("emotion"):
            parts.append(scene.get("estilo") or scene.get("emotion", ""))
        return ". ".join(filter(None, parts))

    def build_batch_prompts(
        self,
        scenes: list[dict[str, Any]],
        style_context: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, str]]:
        """Build prompts for all scenes."""
        self._ensure_loaded()
        if self._shot_prompt is not None:
            try:
                return self._shot_prompt.build_batch_prompts(scenes, style_context)
            except Exception:
                pass

        return [
            {"scene_id": str(i), "prompt": self.build_shot_prompt(s, style_context)}
            for i, s in enumerate(scenes)
        ]

    # ── Playbook Generator ─────────────────────────────────────

    def generate_playbook(
        self,
        name: str,
        context: dict[str, Any],
        base_playbook: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Generate a visual style playbook using OM."""
        self._ensure_loaded()
        if self._playbook_gen is not None:
            try:
                return self._playbook_gen.generate_playbook(
                    name, context, base_playbook
                )
            except Exception as exc:
                logger.debug(f"OM playbook_generator failed: {exc}")

        return None

    def list_playbooks(self) -> list[str]:
        """List available playbook names."""
        self._ensure_loaded()
        if self._playbook_gen is not None:
            try:
                return self._playbook_gen.list_playbooks()
            except Exception:
                pass
        return []

    # ── Utility ─────────────────────────────────────────────────

    def get_ffmpeg_scale_filter(self, fmt: str = "vertical") -> str:
        """Return FFmpeg scale filter string for a format."""
        spec = get_format_spec(fmt)
        return (
            f"scale={spec['w']}:{spec['h']}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={spec['w']}:{spec['h']}:(ow-iw)/2:(oh-ih)/2"
        )

    def get_ffmpeg_crop_filter(self, fmt: str = "vertical") -> str:
        """Return FFmpeg crop filter for a format's aspect ratio."""
        spec = get_format_spec(fmt)
        return (
            f"crop='{spec['crop_expr']}':'{spec['crop_expr_h']}':"
            f"'(iw-ow)/2':'(ih-oh)/2'"
        )

    @property
    def is_available(self) -> bool:
        """True if at least 1 OM module loaded successfully."""
        self._ensure_loaded()
        return any(m is not None for m in [
            self._media_profiles, self._slideshow_risk,
            self._shot_prompt, self._playbook_gen,
        ])
