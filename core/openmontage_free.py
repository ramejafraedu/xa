"""OpenMontage free-tools adapter for Video Factory V15.

This module provides safe, optional integration points with the local
OpenMontage workspace. All imports are lazy and failure-tolerant so the
pipeline keeps running when optional dependencies are missing.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import settings


def _om_enabled() -> bool:
    return bool(settings.enable_openmontage_free_tools)


def _om_root() -> Path:
    return settings.openmontage_root()


def _ensure_openmontage_path() -> bool:
    """Ensure OpenMontage root is first on sys.path for tools.* imports."""
    if not _om_enabled():
        return False

    root = _om_root()
    if not root.exists():
        logger.debug(f"OpenMontage root not found: {root}")
        return False

    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return True


def _import_module(module_name: str):
    if not _ensure_openmontage_path():
        return None
    try:
        return importlib.import_module(module_name)
    except Exception as exc:
        logger.debug(f"OpenMontage import failed ({module_name}): {exc}")
        return None


def _load_module_from_file(module_alias: str, file_path: Path):
    if not file_path.exists():
        return None

    spec = importlib.util.spec_from_file_location(module_alias, str(file_path))
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        logger.debug(f"OpenMontage file-module import failed ({file_path.name}): {exc}")
        return None


def _run_tool(module_name: str, class_name: str, inputs: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Run a BaseTool-compatible OpenMontage tool safely."""
    module = _import_module(module_name)
    if module is None:
        return None

    tool_cls = getattr(module, class_name, None)
    if tool_cls is None:
        logger.debug(f"OpenMontage class not found: {module_name}.{class_name}")
        return None

    try:
        tool = tool_cls()
        result = tool.execute(inputs)
    except Exception as exc:
        logger.debug(f"OpenMontage tool execution failed ({class_name}): {exc}")
        return None

    success = bool(getattr(result, "success", False))
    if not success:
        return None

    data = getattr(result, "data", {}) or {}
    artifacts = getattr(result, "artifacts", []) or []
    return {
        "data": data,
        "artifacts": artifacts,
        "duration_seconds": float(getattr(result, "duration_seconds", 0.0) or 0.0),
    }


def strict_free_candidates(candidates: list[str], usage: str) -> list[str]:
    """Filter providers to free tier when strict-free media policy is enabled."""
    if not settings.v15_strict_free_media_tools:
        return candidates

    usage_key = (usage or "").strip().lower()
    if usage_key not in {"media", "analysis", "subtitle", "enhancement", "video_tools", "render"}:
        return candidates

    allowed = [p for p in candidates if settings.provider_tier(p) == "free"]
    return allowed


def load_style_playbook(playbook_name: str) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
    """Load and validate style playbook from OpenMontage styles/.

    Returns (playbook, issues). On failures returns (None, []).
    """
    if not (_om_enabled() and settings.openmontage_enable_styles):
        return None, []

    module = _load_module_from_file(
        module_alias="openmontage_playbook_loader",
        file_path=_om_root() / "styles" / "playbook_loader.py",
    )
    if module is None:
        return None, []

    load_playbook = getattr(module, "load_playbook", None)
    validate_palette = getattr(module, "validate_palette", None)
    if load_playbook is None:
        return None, []

    try:
        playbook = load_playbook(playbook_name, styles_dir=_om_root() / "styles")
        issues = validate_palette(playbook) if callable(validate_palette) else []
        return playbook, issues or []
    except Exception as exc:
        logger.debug(f"OpenMontage playbook load failed ({playbook_name}): {exc}")
        return None, []


def apply_playbook_to_story(story: Any, playbook_name: str) -> tuple[str, list[str]]:
    """Apply OpenMontage playbook values into StoryState in a tolerant way."""
    playbook, issues = load_style_playbook(playbook_name)
    if not playbook:
        return "", []

    visual = playbook.get("visual_language", {}) or {}
    motion = playbook.get("motion", {}) or {}
    audio = playbook.get("audio", {}) or {}
    identity = playbook.get("identity", {}) or {}
    palette = (visual.get("color_palette", {}) or {})

    pace = str(identity.get("pace", "")).strip().lower()
    cut_map = {
        "fast": "ultra_rapido",
        "moderate": "mixto",
        "slow": "cinematografico",
    }
    cut_speed = cut_map.get(pace)

    try:
        transitions = [str(x) for x in (motion.get("transitions") or []) if str(x).strip()]
        if transitions:
            story.style_profile.transitions = transitions

        if cut_speed:
            story.style_profile.cut_speed = cut_speed

        subtitle_style = str(motion.get("animation_style", "")).strip() or "bold_animated"
        story.style_profile.subtitle_style = subtitle_style

        music_volume = audio.get("music_volume")
        if isinstance(music_volume, (int, float)):
            story.style_profile.music_volume = float(music_volume)

        composition = str(visual.get("composition", "")).strip()
        texture = str(visual.get("texture", "")).strip()
        visual_parts = [p for p in [composition, texture] if p]
        if visual_parts:
            story.style_profile.visual_base = ", ".join(visual_parts)
            story.visual_direction = story.style_profile.visual_base

        primary = palette.get("primary") or []
        accent = palette.get("accent") or []
        bg = palette.get("background")
        text = palette.get("text")
        color_tokens: list[str] = []
        for seq in [primary, accent]:
            if isinstance(seq, list):
                color_tokens.extend(str(c) for c in seq if str(c).strip())
        if bg:
            color_tokens.append(str(bg))
        if text:
            color_tokens.append(str(text))
        if color_tokens:
            story.color_palette = ", ".join(color_tokens[:8])
    except Exception as exc:
        logger.debug(f"OpenMontage playbook application failed: {exc}")

    issue_messages = [str(i.get("message", "")) for i in issues if isinstance(i, dict)]
    return str(playbook_name), [m for m in issue_messages if m]


def generate_vtt_from_audio(audio_path: Path, output_dir: Path, language: str = "es") -> Optional[Path]:
    """Generate VTT from audio via OpenMontage Transcriber + SubtitleGen."""
    if not (_om_enabled() and settings.openmontage_enable_subtitle):
        return None
    if not audio_path.exists():
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    transcribed = _run_tool(
        module_name="tools.analysis.transcriber",
        class_name="Transcriber",
        inputs={
            "input_path": str(audio_path),
            "model_size": "base",
            "language": language,
            "output_dir": str(output_dir),
        },
    )
    if not transcribed:
        return None

    segments = (transcribed.get("data") or {}).get("segments")
    if not isinstance(segments, list) or not segments:
        return None

    vtt_path = output_dir / f"{audio_path.stem}_om.vtt"
    subtitles = _run_tool(
        module_name="tools.subtitle.subtitle_gen",
        class_name="SubtitleGen",
        inputs={
            "segments": segments,
            "format": "vtt",
            "output_path": str(vtt_path),
            "max_words_per_cue": 4,
            "highlight_style": "word_by_word",
        },
    )
    if not subtitles:
        return None

    if vtt_path.exists() and vtt_path.stat().st_size > 20:
        return vtt_path
    return None


def run_audio_probe(media_path: Path) -> Optional[dict[str, Any]]:
    if not (_om_enabled() and settings.openmontage_enable_analysis):
        return None
    if not media_path.exists():
        return None

    result = _run_tool(
        module_name="tools.analysis.audio_probe",
        class_name="AudioProbe",
        inputs={"input_path": str(media_path)},
    )
    return result.get("data") if result else None


def run_visual_probe(video_path: Path, expected: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    if not (_om_enabled() and settings.openmontage_enable_analysis):
        return None
    if not video_path.exists():
        return None

    payload = {
        "operation": "probe",
        "input_path": str(video_path),
        "expected": expected or {},
    }
    result = _run_tool(
        module_name="tools.analysis.visual_qa",
        class_name="VisualQA",
        inputs=payload,
    )
    return result.get("data") if result else None


def run_frame_sampler(video_path: Path, output_dir: Path, count: int = 3) -> Optional[dict[str, Any]]:
    """Extract representative frames using OpenMontage frame_sampler."""
    if not (_om_enabled() and settings.openmontage_enable_analysis):
        return None
    if not video_path.exists():
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    result = _run_tool(
        module_name="tools.analysis.frame_sampler",
        class_name="FrameSampler",
        inputs={
            "input_path": str(video_path),
            "strategy": "count",
            "count": max(1, int(count)),
            "output_dir": str(output_dir),
            "format": "jpg",
        },
    )
    return result.get("data") if result else None


def run_composition_validator(composition_path: Path, assets_root: Optional[Path] = None) -> Optional[dict[str, Any]]:
    if not (_om_enabled() and settings.openmontage_enable_analysis):
        return None
    if not composition_path.exists():
        return None

    payload: dict[str, Any] = {"composition_path": str(composition_path)}
    if assets_root:
        payload["assets_root"] = str(assets_root)

    result = _run_tool(
        module_name="tools.analysis.composition_validator",
        class_name="CompositionValidator",
        inputs=payload,
    )
    return result.get("data") if result else None


def apply_color_grade(
    input_path: Path | str,
    output_path: Path | str,
    profile: str = "cinematic_warm",
) -> Path | None:
    """Apply color grade to image/video using OpenMontage."""
    if not _ensure_openmontage_path():
        return None

    try:
        from tools.enhancement.color_grade import ColorGrade

        tool = ColorGrade()
        result = tool.run(
            input_path=str(input_path),
            output_path=str(output_path),
            profile=profile,
        )

        if result.success:
            return Path(result.data.get("output_path", output_path))
        else:
            logger.warning(f"ColorGrade failed: {exc}")
            return None
    except Exception as exc:
        logger.warning(f"ColorGrade failed: {exc}")
        return None


def apply_audio_enhance(
    input_path: Path | str,
    output_path: Path | str,
    preset: str = "clean_speech",
) -> Path | None:
    """Enhance audio (noise reduction, EQ, normalization) using OpenMontage."""
    if not _ensure_openmontage_path():
        return None

    try:
        from tools.audio.audio_enhance import AudioEnhance

        tool = AudioEnhance()
        result = tool.run(
            input_path=str(input_path),
            output_path=str(output_path),
            preset=preset,
        )

        if result.success:
            return Path(result.data.get("output_path", output_path))
        else:
            logger.warning(f"AudioEnhance returned failure: {result.error}")
            return None
    except Exception as exc:
        logger.warning(f"AudioEnhance failed: {exc}")
        return None


def apply_silence_cutter(
    input_path: Path | str,
    output_path: Path | str,
    mode: str = "remove",
    silence_threshold_db: float = -35.0,
    min_silence_duration: float = 0.5,
) -> Path | None:
    """Cut or speed up silences in audio/video using OpenMontage."""
    if not _ensure_openmontage_path():
        return None

    try:
        from tools.video.silence_cutter import SilenceCutter

        tool = SilenceCutter()
        result = tool.run(
            input_path=str(input_path),
            output_path=str(output_path),
            mode=mode,
            silence_threshold_db=silence_threshold_db,
            min_silence_duration=min_silence_duration,
        )

        if result.success:
            return Path(result.data.get("output_path", output_path))
        else:
            logger.warning(f"SilenceCutter returned failure: {result.error}")
            return None
    except Exception as exc:
        logger.warning(f"SilenceCutter failed: {exc}")
        return None


def apply_auto_reframe(
    input_path: Path,
    output_path: Path,
    target_aspect: str = "portrait",
) -> Optional[Path]:
    """Apply OpenMontage auto_reframe for aspect conversions."""
    if not (_om_enabled() and settings.openmontage_enable_video_utilities):
        return None
    if not input_path.exists():
        return None

    result = _run_tool(
        module_name="tools.video.auto_reframe",
        class_name="AutoReframe",
        inputs={
            "input_path": str(input_path),
            "output_path": str(output_path),
            "target_aspect": target_aspect,
        },
    )
    if not result:
        return None

    out = Path(str((result.get("data") or {}).get("output", "") or ""))
    if out.exists():
        return out
    return None


def apply_video_trim(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> Optional[Path]:
    """Apply OpenMontage video_trimmer cut operation."""
    if not (_om_enabled() and settings.openmontage_enable_video_utilities):
        return None
    if not input_path.exists():
        return None

    result = _run_tool(
        module_name="tools.video.video_trimmer",
        class_name="VideoTrimmer",
        inputs={
            "operation": "cut",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "start_seconds": max(0.0, float(start_seconds)),
            "end_seconds": max(float(end_seconds), float(start_seconds)),
        },
    )
    if not result:
        return None

    out = Path(str((result.get("data") or {}).get("output", "") or ""))
    if out.exists():
        return out
    return None


def apply_upscale(input_path: Path, output_path: Path, scale: int = 2) -> Optional[Path]:
    """Apply OpenMontage image/video upscale (best effort)."""
    if not (_om_enabled() and settings.openmontage_enable_enhancement):
        return None
    if not input_path.exists():
        return None

    result = _run_tool(
        module_name="tools.enhancement.upscale",
        class_name="Upscale",
        inputs={
            "input_path": str(input_path),
            "output_path": str(output_path),
            "scale": int(scale),
        },
    )
    if not result:
        return None
    out = Path(str((result.get("data") or {}).get("output", "") or ""))
    if out.exists():
        return out
    return None


def apply_bg_remove(input_path: Path, output_path: Path) -> Optional[Path]:
    """Apply OpenMontage background removal for images."""
    if not (_om_enabled() and settings.openmontage_enable_enhancement):
        return None
    if not input_path.exists():
        return None

    result = _run_tool(
        module_name="tools.enhancement.bg_remove",
        class_name="BgRemove",
        inputs={
            "input_path": str(input_path),
            "output_path": str(output_path),
        },
    )
    if not result:
        return None
    out = Path(str((result.get("data") or {}).get("output", "") or ""))
    if out.exists():
        return out
    return None


def apply_face_restore(input_path: Path, output_path: Path) -> Optional[Path]:
    """Apply OpenMontage face restoration for images."""
    if not (_om_enabled() and settings.openmontage_enable_enhancement):
        return None
    if not input_path.exists():
        return None

    result = _run_tool(
        module_name="tools.enhancement.face_restore",
        class_name="FaceRestore",
        inputs={
            "input_path": str(input_path),
            "output_path": str(output_path),
            "model": "CodeFormer",
            "upscale": 2,
        },
    )
    if not result:
        return None
    out = Path(str((result.get("data") or {}).get("output", "") or ""))
    if out.exists():
        return out
    return None
