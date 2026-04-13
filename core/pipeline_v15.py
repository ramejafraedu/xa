"""Video Factory V15 — Pipeline Orchestrator.

Director-based multi-agent pipeline that replaces the monolithic
run_pipeline() function from V14.

Flow:
  1. Research → [checkpoint]
  2. Script (prompt chaining) → [checkpoint]
  3. Quality gate (V14 reuse) → automatic
  4. Scene planning → [checkpoint]
  5. Feedback review → auto-loop or pass
  6. Assets (coherent) → [checkpoint]
  7. TTS + Subtitles
  8. Edit decisions
  9. Render
  10. Post-render QA
  11. Publish + cleanup

MODULE CONTRACT:
  Input:  nicho_slug + DirectorMode
  Output: JobManifest (same as V14 — full backward compat)
"""
from __future__ import annotations

import os
import json
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from agents.asset_agent import AssetAgent
from agents.editor_agent import EditorAgent
from agents.reference_agent import ReferenceAgent
from agents.research_agent import ResearchAgent
from agents.scene_agent import SceneAgent
from agents.script_agent import ScriptAgent
from config import NICHOS, app_config, settings
from core.cost_governance import CostGovernance
from core.director import CheckpointResult, Director, DirectorMode
from core.feedback_loop import review_content, should_iterate
from core.gemini_control_plane import get_gemini_control_plane
from core.openmontage_free import (
    strict_free_candidates,
    apply_playbook_to_story,
    apply_bg_remove,
    apply_upscale,
    apply_face_restore,
    apply_color_grade,
    apply_auto_reframe,
    apply_video_trim,
)
from tools.tool_registry import ToolRegistry
from core.reference_context import load_reference_context
from core.subtopic_manager import get_subtopic_manager
from core.state import StoryState, get_style_for_platform
from tools.video.saar_composer import SaarComposer
from models.content import (
    BlockScores,
    ErrorCode,
    FailureType,
    JobManifest,
    JobStatus,
    VideoContent,
)
from services.niche_memory import get_niche_memory_lines, normalize_manual_ideas
from services.niche_memory import add_niche_memory_entry
from state_manager import StateManager

console = Console()


class _NoopProgress:
    """Fallback progress object for non-interactive environments."""

    def add_task(self, *_args, **_kwargs) -> int:
        return 1

    def update(self, *_args, **_kwargs) -> None:
        return None

    def advance(self, *_args, **_kwargs) -> None:
        return None


def _should_use_live_progress() -> bool:
    """Enable rich live progress only for interactive terminals."""
    if os.getenv("VIDEO_FACTORY_DISABLE_PROGRESS", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return bool(sys.stdout and sys.stdout.isatty())


@contextmanager
def _progress_scope():
    """Use Rich Progress on TTY; otherwise no-op progress to avoid LiveError."""
    if _should_use_live_progress():
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            yield progress
        return

    yield _NoopProgress()


def _build_saar_scene_data(media_paths: list[Path]) -> list[dict[str, str]]:
    """Build paired A/B scene payload for SaarComposer from downloaded clips."""
    normalized_paths = [Path(p) for p in media_paths if p]
    if not normalized_paths:
        return []

    variant_a = [path for idx, path in enumerate(normalized_paths) if idx % 2 == 0]
    variant_b = [path for idx, path in enumerate(normalized_paths) if idx % 2 == 1]
    if not variant_b:
        variant_b = list(variant_a)

    scene_count = max(len(variant_a), len(variant_b))
    scene_data: list[dict[str, str]] = []
    for idx in range(scene_count):
        visual_a = variant_a[idx % len(variant_a)]
        visual_b = variant_b[idx % len(variant_b)]
        scene_data.append(
            {
                "visual_1": str(visual_a),
                "visual_2": str(visual_b),
                "fallback_clip": str(visual_a),
            }
        )
    return scene_data


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _probe_video_metrics(video_path: Path) -> dict[str, float]:
    """Probe key media metrics used to validate Saar candidates."""
    metrics = {
        "format_duration": 0.0,
        "video_duration": 0.0,
        "audio_duration": 0.0,
        "fps": 0.0,
    }
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode != 0 or not result.stdout:
            return metrics

        payload = json.loads(result.stdout)
        metrics["format_duration"] = _safe_float(
            (payload.get("format") or {}).get("duration"),
            0.0,
        )
        for stream in payload.get("streams", []):
            if not isinstance(stream, dict):
                continue
            codec_type = str(stream.get("codec_type") or "").strip().lower()
            if codec_type == "video" and metrics["video_duration"] <= 0:
                metrics["video_duration"] = _safe_float(stream.get("duration"), 0.0)
                fps_raw = str(stream.get("r_frame_rate") or "0/1")
                try:
                    num_s, den_s = fps_raw.split("/", 1)
                    num = float(num_s)
                    den = float(den_s)
                    if den > 0:
                        metrics["fps"] = num / den
                except Exception:
                    metrics["fps"] = 0.0
            elif codec_type == "audio" and metrics["audio_duration"] <= 0:
                metrics["audio_duration"] = _safe_float(stream.get("duration"), 0.0)
    except Exception:
        return metrics

    return metrics


def _select_saar_variant(
    candidate_paths: list[Path],
    expected_duration: float = 0.0,
) -> tuple[Optional[Path], dict]:
    """Select Saar winner using sync and duration quality before size."""
    expected_duration = max(0.0, _safe_float(expected_duration, 0.0))
    ranked: list[dict[str, object]] = []
    for candidate in candidate_paths:
        path = Path(candidate)
        try:
            size_bytes = int(path.stat().st_size)
        except OSError:
            continue
        if size_bytes < 1024:
            continue

        name_upper = path.stem.upper()
        variant = "B" if "VARIANT_B" in name_upper else "A"
        metrics = _probe_video_metrics(path)
        format_duration = _safe_float(metrics.get("format_duration"), 0.0)
        video_duration = _safe_float(metrics.get("video_duration"), 0.0)
        audio_duration = _safe_float(metrics.get("audio_duration"), 0.0)
        fps = _safe_float(metrics.get("fps"), 0.0)

        av_delta_seconds = (
            abs(video_duration - audio_duration)
            if video_duration > 0 and audio_duration > 0
            else 0.0
        )
        duration_reference = format_duration if format_duration > 0 else video_duration
        duration_delta_seconds = (
            abs(duration_reference - expected_duration)
            if duration_reference > 0 and expected_duration > 0
            else 0.0
        )

        fps_valid = fps <= 0.0 or (23.5 <= fps <= 61.0)
        sync_valid = av_delta_seconds <= 0.25 if video_duration > 0 and audio_duration > 0 else True
        duration_valid = (
            duration_delta_seconds <= max(2.0, expected_duration * 0.20)
            if duration_reference > 0 and expected_duration > 0
            else True
        )

        quality_valid = bool(fps_valid and sync_valid and duration_valid)
        quality_score = 0
        quality_score += 3 if sync_valid else -5
        quality_score += 2 if duration_valid else -4
        quality_score += 1 if fps_valid else -2

        ranked.append(
            {
                "variant": variant,
                "path": str(path),
                "size_bytes": size_bytes,
                "format_duration_seconds": round(format_duration, 3),
                "video_duration_seconds": round(video_duration, 3),
                "audio_duration_seconds": round(audio_duration, 3),
                "fps": round(fps, 3),
                "av_delta_seconds": round(av_delta_seconds, 3),
                "duration_delta_seconds": round(duration_delta_seconds, 3),
                "quality_valid": quality_valid,
                "quality_score": quality_score,
            }
        )

    ranked.sort(
        key=lambda item: (
            int(bool(item.get("quality_valid", False))),
            int(item.get("quality_score", -99)),
            -_safe_float(item.get("av_delta_seconds"), 9999.0),
            -_safe_float(item.get("duration_delta_seconds"), 9999.0),
            int(item.get("size_bytes", 0)),
        ),
        reverse=True,
    )

    if not ranked:
        return None, {
            "saar_candidate_count": 0,
            "saar_candidates": [],
            "saar_selection_mode": "quality_score_desc",
            "saar_selection_reason": "no_valid_candidates",
            "saar_selected_variant": "",
            "saar_selected_path": "",
        }

    winner = ranked[0]
    if not bool(winner.get("quality_valid", False)):
        return None, {
            "saar_candidate_count": len(ranked),
            "saar_candidates": ranked,
            "saar_selection_mode": "quality_score_desc",
            "saar_selection_reason": "no_quality_valid_candidates",
            "saar_selected_variant": "",
            "saar_selected_path": "",
        }

    winner_path = Path(str(winner.get("path", "")))
    return winner_path, {
        "saar_candidate_count": len(ranked),
        "saar_candidates": ranked,
        "saar_selection_mode": "quality_score_desc",
        "saar_selection_reason": "winner=quality_score_then_file_size",
        "saar_selected_variant": str(winner.get("variant", "A") or "A"),
        "saar_selected_path": str(winner_path),
    }


def run_pipeline_v15(
    nicho_slug: str,
    mode: DirectorMode = DirectorMode.AUTO,
    dry_run: bool = False,
    resume_job_id: str = "",
    reference_url: str = "",
    manual_ideas: str | list[str] | None = None,
    runtime_overrides: Optional[dict] = None,
) -> JobManifest:
    """Execute the V15 multi-agent pipeline.

    This is the V15 equivalent of V14's run_pipeline().
    Compatible output: returns a JobManifest with full audit trail.

    Args:
        nicho_slug: Niche identifier.
        mode: INTERACTIVE (human checkpoints) or AUTO (autonomous).
        dry_run: If True, stop after script + scene plan.
        resume_job_id: Resume a crashed job.
        manual_ideas: Optional manual direction lines with highest narrative priority.

    Returns:
        JobManifest with full audit trail.
    """
    from pipeline.tts_engine import generate_tts, get_audio_duration
    from pipeline.audio_trim_smart import apply_post_tts_audio_processing
    from pipeline.subtitles_whisperx import generate_subtitles_with_fallback
    from pipeline.quality_gate import validate_and_score
    from pipeline.self_healer import attempt_healing
    from pipeline.renderer import download_clips
    from pipeline.renderer_remotion import (
        render_video_with_fallback,
        build_director_artifacts,
        build_edit_decisions_artifact,
    )
    from pipeline.duration_validator import validate_duration
    from pipeline.pre_render_validator import validate_pre_render, extract_flagged_greenscreen_clips
    from pipeline.cleanup import cleanup_temp, cleanup_stale_temp
    from publishers.telegram import notify_success, notify_error, notify_review
    from publishers.drive_sheets import upload_to_drive, log_to_sheets
    from services.publish_package import build_publish_package
    from services.supabase_client import save_result, save_performance

    nicho = NICHOS.get(nicho_slug)
    if not nicho:
        raise ValueError(f"Unknown niche: {nicho_slug}. Available: {list(NICHOS.keys())}")

    # Initialize
    timestamp = int(time.time() * 1000)
    job_id = f"{nicho_slug}_{timestamp}"
    director = Director(mode, job_id=job_id)
    state_mgr = StateManager(settings.temp_dir)
    
    # Initialize OpenMontage cost tracker
    state_mgr.initialize_cost_tracker(
        budget=float(settings.daily_budget_usd),
        mode_str="warn"
    )

    # V16: Initialize Tool Registry (focused pre/render QA tools)
    registry = ToolRegistry()
    for module_name in (
        "tools.analysis.audio_probe",
        "tools.analysis.composition_validator",
        "tools.analysis.visual_qa",
    ):
        try:
            module = __import__(module_name, fromlist=["*"])
            registry.register_module(module)
        except Exception as exc:
            logger.debug(f"Tool module skipped ({module_name}): {exc}")

    if registry._tools:
        logger.info(f"🔧 Loaded {len(registry._tools)} analysis tools from OpenMontage suite")
    else:
        logger.warning("No OpenMontage analysis tools were registered in ToolRegistry")

    manifest = JobManifest(
        job_id=job_id,
        nicho_slug=nicho_slug,
        timestamp=timestamp,
        plataforma=nicho.plataforma,
        model_version=f"v15_{settings.inference_model}",
    )
    manifest.execution_mode = settings.execution_mode_label()
    manifest.feature_flags = settings.active_feature_flags()
    manifest.pipeline_type = "v15"
    manifest.checkpoint_policy = "guided" if mode == DirectorMode.INTERACTIVE else "auto"
    manifest.human_approval_required = mode == DirectorMode.INTERACTIVE
    manifest.human_approved = mode != DirectorMode.INTERACTIVE
    manifest.budget_daily_usd = float(settings.daily_budget_usd)
    manifest.budget_monthly_usd = float(settings.monthly_budget_usd)
    manual_idea_lines = normalize_manual_ideas(manual_ideas)
    niche_memory_lines = get_niche_memory_lines(nicho_slug, limit=10)
    runtime_overrides = dict(runtime_overrides or {})
    allowed_override_keys = {
        "prefer_stock_images",
        "generated_images_count",
        "media_cache_ttl_days",
        "enable_image_cache",
        "disable_image_cache",
        "gemini_everywhere_mode",
        "remotion_theme",
        "remotion_layout_variant",
        "remotion_kinetic_level",
        "remotion_transition_preset",
        "remotion_feature_card_mode",
        "ab_visual_split_enabled",
        "ab_visual_multiplier",
        "saar_composer_enabled",
        "saar_composer_use_winner",
        "remotion_composition_id",
        "provider_order_stock_video",
        "provider_order_image_generation",
        "provider_order_music_generation",
        "provider_order_tts",
    }
    runtime_overrides = {k: v for k, v in runtime_overrides.items() if k in allowed_override_keys}

    allowed_remotion_compositions = {"CinematicRenderer", "UniversalCommercial"}
    raw_requested_composition = str(
        runtime_overrides.get("remotion_composition_id", settings.remotion_composition_id)
        or "UniversalCommercial"
    ).strip()
    requested_remotion_composition = (
        raw_requested_composition
        if raw_requested_composition in allowed_remotion_compositions
        else "UniversalCommercial"
    )
    if raw_requested_composition and raw_requested_composition not in allowed_remotion_compositions:
        logger.warning(
            "Unknown remotion composition requested ({}). Falling back to UniversalCommercial.",
            raw_requested_composition,
        )
    manifest.feature_flags["remotion_composition_id"] = requested_remotion_composition

    try:
        baseline_ab_multiplier = int(
            runtime_overrides.get("ab_visual_multiplier", settings.ab_visual_split_multiplier)
        )
    except (TypeError, ValueError):
        baseline_ab_multiplier = int(settings.ab_visual_split_multiplier)
    saar_enabled = bool(runtime_overrides.get("saar_composer_enabled", settings.enable_saar_composer))
    saar_use_winner = bool(runtime_overrides.get("saar_composer_use_winner", settings.saar_composer_use_winner))
    manifest.ab_visual_split = {
        "enabled": bool(runtime_overrides.get("ab_visual_split_enabled", settings.enable_ab_visual_split)),
        "multiplier": max(2, min(3, baseline_ab_multiplier)),
        "runtime_override_enabled": "ab_visual_split_enabled" in runtime_overrides,
        "runtime_override_multiplier": "ab_visual_multiplier" in runtime_overrides,
        "saar_enabled": saar_enabled,
        "saar_use_winner": saar_use_winner,
        "runtime_override_saar_enabled": "saar_composer_enabled" in runtime_overrides,
        "runtime_override_saar_use_winner": "saar_composer_use_winner" in runtime_overrides,
        "requested_images_count": runtime_overrides.get("generated_images_count", settings.generated_images_count),
    }
    manifest.feature_flags["enable_saar_composer"] = saar_enabled
    manifest.feature_flags["saar_composer_use_winner"] = saar_use_winner
    manifest.manual_ideas = manual_idea_lines
    manifest.niche_memory_snapshot = niche_memory_lines
    manifest.reference_url = (reference_url or "").strip()
    if manifest.reference_url:
        manifest.reference_notes = "reference_received"

    if manual_idea_lines:
        manifest.reference_notes = (
            f"{manifest.reference_notes}|manual_ideas_received"
            if manifest.reference_notes
            else "manual_ideas_received"
        )

    if "disable_image_cache" in runtime_overrides:
        manifest.feature_flags["disable_image_cache"] = bool(runtime_overrides.get("disable_image_cache"))
    if "enable_image_cache" in runtime_overrides:
        manifest.feature_flags["enable_image_cache"] = bool(runtime_overrides.get("enable_image_cache"))

    precedence_rule = "RESEARCH > NICHO_DEFAULT"
    if manual_idea_lines and manifest.reference_url:
        precedence_rule = "MANUAL_IDEAS > REFERENCE > RESEARCH > NICHO_DEFAULT"
    elif manual_idea_lines:
        precedence_rule = "MANUAL_IDEAS > RESEARCH > NICHO_DEFAULT"
    elif manifest.reference_url:
        precedence_rule = "REFERENCE > RESEARCH > NICHO_DEFAULT"

    cost_governance = CostGovernance(manifest)
    manifest.month_to_date_spend_usd = cost_governance.get_current_month_spend_usd()

    # Build initial StoryState
    story = StoryState(
        topic=nicho.nombre,
        tone=nicho.tono,
        audience=_audience_for_nicho(nicho_slug),
        platform=_resolve_platform(nicho.plataforma),
        nicho_slug=nicho_slug,
        manual_ideas=manual_idea_lines,
        niche_memory_entries=niche_memory_lines,
        visual_direction=nicho.direccion_visual,
        style_profile=get_style_for_platform(nicho.plataforma),
        reference_url=manifest.reference_url,
        precedence_rule=precedence_rule,
    )

    settings.ensure_dirs()
    cleanup_stale_temp()

    # Lightweight observability trail persisted in the manifest.
    stage_clock: dict[str, float] = {}
    pre_render_tool_checks: dict[str, dict] = {}

    def _stage_start(stage_key: str, label: str = "", detail: str = "") -> None:
        stage_clock[stage_key] = time.time()
        manifest.stage_trace.append({
            "stage": stage_key,
            "label": label or stage_key,
            "state": "running",
            "started_at": int(time.time() * 1000),
            "ended_at": 0,
            "elapsed_seconds": 0.0,
            "detail": detail,
            "metadata": {},
        })

    def _stage_end(
        stage_key: str,
        state: str = "completed",
        detail: str = "",
        metadata: Optional[dict] = None,
    ) -> None:
        started = stage_clock.pop(stage_key, None)
        elapsed = round(max(0.0, time.time() - started), 2) if started else 0.0
        if elapsed > 0 and stage_key not in manifest.timings:
            manifest.timings[stage_key] = elapsed

        checkpoint_metadata = dict(metadata or {})
        checkpoint_metadata.setdefault("cost_estimate_usd", round(float(manifest.cost_estimate_usd or 0.0), 4))
        checkpoint_metadata.setdefault("cost_reserved_usd", round(float(manifest.cost_reserved_usd or 0.0), 4))
        checkpoint_metadata.setdefault("cost_actual_usd", round(float(manifest.cost_actual_usd or 0.0), 4))
        checkpoint_metadata.setdefault("error_code", str(manifest.error_code or ""))

        updated_running_entry = False
        for entry in reversed(manifest.stage_trace):
            if entry.get("stage") == stage_key and entry.get("state") == "running":
                entry["state"] = state
                entry["ended_at"] = int(time.time() * 1000)
                entry["elapsed_seconds"] = elapsed
                if detail:
                    entry["detail"] = detail
                entry["metadata"] = checkpoint_metadata
                updated_running_entry = True
                break

        if not updated_running_entry:
            manifest.stage_trace.append({
                "stage": stage_key,
                "label": stage_key,
                "state": state,
                "started_at": 0,
                "ended_at": int(time.time() * 1000),
                "elapsed_seconds": elapsed,
                "detail": detail,
                "metadata": checkpoint_metadata,
            })

        checkpoint_status = "completed" if state == "completed" else "error" if state == "error" else state
        checkpoint_artifacts = _checkpoint_artifacts(stage_key)
        try:
            state_mgr.write_stage_checkpoint(
                manifest,
                stage=stage_key,
                status=checkpoint_status,
                artifacts=checkpoint_artifacts,
                metadata=checkpoint_metadata,
                elapsed=elapsed,
            )
        except Exception as exc:
            logger.debug(f"Checkpoint dual-write skipped for {stage_key}: {exc}")

    def _add_decision(stage: str, label: str, detail: str = "", severity: str = "info", metadata: Optional[dict] = None) -> None:
        manifest.decision_trail.append({
            "stage": stage,
            "label": label,
            "detail": detail,
            "severity": severity,
            "timestamp": int(time.time() * 1000),
            "metadata": metadata or {},
        })

    def _infer_render_error_code(render_backend: str, render_error: str) -> ErrorCode:
        msg = (render_error or "").lower()
        if render_backend == "ffmpeg":
            if "timeout" in msg:
                return ErrorCode.FFMPEG_TIMEOUT
            if "concat" in msg:
                return ErrorCode.FFMPEG_CONCAT_FAIL
            if "audio mix" in msg or "amix" in msg:
                return ErrorCode.FFMPEG_AUDIO_MIX_FAIL
            return ErrorCode.FFMPEG_FILTER_FAIL
        return ErrorCode.UNKNOWN

    def _merge_unique_strings(items: list[str], limit: int = 12) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for item in items:
            clean = str(item or "").strip()
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(clean)
            if len(merged) >= max(1, int(limit)):
                break
        return merged

    def _update_ab_variant_selection(
        qa_passed: bool,
        qa_issues: list[str],
        *,
        qa_skipped: bool = False,
    ) -> None:
        """Score and persist A/B variant selection metadata for observability.

        Current V15 render path produces one final variant per run, so selection is
        expressed as promote/iterate/hold decision for that candidate.
        """
        split = dict(manifest.ab_visual_split or {})
        if not bool(split.get("enabled", False)):
            manifest.ab_visual_split = split
            return

        quality = float(manifest.quality_score or 0.0)
        viral = float(manifest.viral_score or 0.0)
        base_score = (quality * 0.62) + (viral * 0.38)
        backend_bonus = 0.15 if str(manifest.render_backend or "").lower() == "remotion" else 0.0

        issue_count = len(qa_issues or [])
        qa_penalty = 0.0 if qa_passed else min(2.0, 0.40 * max(1, issue_count))
        selection_score = round(max(0.0, min(10.0, base_score + backend_bonus - qa_penalty)), 2)
        try:
            saar_candidate_count = int(split.get("saar_candidate_count", 0) or 0)
        except (TypeError, ValueError):
            saar_candidate_count = 0

        if saar_candidate_count >= 2:
            selection_mode = "saar_ab_scoring"
        elif bool(split.get("saar_enabled", False)):
            selection_mode = "saar_ab_attempted"
        else:
            selection_mode = "single_run_scoring"

        if qa_skipped:
            selection_decision = "needs_qa_confirmation"
            severity = "warning"
        elif not qa_passed:
            selection_decision = "hold_review"
            severity = "warning"
        elif selection_score >= 8.0:
            selection_decision = "promote"
            severity = "info"
        elif selection_score >= 7.0:
            selection_decision = "keep_testing"
            severity = "info"
        else:
            selection_decision = "iterate"
            severity = "warning"

        reason_parts = [
            f"quality={quality:.2f}",
            f"viral={viral:.2f}",
            f"qa={'pass' if qa_passed else 'fail'}",
        ]
        if qa_skipped:
            reason_parts.append("qa_skipped=true")
        if qa_penalty > 0:
            reason_parts.append(f"qa_penalty={qa_penalty:.2f}")
        if saar_candidate_count > 0:
            reason_parts.append(f"saar_candidates={saar_candidate_count}")
            saar_pick = str(split.get("saar_selected_variant", "") or "")
            if saar_pick:
                reason_parts.append(f"saar_pick={saar_pick}")

        split.update(
            {
                "selected_variant": str(manifest.ab_variant or "A"),
                "selection_score": selection_score,
                "selection_decision": selection_decision,
                "selection_mode": selection_mode,
                "qa_gate_passed": bool(qa_passed),
                "qa_skipped": bool(qa_skipped),
                "qa_penalty": round(qa_penalty, 2),
                "selection_reason": ", ".join(reason_parts)[:220],
                "selection_timestamp": int(time.time() * 1000),
            }
        )
        manifest.ab_visual_split = split

        _add_decision(
            "qa_post",
            "A/B variant selection updated",
            (
                f"variant={split.get('selected_variant', 'A')}, "
                f"decision={selection_decision}, score={selection_score:.2f}"
            ),
            severity=severity,
            metadata={
                "ab_visual_split": {
                    "enabled": bool(split.get("enabled", False)),
                    "multiplier": int(split.get("multiplier", 1) or 1),
                    "selected_variant": str(split.get("selected_variant", "A") or "A"),
                    "selection_score": selection_score,
                    "selection_decision": selection_decision,
                    "qa_gate_passed": bool(qa_passed),
                    "qa_skipped": bool(qa_skipped),
                }
            },
        )

    def _is_non_blocking_composition_validator_error(message: str) -> bool:
        text = str(message or "").strip().lower()
        if not text:
            return False
        # Current CompositionValidator validates a legacy ExplainerProps schema
        # using `cuts`, while V15 timeline JSON uses `scenes`.
        return "no cuts defined in composition" in text

    def _tool_checks_summary() -> dict:
        checks = pre_render_tool_checks if isinstance(pre_render_tool_checks, dict) else {}
        summary = {
            "registered": sorted(checks.keys()),
            "registered_count": len(checks),
            "failing_count": 0,
            "warning_count": 0,
            "error_count": 0,
        }

        for payload in checks.values():
            if not isinstance(payload, dict):
                continue
            if payload.get("valid") is False:
                summary["failing_count"] += 1

            warnings = payload.get("warnings")
            errors = payload.get("errors")
            warning_count = payload.get("warning_count")
            error_count = payload.get("error_count")

            if warning_count is None and isinstance(warnings, list):
                warning_count = len(warnings)
            if error_count is None and isinstance(errors, list):
                error_count = len(errors)

            try:
                summary["warning_count"] += int(warning_count or 0)
            except (TypeError, ValueError):
                pass
            try:
                summary["error_count"] += int(error_count or 0)
            except (TypeError, ValueError):
                pass

        return summary

    def _rollback_controls_snapshot() -> dict:
        flags = manifest.feature_flags if isinstance(manifest.feature_flags, dict) else {}
        return {
            "quality_tools": {
                "enabled": bool(flags.get("enable_openmontage_free_tools", False))
                and bool(flags.get("openmontage_enable_analysis", False)),
                "flag_enable_openmontage_free_tools": bool(flags.get("enable_openmontage_free_tools", False)),
                "flag_openmontage_enable_analysis": bool(flags.get("openmontage_enable_analysis", False)),
            },
            "subtitle_bridge": {
                "enabled": bool(flags.get("use_whisperx", False)) and bool(flags.get("openmontage_enable_subtitle", False)),
                "flag_use_whisperx": bool(flags.get("use_whisperx", False)),
                "flag_openmontage_enable_subtitle": bool(flags.get("openmontage_enable_subtitle", False)),
                "flag_subtitles_use_script_text": bool(flags.get("subtitles_use_script_text", False)),
            },
            "ab_split": {
                "enabled": bool(flags.get("enable_ab_visual_split", False)),
                "saar_enabled": bool(flags.get("enable_saar_composer", False)),
                "saar_use_winner": bool(flags.get("saar_composer_use_winner", False)),
            },
            "post_tts": {
                "smart_silence_trim": bool(flags.get("enable_smart_silence_trim", False)),
                "loudnorm": bool(flags.get("enable_post_tts_loudnorm", False)),
            },
            "render_policy": {
                "force_ffmpeg_renderer": bool(flags.get("force_ffmpeg_renderer", False)),
                "allow_ffmpeg_fallback": bool(flags.get("allow_ffmpeg_fallback", False)),
                "require_remotion": bool(flags.get("require_remotion", False)),
            },
        }

    def _governance_snapshot() -> dict:
        return {
            "cost_estimate_usd": round(float(manifest.cost_estimate_usd or 0.0), 4),
            "cost_reserved_usd": round(float(manifest.cost_reserved_usd or 0.0), 4),
            "cost_actual_usd": round(float(manifest.cost_actual_usd or 0.0), 4),
            "cost_breakdown": dict(manifest.cost_breakdown or {}),
            "budget_blocked": bool(manifest.budget_blocked),
            "budget_monthly_blocked": bool(manifest.budget_monthly_blocked),
            "month_to_date_spend_usd": round(float(manifest.month_to_date_spend_usd or 0.0), 4),
        }

    def _apply_research_edit_notes(notes: str) -> None:
        """Inject checkpoint edit notes back into research context before script stage."""
        clean_notes = str(notes or "").strip()
        if not clean_notes:
            return

        parsed_lines = normalize_manual_ideas(clean_notes, limit=6)
        story.manual_ideas = _merge_unique_strings(story.manual_ideas + parsed_lines, limit=12)
        manifest.manual_ideas = list(story.manual_ideas)

        if parsed_lines:
            story.research.recommended_angles = _merge_unique_strings(
                parsed_lines + list(story.research.recommended_angles),
                limit=5,
            )
            story.research.hook_suggestions = _merge_unique_strings(
                list(story.research.hook_suggestions) + parsed_lines,
                limit=6,
            )
            research_context = " | ".join(parsed_lines)
            if story.research.trending_context_raw:
                story.research.trending_context_raw = (
                    f"{story.research.trending_context_raw} | RESEARCH_EDIT: {research_context}"
                )
            else:
                story.research.trending_context_raw = f"RESEARCH_EDIT: {research_context}"

        story.revision_notes.append(clean_notes[:220])

    def _checkpoint_artifacts(stage_key: str) -> dict:
        artifacts: dict[str, dict] = {}
        if stage_key == "content_gen":
            artifacts["script"] = {
                "title": manifest.titulo,
                "hook": manifest.gancho,
                "cta": manifest.cta,
                "reference_url": manifest.reference_url,
                "style_playbook": manifest.style_playbook,
            }
        elif stage_key == "quality_gate":
            artifacts["quality_report"] = {
                "quality_score": manifest.quality_score,
                "hook_score": manifest.hook_score,
                "viral_score": manifest.viral_score,
            }
        elif stage_key == "media":
            artifacts["asset_manifest"] = {
                "clip_count": len(manifest.clip_paths),
                "image_count": len(manifest.image_paths),
                "sfx_count": len(manifest.sfx_paths),
                "ab_visual_split": manifest.ab_visual_split,
            }
        elif stage_key == "tts":
            artifacts["audio"] = {
                "audio_path": manifest.audio_path,
                "tts_engine": manifest.tts_engine_used,
                "duration_seconds": manifest.duration_seconds,
            }
        elif stage_key == "subtitles":
            artifacts["subtitles"] = {
                "subs_path": manifest.subs_path,
            }
        elif stage_key == "combine":
            artifacts["edit_decisions"] = {
                "timeline_json_path": manifest.timeline_json_path,
                "edit_decisions_path": manifest.edit_decisions_path,
                "director_json_path": manifest.director_json_path,
                "director_meta_path": manifest.director_meta_path,
            }
        elif stage_key == "validated":
            artifacts["pre_render_validation"] = {
                "tool_checks": pre_render_tool_checks,
            }
        elif stage_key == "render":
            artifacts["render_report"] = {
                "video_path": manifest.video_path,
                "thumbnail_path": manifest.thumbnail_path,
                "backend": manifest.render_backend,
            }
        elif stage_key == "qa_post":
            artifacts["qa_report"] = {
                "passed": manifest.qa_passed,
                "issues": manifest.qa_issues,
                "report": manifest.post_render_report,
                "ab_visual_split": manifest.ab_visual_split,
            }
        elif stage_key == "publish":
            artifacts["publish_log"] = {
                "status": manifest.status,
                "drive_link": manifest.drive_link,
            }

        artifacts["governance"] = _governance_snapshot()
        artifacts["rollback_controls"] = _rollback_controls_snapshot()
        artifacts["tool_checks_summary"] = _tool_checks_summary()
        return artifacts

    if settings.enable_openmontage_free_tools and settings.openmontage_enable_styles:
        requested_playbook = settings.openmontage_default_playbook or "clean-professional"
        playbook_name, playbook_issues = apply_playbook_to_story(story, requested_playbook)
        if playbook_name:
            manifest.style_playbook = playbook_name
            _add_decision("style", f"Playbook applied: {playbook_name}")
            if playbook_issues:
                _add_decision(
                    "style",
                    "Playbook validation warnings",
                    "; ".join(playbook_issues[:3]),
                    severity="warning",
                    metadata={"issue_count": len(playbook_issues)},
                )
        else:
            _add_decision(
                "style",
                "Playbook unavailable",
                requested_playbook,
                severity="warning",
            )

    if settings.v15_strict_free_media_tools:
        _add_decision(
            "policy",
            "Strict-free media/tools policy enabled",
            "Only free providers are eligible for media/render tool stages",
        )

    if story.manual_ideas:
        _add_decision(
            "inputs",
            "Manual ideas injected",
            " | ".join(story.manual_ideas[:3]),
            metadata={"ideas_count": len(story.manual_ideas)},
        )

    if story.niche_memory_entries:
        _add_decision(
            "inputs",
            "Local niche memory loaded",
            " | ".join(story.niche_memory_entries[:2]),
            metadata={"memory_count": len(story.niche_memory_entries)},
        )

    if runtime_overrides:
        _add_decision(
            "policy",
            "Runtime overrides received",
            json.dumps(runtime_overrides, ensure_ascii=False)[:220],
            metadata={"runtime_overrides": runtime_overrides},
        )

    control_plane_plan: dict[str, object] = {}

    # Agents
    research_agent = ResearchAgent()
    reference_agent = ReferenceAgent()
    script_agent = ScriptAgent()
    scene_agent = SceneAgent()
    asset_agent = AssetAgent()
    editor_agent = EditorAgent()

    with _progress_scope() as progress:
        total_stages = 5 if dry_run else 12
        main_task = progress.add_task(
            f"[cyan]🎬 V15 {nicho_slug.upper()} Pipeline", total=total_stages,
        )

        try:
            # Optional reference-driven context loading.
            if manifest.reference_url:
                if settings.enable_reference_driven:
                    ref_analysis = reference_agent.run(manifest.reference_url, settings.temp_dir)
                    if ref_analysis:
                        manifest.reference_analysis = ref_analysis
                        story.reference_delivery_promise = str(ref_analysis.get("delivery_promise", ""))
                        story.reference_hook_seconds = float(ref_analysis.get("hook_seconds", 0.0) or 0.0)
                        story.reference_avg_cut_seconds = float(ref_analysis.get("avg_cut_seconds", 0.0) or 0.0)
                        story.reference_video_available = bool(ref_analysis.get("video_available", False))
                        manifest.reference_delivery_promise = story.reference_delivery_promise
                        manifest.reference_hook_seconds = story.reference_hook_seconds
                        manifest.reference_avg_cut_seconds = story.reference_avg_cut_seconds
                        manifest.reference_video_available = story.reference_video_available
                        extra_points = [str(x) for x in ref_analysis.get("key_moments", []) if x]
                        if extra_points:
                            story.reference_key_points = (story.reference_key_points + extra_points)[:6]

                cache_path = settings.temp_dir / "reference_context_cache.json"
                ref_ctx = load_reference_context(manifest.reference_url, cache_path)
                if ref_ctx:
                    story.reference_title = str(ref_ctx.get("title", ""))
                    story.reference_summary = str(ref_ctx.get("summary", ""))
                    text_points = [str(x).strip() for x in ref_ctx.get("key_points", []) if str(x).strip()]
                    if text_points:
                        merged_points: list[str] = []
                        seen_points: set[str] = set()
                        for point in list(story.reference_key_points) + text_points:
                            key = point.lower()
                            if key in seen_points:
                                continue
                            seen_points.add(key)
                            merged_points.append(point)
                            if len(merged_points) >= 6:
                                break
                        story.reference_key_points = merged_points
                    manifest.reference_notes = "reference_context_loaded"
                else:
                    manifest.reference_notes = "reference_context_unavailable"

            # ── Stage 1: Research ────────────────────────────────────
            _stage_start("content_gen", "Content Generation", "Research + Script + Scenes + Review")
            t = time.time()
            progress.update(main_task, description="[cyan]🔍 Research Agent...")
            research_agent.run(nicho, story)
            manifest.timings["research"] = round(time.time() - t, 2)

            # Checkpoint: research
            research_summary = (
                f"📊 Trending: {', '.join(story.research.trending_topics[:3]) or 'N/A'}\n"
                f"🎯 Ángulos: {', '.join(story.research.recommended_angles) or 'N/A'}\n"
                f"🪝 Hooks sugeridos: {', '.join(story.research.hook_suggestions[:3]) or 'N/A'}\n"
                f"📚 Prioridad: {story.precedence_rule}\n"
                f"🔗 Reference: {story.reference_url or 'N/A'}"
            )
            result = director.checkpoint("research", research_summary, story)
            _add_decision(
                "content_gen",
                f"Checkpoint research: {result.decision.value}",
                (result.notes or "")[:160],
                severity="warning" if result.decision.value == "reject" else "info",
            )
            if not result.approved and result.decision.value == "reject":
                _stage_end("content_gen", "error", "Rejected at research checkpoint")
                manifest.status = JobStatus.DRAFT.value
                state_mgr.save(manifest)
                return manifest

            if result.edited:
                _apply_research_edit_notes(result.notes)
                # Re-run research with injected edit notes so script stage consumes corrected intent.
                research_agent.run(nicho, story)
                _add_decision(
                    "content_gen",
                    "Research checkpoint edits applied",
                    (result.notes or "")[:180],
                    metadata={"edited": True},
                )

            progress.advance(main_task)

            # ── Stage 2: Script Generation (prompt chaining) ─────────
            t = time.time()
            progress.update(main_task, description="[cyan]✍️ Script Agent...")

            script_approved = False
            script_attempts = 0
            script_feedback_notes = ""

            while not script_approved and script_attempts < 3:
                correction = script_feedback_notes if script_attempts > 0 else ""

                script_agent.run(story, nicho, correction_notes=correction)
                script_attempts += 1

                # Checkpoint: script
                script_display = (
                    f"🪝 Hook: {story.hook}\n\n"
                    f"📝 Guión:\n{story.script_full}\n\n"
                    f"📣 CTA: {story.cta}\n"
                    f"📱 Caption: {story.caption}"
                )
                result = director.checkpoint("script", script_display, story, {
                    "Hook Score": story.hook_score,
                    "Script Score": story.script_score,
                    "Attempt": script_attempts,
                })
                _add_decision(
                    "content_gen",
                    f"Checkpoint script attempt {script_attempts}: {result.decision.value}",
                    (result.notes or "")[:160],
                    severity="warning" if result.decision.value == "reject" else "info",
                    metadata={"attempt": script_attempts},
                )
                script_feedback_notes = result.notes if hasattr(result, "notes") else ""
                script_approved = result.approved

                # V16.1: subtema no repetido (solo tras aprobación del director; continue pertenece a este while)
                if script_approved and settings.force_subtopic_variety:
                    subtopic_mgr = get_subtopic_manager()
                    angle_candidate = ""
                    if story.research and story.research.recommended_angles:
                        angle_candidate = str(story.research.recommended_angles[0] or "").strip()

                    script_seed = ""
                    if story.script_full:
                        script_seed = str(story.script_full).strip().split("\n", 1)[0][:140]

                    candidate_parts = [
                        angle_candidate,
                        str(story.hook or "").strip(),
                        script_seed,
                    ]
                    subtopic_candidate = " | ".join(part for part in candidate_parts if part)
                    if not subtopic_candidate:
                        subtopic_candidate = story.topic or "sin-subtema"

                    is_duplicate, similarity = subtopic_mgr.is_subtopic_used(
                        nicho_slug, subtopic_candidate
                    )

                    if is_duplicate:
                        logger.warning(
                            f"🔄 Subtema repetido detectado (similitud: {similarity:.2f}). "
                            f"Regenerando con instrucciones de variedad..."
                        )
                        script_feedback_notes = (
                            f"SUBTEMA REPETIDO (similitud {similarity:.0%}). "
                            f"Elige un tema COMPLETAMENTE DIFERENTE. "
                            f"Historial reciente: {subtopic_mgr.get_used_subtopics(nicho_slug)[:3]}"
                        )
                        script_approved = False
                        continue

                    subtopic_mgr.record_subtopic(
                        nicho_slug=nicho_slug,
                        subtopic=subtopic_candidate,
                        video_id=manifest.job_id,
                    )
                    logger.info(f"✅ Subtema registrado: {subtopic_candidate[:80]}...")

            if not script_approved:
                _stage_end("content_gen", "error", "Script not approved after retries")
                manifest.status = JobStatus.DRAFT.value
                state_mgr.save(manifest)
                return manifest

            manifest.timings["script"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 3: Quality Gate (V14 reuse) ────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🔎 Quality Gate...")
            _stage_start("quality_gate", "Quality Gate")

            raw_content = script_agent.get_raw_content(story)
            if raw_content:
                content, quality, errors = validate_and_score(raw_content, nicho)

                # Self-healing loop (same as V14)
                healing_attempts = 0
                while (content is None or not quality.is_approved) and healing_attempts < app_config.max_healing_attempts:
                    healing_attempts += 1
                    error_detail = "; ".join(errors) if errors else "Quality below threshold"
                    primary_code = quality.error_codes[0] if quality.error_codes else ErrorCode.UNKNOWN

                    if content is None:
                        fix = attempt_healing(
                            manifest, FailureType.JSON, "quality_gate",
                            error_detail, json.dumps(raw_content, default=str)[:1000],
                            error_code=ErrorCode.JSON_SCHEMA_INVALID,
                        )
                        if fix:
                            try:
                                raw_content = json.loads(fix)
                                content, quality, errors = validate_and_score(raw_content, nicho)
                                continue
                            except Exception:
                                pass
                    else:
                        fix = attempt_healing(
                            manifest, FailureType.PROMPT, "quality_gate",
                            error_detail, content.guion[:500] if content else "",
                            nicho=nicho, error_code=primary_code,
                        )
                        if fix:
                            try:
                                fixed_data = json.loads(fix) if isinstance(fix, str) and fix.strip().startswith("{") else raw_content
                                content, quality, errors = validate_and_score(fixed_data, nicho)
                                continue
                            except Exception:
                                pass
                    break

                if content is None:
                    _stage_end("quality_gate", "error", "Content validation failed")
                    manifest.status = JobStatus.ERROR.value
                    manifest.error_stage = "quality_gate"
                    manifest.error_message = "Content validation failed"
                    manifest.error_code = ErrorCode.JSON_SCHEMA_INVALID.value
                    state_mgr.save(manifest)
                    notify_error(manifest)
                    return manifest

                # Update manifest
                manifest.titulo = content.titulo
                manifest.gancho = content.gancho
                manifest.guion = content.guion
                manifest.cta = content.cta
                manifest.caption = content.caption
                publish_pkg = build_publish_package(
                    title=manifest.titulo,
                    hook=manifest.gancho,
                    cta=manifest.cta,
                    caption=manifest.caption,
                )
                manifest.publish_title = publish_pkg["title"]
                manifest.publish_description = publish_pkg["description"]
                manifest.publish_hashtags = publish_pkg["hashtags"]
                manifest.publish_hashtags_text = publish_pkg["hashtags_text"]
                manifest.publish_comment = publish_pkg["comment"]
                manifest.quality_score = quality.quality_score
                manifest.viral_score = content.viral_score
                manifest.hook_score = quality.block_scores.hook
                manifest.block_scores = quality.block_scores
                manifest.ab_variant = raw_content.get("_ab_variant", "A")
                manifest.input_hash = content.input_hash
                if raw_content.get("_reference_applied"):
                    manifest.reference_notes = "reference_applied_in_script"

                if not quality.is_approved:
                    manifest.status = JobStatus.MANUAL_REVIEW.value
                    _add_decision(
                        "quality_gate",
                        "Quality gate flagged manual review",
                        f"score={quality.quality_score:.2f}",
                        severity="warning",
                    )

            manifest.timings["quality_gate"] = round(time.time() - t, 2)
            _stage_end(
                "quality_gate",
                "completed",
                metadata={
                    "quality_score": manifest.quality_score,
                    "hook_score": manifest.hook_score,
                    "status": manifest.status,
                },
            )
            _add_decision(
                "quality_gate",
                "Quality gate completed",
                f"quality={manifest.quality_score:.2f}, viral={manifest.viral_score:.2f}",
            )
            state_mgr.mark_stage(manifest, "quality_gate")
            progress.advance(main_task)

            # ── Stage 4: Scene Planning ──────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🎬 Scene Agent...")

            scenes = scene_agent.run(story, nicho)

            # Checkpoint: scenes
            cp_result = director.checkpoint_scenes(scenes, story)
            _add_decision(
                "content_gen",
                f"Checkpoint scenes: {cp_result.decision.value}",
                (cp_result.notes or "")[:160],
                severity="warning" if cp_result.decision.value == "reject" else "info",
            )
            if not cp_result.approved and cp_result.decision.value == "reject":
                _stage_end("content_gen", "error", "Rejected at scenes checkpoint")
                manifest.status = JobStatus.DRAFT.value
                state_mgr.save(manifest)
                return manifest
            elif cp_result.edited:
                # Re-run scene agent with correction notes
                scenes = scene_agent.run(story, nicho, correction_notes=cp_result.notes)

            manifest.timings["scene_plan"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 5: Feedback Loop ───────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🔬 Review System...")

            review = review_content(story, nicho)
            story.overall_score = review.overall_score
            story.coherence_score = review.coherence_score

            should_retry, retry_stage, correction = should_iterate(story, review)
            while should_retry:
                story.feedback_iterations += 1
                story.revision_notes.append(correction[:200])
                logger.info(f"🔄 Feedback iteration {story.feedback_iterations}: retrying {retry_stage}")

                if retry_stage == "script":
                    script_agent.run(story, nicho, correction_notes=correction)
                    raw_content = script_agent.get_raw_content(story)
                    if raw_content:
                        content, quality, _ = validate_and_score(raw_content, nicho)
                        if content:
                            manifest.quality_score = quality.quality_score
                elif retry_stage == "scenes":
                    scene_agent.run(story, nicho, correction_notes=correction)

                review = review_content(story, nicho)
                should_retry, retry_stage, correction = should_iterate(story, review)

            manifest.timings["review"] = round(time.time() - t, 2)
            manifest.timings["content_gen"] = round(
                sum(
                    manifest.timings.get(k, 0.0)
                    for k in ("research", "script", "scene_plan", "review")
                ),
                2,
            )
            _stage_end(
                "content_gen",
                "completed",
                metadata={"feedback_iterations": story.feedback_iterations},
            )
            _add_decision(
                "content_gen",
                "Content generation completed",
                f"hook={story.hook[:60]} | feedback_iterations={story.feedback_iterations}",
            )
            progress.advance(main_task)

            # ── DRY RUN EXIT ─────────────────────────────────────────
            if dry_run:
                manifest.status = JobStatus.DRAFT.value
                state_mgr.save(manifest)
                console.print("\n[yellow]🏁 DRY RUN — script + scenes generated, no render.[/yellow]")
                _print_v15_summary(manifest, story)
                return manifest

            # ── Stage 6: Assets ──────────────────────────────────────
            media_stage_t0 = time.time()
            _stage_start("media", "Media Retrieval")
            t = time.time()
            progress.update(main_task, description="[cyan]🎨 Asset Agent...")

            allowed_assets, reason_assets, est_assets = cost_governance.reserve_stage("assets")
            if not allowed_assets:
                _stage_end("media", "error", reason_assets[:180])
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "assets_budget"
                manifest.error_message = reason_assets
                manifest.error_code = ErrorCode.UNKNOWN.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            if settings.gemini_control_plane_enabled and settings.provider_selector_enabled:
                try:
                    requested_images = runtime_overrides.get("generated_images_count", settings.generated_images_count)
                    requested_images = max(4, min(10, int(requested_images)))
                    ab_split_enabled = bool(
                        runtime_overrides.get("ab_visual_split_enabled", settings.enable_ab_visual_split)
                    )
                    try:
                        ab_multiplier = int(
                            runtime_overrides.get("ab_visual_multiplier", settings.ab_visual_split_multiplier)
                        )
                    except (TypeError, ValueError):
                        ab_multiplier = int(settings.ab_visual_split_multiplier)
                    if ab_split_enabled:
                        requested_images = max(4, min(16, requested_images * max(2, min(3, ab_multiplier))))

                    control_plane_plan = get_gemini_control_plane().plan_media(
                        script_text=" ".join(filter(None, [manifest.gancho, manifest.guion, manifest.cta])) or story.script_full,
                        execution_mode=manifest.execution_mode,
                        image_count=requested_images,
                        music_count=1,
                    )
                    for decision_event in control_plane_plan.get("decisions", []):
                        if not isinstance(decision_event, dict):
                            continue
                        _add_decision(
                            decision_event.get("stage", "policy"),
                            decision_event.get("label", "Control plane decision"),
                            decision_event.get("detail", ""),
                            decision_event.get("severity", "info"),
                            decision_event.get("metadata", {}),
                        )
                except Exception as cp_exc:
                    _add_decision(
                        "policy",
                        "Gemini control plane skipped",
                        str(cp_exc)[:180],
                        severity="warning",
                    )

            stage_runtime_overrides = dict(runtime_overrides)
            if settings.gemini_control_plane_enabled and settings.gemini_control_plane_enforce_orders:
                for key in (
                    "provider_order_stock_video",
                    "provider_order_image_generation",
                    "provider_order_music_generation",
                    "provider_order_tts",
                ):
                    value = control_plane_plan.get(key)
                    if isinstance(value, list) and value:
                        stage_runtime_overrides[key] = value

            strict_free_media = settings.v15_strict_free_media_tools or (
                settings.free_mode and not settings.allow_freemium_in_free_mode
            )
            gemini_everywhere_enabled = bool(
                stage_runtime_overrides.get("gemini_everywhere_mode", settings.gemini_everywhere_mode)
            )
            if gemini_everywhere_enabled and not strict_free_media:
                try:
                    current_image_count = int(
                        stage_runtime_overrides.get("generated_images_count", settings.generated_images_count)
                    )
                except (TypeError, ValueError):
                    current_image_count = int(settings.generated_images_count)

                stage_runtime_overrides["provider_order_music_generation"] = [
                    "lyria", "suno", "pixabay", "jamendo"
                ]
                stage_runtime_overrides["provider_order_tts"] = [
                    "gemini", "edge-tts", "piper", "google_tts", "elevenlabs"
                ]
                # There is no dedicated Gemini image provider yet, so prefer AI image generators first.
                stage_runtime_overrides["provider_order_image_generation"] = [
                    "pollinations", "leonardo", "pexels", "pixabay"
                ]
                stage_runtime_overrides["prefer_stock_images"] = False
                stage_runtime_overrides["generated_images_count"] = max(8, current_image_count)
                stage_runtime_overrides["ab_visual_split_enabled"] = True
                stage_runtime_overrides["saar_composer_enabled"] = True
                stage_runtime_overrides["saar_composer_use_winner"] = True

                _add_decision(
                    "policy",
                    "Gemini-everywhere mode active",
                    "Gemini-first TTS/music + AI-first images + dynamic visual split",
                    metadata={
                        "provider_order_tts": stage_runtime_overrides.get("provider_order_tts"),
                        "provider_order_music_generation": stage_runtime_overrides.get("provider_order_music_generation"),
                        "provider_order_image_generation": stage_runtime_overrides.get("provider_order_image_generation"),
                        "generated_images_count": stage_runtime_overrides.get("generated_images_count"),
                    },
                )
            elif gemini_everywhere_enabled:
                _add_decision(
                    "policy",
                    "Gemini-everywhere limited by strict-free policy",
                    "Strict-free mode blocks freemium Gemini media providers",
                    severity="warning",
                )

            assets = asset_agent.run(
                story,
                nicho,
                timestamp,
                settings.temp_dir,
                runtime_overrides=stage_runtime_overrides,
            )

            stock_urls = assets.get("stock_clips", [])
            images = assets.get("images", [])
            music_path = assets.get("music_path")
            sfx_paths = assets.get("sfx_paths", [])
            ab_visual_split = assets.get("ab_visual_split", {})

            manifest.image_paths = [str(p) for p in images]
            manifest.sfx_paths = [str(p) for p in sfx_paths]
            if isinstance(ab_visual_split, dict):
                merged_ab_split = dict(manifest.ab_visual_split or {})
                merged_ab_split.update(ab_visual_split)
                merged_ab_split["stock_clips"] = len(stock_urls)
                merged_ab_split["images"] = len(images)
                manifest.ab_visual_split = merged_ab_split
            else:
                manifest.ab_visual_split = dict(manifest.ab_visual_split or {})

            # Checkpoint: assets
            asset_summary = (
                f"📦 Stock clips: {len(stock_urls)}\n"
                f"🖼️ Images: {len(images)}\n"
                f"🎵 Music: {'✅' if music_path else '❌'}\n"
                f"🔊 SFX: {len(sfx_paths)}"
            )
            if isinstance(ab_visual_split, dict) and ab_visual_split.get("enabled"):
                asset_summary += (
                    f"\n🧩 A/B split: x{ab_visual_split.get('multiplier', 2)}"
                    f" | target clips={ab_visual_split.get('target_clips', len(stock_urls))}"
                )
            result = director.checkpoint("assets", asset_summary, story)

            assets_actual = 0.0 if manifest.execution_mode == "free" else est_assets
            cost_governance.record_stage_actual("assets", assets_actual)

            manifest.timings["assets"] = round(time.time() - t, 2)
            state_mgr.mark_stage(manifest, "media")
            progress.advance(main_task)

            # ── Stage 7: Download + Combine clips ────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]⬇️ Downloading clips...")
            stock_clips = download_clips(stock_urls, timestamp, settings.temp_dir)
            clips = stock_clips
            manifest.clip_paths = [str(p) for p in clips]

            if not clips and not images:
                _stage_end("media", "error", "No clips and no images")
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "download"
                manifest.error_message = "No clips and no images"
                manifest.error_code = ErrorCode.ASSET_MISSING.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.timings["download"] = round(time.time() - t, 2)
            manifest.timings["media"] = round(time.time() - media_stage_t0, 2)
            _stage_end(
                "media",
                "completed",
                metadata={"clips": len(clips), "images": len(images), "sfx": len(sfx_paths)},
            )
            _add_decision(
                "media",
                f"Media selected: {len(clips)} clips, {len(images)} images",
                f"music={'yes' if music_path else 'no'}",
                metadata={"ab_visual_split": manifest.ab_visual_split},
            )
            progress.advance(main_task)

            # ── Stage 8: TTS ─────────────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🗣️ TTS...")
            _stage_start("tts", "Narration TTS")

            gemini_everywhere_enabled = bool(
                stage_runtime_overrides.get("gemini_everywhere_mode", settings.gemini_everywhere_mode)
            )
            if gemini_everywhere_enabled and settings.provider_allowed("gemini", usage="media"):
                preferred_tts_provider = "gemini"
            elif settings.elevenlabs_api_key and settings.provider_allowed("elevenlabs", usage="media"):
                preferred_tts_provider = "elevenlabs"
            elif settings.use_google_tts and settings.provider_allowed("google_tts", usage="media"):
                preferred_tts_provider = "google_tts"
            elif settings.provider_allowed("gemini", usage="media"):
                preferred_tts_provider = "gemini"
            else:
                preferred_tts_provider = "edge_tts"
            tts_provider_order = stage_runtime_overrides.get("provider_order_tts", [])
            if isinstance(tts_provider_order, list) and tts_provider_order:
                preferred_tts_provider = str(tts_provider_order[0]).replace("-", "_")
            allowed_tts, reason_tts, _ = cost_governance.reserve_stage(
                "tts",
                provider=preferred_tts_provider,
            )
            if not allowed_tts:
                _stage_end("tts", "error", reason_tts[:180])
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "tts_budget"
                manifest.error_message = reason_tts
                manifest.error_code = ErrorCode.UNKNOWN.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            # Keep narration on canonical script text to avoid scene-level paraphrasing.
            script_tts_text = " ".join(filter(None, [manifest.gancho, manifest.guion, manifest.cta]))
            if settings.tts_use_script_text:
                guion_tts = script_tts_text or story.scene_texts_joined()
            else:
                guion_tts = story.scene_texts_joined() or script_tts_text
            guion_tts = _clean_tts_text(guion_tts)

            audio_path = settings.temp_dir / f"audio_{timestamp}.mp3"
            vtt_path = settings.temp_dir / f"subs_{timestamp}.vtt"

            tts_ok, tts_engine = generate_tts(
                guion_tts, audio_path,
                voz_gemini=nicho.voz_gemini,
                voz_edge=nicho.voz_edge,
                rate_tts=nicho.rate_tts,
                pitch_tts=nicho.pitch_tts,
                subs_vtt_path=vtt_path,
                provider_order=tts_provider_order if isinstance(tts_provider_order, list) else None,
            )

            if not tts_ok:
                fix = attempt_healing(
                    manifest,
                    FailureType.AUDIO,
                    "tts",
                    "TTS generation failed for available providers",
                    error_code=ErrorCode.TTS_EMPTY_AUDIO,
                )

                fix_data = {}
                if fix:
                    try:
                        fix_data = json.loads(fix) if isinstance(fix, str) else dict(fix)
                    except Exception:
                        fix_data = {}

                if fix_data.get("action") == "retry_edge_tts":
                    tts_ok, tts_engine = generate_tts(
                        guion_tts, audio_path,
                        voz_gemini="",
                        voz_edge=nicho.voz_edge,
                        rate_tts=nicho.rate_tts,
                        pitch_tts=nicho.pitch_tts,
                        subs_vtt_path=vtt_path,
                        provider_order=["edge-tts", "piper", "gemini", "google_tts", "elevenlabs"],
                    )

                # Last-resort reliability fallback:
                # if strict-free blocks Gemini and edge path failed, try Gemini once.
                if (
                    not tts_ok
                    and settings.v15_strict_free_media_tools
                    and bool(settings.get_gemini_keys())
                ):
                    logger.warning("V15 TTS strict-free path failed; trying Gemini rescue fallback")
                    tts_ok, tts_engine = generate_tts(
                        guion_tts, audio_path,
                        voz_gemini=nicho.voz_gemini,
                        voz_edge=nicho.voz_edge,
                        rate_tts=nicho.rate_tts,
                        pitch_tts=nicho.pitch_tts,
                        subs_vtt_path=vtt_path,
                        enforce_provider_policy=False,
                        provider_order=["gemini", "edge-tts", "google_tts", "piper", "elevenlabs"],
                    )
                    if tts_ok:
                        _add_decision(
                            "tts",
                            "Gemini rescue fallback applied",
                            "strict_free_override=true",
                        )

                if not tts_ok:
                    _stage_end("tts", "error", "TTS failed after healing")
                    manifest.status = JobStatus.ERROR.value
                    manifest.error_stage = "tts"
                    manifest.error_message = "TTS failed after healing"
                    manifest.error_code = ErrorCode.TTS_EMPTY_AUDIO.value
                    state_mgr.save(manifest)
                    notify_error(manifest)
                    return manifest

            processed_audio_path, audio_steps = apply_post_tts_audio_processing(
                audio_path=audio_path,
                timestamp=timestamp,
                temp_dir=settings.temp_dir,
            )
            if processed_audio_path != audio_path:
                audio_path = processed_audio_path
            if audio_steps:
                _add_decision(
                    "tts",
                    "Post-TTS audio processing applied",
                    ", ".join(audio_steps),
                    metadata={"audio_processing_steps": audio_steps},
                )

            manifest.audio_path = str(audio_path)
            manifest.tts_engine_used = tts_engine
            audio_duration = get_audio_duration(audio_path)
            manifest.duration_seconds = audio_duration

            tts_actual = settings.est_cost_tts_usd if tts_engine in {"gemini", "elevenlabs", "google_tts"} else 0.0
            cost_governance.record_stage_actual("tts", tts_actual)

            manifest.timings["tts"] = round(time.time() - t, 2)
            _stage_end("tts", "completed", metadata={"engine": tts_engine})
            _add_decision("tts", f"Narration generated with {tts_engine}", f"duration={audio_duration:.2f}s")
            progress.advance(main_task)

            # ── Stage 9: Subtitles ───────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]📝 Subtitles...")
            _stage_start("subtitles", "Subtitle Generation")

            ass_path = settings.temp_dir / f"subs_{timestamp}.ass"
            subtitles_text = guion_tts if settings.subtitles_use_script_text else (story.scene_texts_joined() or guion_tts)
            events = generate_subtitles_with_fallback(
                audio_path=audio_path,
                text=subtitles_text,
                audio_duration=audio_duration,
                ass_path=ass_path,
                language="es",
            )
            _add_decision(
                "subtitles",
                "WhisperX word-by-word subtitles applied" if events > 0 and settings.use_whisperx else (
                    "Script-locked subtitles applied" if settings.subtitles_use_script_text else "Timed text subtitles applied"
                ),
                f"events={events}",
            )

            manifest.subs_path = str(ass_path)

            # Duration validation
            audio_duration, was_trimmed = validate_duration(
                audio_duration, nicho.plataforma, audio_path,
                niche_slug=nicho.slug,
            )
            if was_trimmed:
                manifest.duration_seconds = audio_duration

            manifest.timings["subtitles"] = round(time.time() - t, 2)
            _stage_end(
                "subtitles",
                "completed",
                metadata={"subtitle_path": manifest.subs_path, "audio_duration": manifest.duration_seconds},
            )
            progress.advance(main_task)

            # ── Stage 10: Edit Decisions + Render ────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🎥 Rendering...")

            allowed_render, reason_render, est_render = cost_governance.reserve_stage("render")
            if not allowed_render:
                _stage_end("render", "error", reason_render[:180])
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "render_budget"
                manifest.error_message = reason_render
                manifest.error_code = ErrorCode.UNKNOWN.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            combine_t0 = time.time()
            _stage_start("combine", "Edit Decisions + Timeline")

            # Get scene-aware edit decisions
            edit_decisions = editor_agent.run(story, nicho, len(clips), audio_duration)

            # Build duraciones from edit decisions
            duraciones = [d.duration for d in edit_decisions] if edit_decisions else None

            # Build structured timeline JSON for Remotion (Phase 1 integration)
            render_inputs = []
            if clips and images:
                # Interleave clips and images for a dynamic montage
                for i in range(max(len(clips), len(images))):
                    if i < len(clips): render_inputs.append(clips[i])
                    if i < len(images): render_inputs.append(images[i])
            else:
                render_inputs = clips if clips else images
                
            timeline_path = settings.temp_dir / f"timeline_{timestamp}.json"
            timeline_payload = editor_agent.build_timeline_json(
                state=story,
                media_paths=render_inputs,
                decisions=edit_decisions,
                audio_duration=audio_duration,
                timeline_path=timeline_path,
                subtitles_path=ass_path if ass_path.exists() else None,
                narration_audio_path=audio_path,
                music_path=music_path if music_path and music_path.exists() else None,
                composition_id=requested_remotion_composition,
                style_playbook=manifest.style_playbook or "",
                visual_theme=str(
                    stage_runtime_overrides.get("remotion_theme", settings.remotion_theme)
                    or ""
                ),
                layout_variant=str(
                    stage_runtime_overrides.get("remotion_layout_variant", settings.remotion_layout_variant)
                    or ""
                ),
                kinetic_level=str(
                    stage_runtime_overrides.get("remotion_kinetic_level", settings.remotion_kinetic_level)
                    or ""
                ),
                transition_preset=str(
                    stage_runtime_overrides.get("remotion_transition_preset", settings.remotion_transition_preset)
                    or ""
                ),
                feature_card_mode=str(
                    stage_runtime_overrides.get("remotion_feature_card_mode", settings.remotion_feature_card_mode)
                    or ""
                ),
            )
            incremental_eml_seed = editor_agent.build_incremental_eml_seed(
                state=story,
                media_paths=render_inputs,
                decisions=edit_decisions,
                audio_duration=audio_duration,
            )

            try:
                edit_decisions_path, _ = build_edit_decisions_artifact(
                    timeline_payload=timeline_payload,
                    metadata={
                        "timestamp": timestamp,
                        "job_id": job_id,
                        "composition_id": requested_remotion_composition,
                        "titulo": manifest.titulo,
                        "nicho": nicho_slug,
                    },
                    artifacts_dir=settings.temp_dir,
                    subtitles_path=ass_path if ass_path.exists() else None,
                    narration_audio_path=audio_path,
                    music_path=music_path if music_path and music_path.exists() else None,
                    incremental_eml_seed=incremental_eml_seed,
                )
                manifest.edit_decisions_path = str(edit_decisions_path)

                director_path, director_meta_path, _, _ = build_director_artifacts(
                    timeline_payload=timeline_payload,
                    metadata={
                        "timestamp": timestamp,
                        "job_id": job_id,
                        "composition_id": requested_remotion_composition,
                        "titulo": manifest.titulo,
                        "nicho": nicho_slug,
                    },
                    artifacts_dir=settings.temp_dir,
                    subtitles_path=ass_path if ass_path.exists() else None,
                )
                manifest.director_json_path = str(director_path)
                manifest.director_meta_path = str(director_meta_path)
            except Exception as exc:
                _stage_end("combine", "error", str(exc)[:180])
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "combine"
                manifest.error_message = f"Edit/director artifact generation failed: {str(exc)[:180]}"
                manifest.error_code = ErrorCode.JSON_SCHEMA_INVALID.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.timeline_json_path = str(timeline_path)
            manifest.timings["combine"] = round(time.time() - combine_t0, 2)
            _stage_end(
                "combine",
                "completed",
                metadata={
                    "timeline_scenes": len(timeline_payload.get("scenes", [])),
                    "composition_id": str(timeline_payload.get("composition_id", requested_remotion_composition)),
                    "edit_decisions_path": manifest.edit_decisions_path,
                    "director_json_path": manifest.director_json_path,
                    "director_meta_path": manifest.director_meta_path,
                },
            )
            _add_decision(
                "combine",
                "Timeline assembled",
                (
                    f"scenes={len(timeline_payload.get('scenes', []))}, "
                    f"composition={timeline_payload.get('composition_id', requested_remotion_composition)}, "
                    f"edit={Path(manifest.edit_decisions_path).name if manifest.edit_decisions_path else 'none'}, "
                    f"director={Path(manifest.director_json_path).name if manifest.director_json_path else 'none'}"
                ),
            )

            # Determine velocidad from dominant mood
            velocidad = raw_content.get("velocidad_cortes", "rapido") if raw_content else "rapido"

            output_target = settings.output_dir
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                output_target = settings.review_dir

            # Pre-render validation
            validated_t0 = time.time()
            _stage_start("validated", "Pre-render Validation")

            composition_check: dict[str, object] = {}
            composition_error_summary = ""
            validator_tool = registry.get("composition_validator")
            if validator_tool and timeline_path.exists():
                try:
                    validator_result = validator_tool.execute(
                        {
                            "composition_path": str(timeline_path),
                            "assets_root": str(settings.workspace),
                        }
                    )
                    result_data = getattr(validator_result, "data", {}) or {}
                    if isinstance(result_data, dict):
                        composition_check = dict(result_data)
                        pre_render_tool_checks["composition_validator"] = composition_check

                    if not bool(getattr(validator_result, "success", False)):
                        composition_error_summary = str(getattr(validator_result, "error", "") or "")
                        if composition_error_summary:
                            _add_decision(
                                "validated",
                                "Composition validator reported issues",
                                composition_error_summary[:180],
                                severity="warning",
                            )
                except Exception as exc:
                    _add_decision(
                        "validated",
                        "Composition validator execution skipped",
                        str(exc)[:180],
                        severity="warning",
                    )

            pre_ok, pre_errors = validate_pre_render(
                audio_path=audio_path,
                subs_path=ass_path if ass_path.exists() else None,
                clips=clips,
                images=images,
                music_path=music_path if music_path and music_path.exists() else None,
                platform=nicho.plataforma,
                audio_duration=audio_duration,
            )

            if composition_check:
                warnings = composition_check.get("warnings", [])
                if isinstance(warnings, list):
                    for warning in warnings[:3]:
                        _add_decision(
                            "validated",
                            "Composition validator warning",
                            str(warning)[:180],
                            severity="warning",
                        )

                if not bool(composition_check.get("valid", True)):
                    comp_errors = composition_check.get("errors", [])
                    if not isinstance(comp_errors, list):
                        comp_errors = []
                    if not comp_errors and composition_error_summary:
                        comp_errors = [composition_error_summary]

                    blocking_comp_errors = [
                        issue for issue in comp_errors
                        if not _is_non_blocking_composition_validator_error(str(issue))
                    ]
                    non_blocking_comp_errors = [
                        issue for issue in comp_errors
                        if _is_non_blocking_composition_validator_error(str(issue))
                    ]

                    if non_blocking_comp_errors:
                        _add_decision(
                            "validated",
                            "Composition validator schema mismatch ignored",
                            str(non_blocking_comp_errors[0])[:180],
                            severity="warning",
                        )

                    for issue in blocking_comp_errors[:3]:
                        pre_errors.append(
                            (
                                ErrorCode.ASSET_MISSING,
                                f"Composition validator: {str(issue)[:180]}",
                            )
                        )

                    if blocking_comp_errors:
                        pre_ok = False

            if not pre_ok:
                flagged_names = extract_flagged_greenscreen_clips(pre_errors)
                if flagged_names:
                    _add_decision(
                        "validated",
                        "Greenscreen clips detected in pre-render",
                        ", ".join(sorted(flagged_names)[:4]),
                        severity="warning",
                        metadata={"flagged_count": len(flagged_names)},
                    )

                    # Deterministic healing path: drop flagged clips, rebuild timeline, retry validation.
                    attempt_healing(
                        manifest,
                        FailureType.RENDER,
                        "pre_render_validation",
                        "; ".join(msg for _, msg in pre_errors),
                        json.dumps({"flagged_clips": sorted(flagged_names)}),
                        error_code=ErrorCode.GREENSCREEN_DETECTED,
                    )

                    before_count = len(clips)
                    clips = [c for c in clips if c.name not in flagged_names]
                    removed_count = before_count - len(clips)

                    if removed_count > 0:
                        manifest.clip_paths = [str(p) for p in clips]

                        # Recompute edit decisions/timeline to keep durations consistent after clip removal.
                        edit_decisions = editor_agent.run(story, nicho, len(clips), audio_duration)
                        duraciones = [d.duration for d in edit_decisions] if edit_decisions else None
                        render_inputs = []
                        if clips and images:
                            for i in range(max(len(clips), len(images))):
                                if i < len(clips): render_inputs.append(clips[i])
                                if i < len(images): render_inputs.append(images[i])
                        else:
                            render_inputs = clips if clips else images
                            
                        timeline_payload = editor_agent.build_timeline_json(
                            state=story,
                            media_paths=render_inputs,
                            decisions=edit_decisions,
                            audio_duration=audio_duration,
                            timeline_path=timeline_path,
                            subtitles_path=ass_path if ass_path.exists() else None,
                            narration_audio_path=audio_path,
                            music_path=music_path if music_path and music_path.exists() else None,
                            composition_id=requested_remotion_composition,
                            style_playbook=manifest.style_playbook or "",
                            visual_theme=str(
                                stage_runtime_overrides.get("remotion_theme", settings.remotion_theme)
                                or ""
                            ),
                            layout_variant=str(
                                stage_runtime_overrides.get("remotion_layout_variant", settings.remotion_layout_variant)
                                or ""
                            ),
                            kinetic_level=str(
                                stage_runtime_overrides.get("remotion_kinetic_level", settings.remotion_kinetic_level)
                                or ""
                            ),
                            transition_preset=str(
                                stage_runtime_overrides.get("remotion_transition_preset", settings.remotion_transition_preset)
                                or ""
                            ),
                            feature_card_mode=str(
                                stage_runtime_overrides.get("remotion_feature_card_mode", settings.remotion_feature_card_mode)
                                or ""
                            ),
                        )
                        incremental_eml_seed = editor_agent.build_incremental_eml_seed(
                            state=story,
                            media_paths=render_inputs,
                            decisions=edit_decisions,
                            audio_duration=audio_duration,
                        )

                        try:
                            edit_decisions_path, _ = build_edit_decisions_artifact(
                                timeline_payload=timeline_payload,
                                metadata={
                                    "timestamp": timestamp,
                                    "job_id": job_id,
                                    "composition_id": requested_remotion_composition,
                                    "titulo": manifest.titulo,
                                    "nicho": nicho_slug,
                                },
                                artifacts_dir=settings.temp_dir,
                                subtitles_path=ass_path if ass_path.exists() else None,
                                narration_audio_path=audio_path,
                                music_path=music_path if music_path and music_path.exists() else None,
                                incremental_eml_seed=incremental_eml_seed,
                            )
                            manifest.edit_decisions_path = str(edit_decisions_path)

                            director_path, director_meta_path, _, _ = build_director_artifacts(
                                timeline_payload=timeline_payload,
                                metadata={
                                    "timestamp": timestamp,
                                    "job_id": job_id,
                                    "composition_id": requested_remotion_composition,
                                    "titulo": manifest.titulo,
                                    "nicho": nicho_slug,
                                },
                                artifacts_dir=settings.temp_dir,
                                subtitles_path=ass_path if ass_path.exists() else None,
                            )
                            manifest.director_json_path = str(director_path)
                            manifest.director_meta_path = str(director_meta_path)
                        except Exception as exc:
                            _stage_end("validated", "error", str(exc)[:180])
                            manifest.status = JobStatus.ERROR.value
                            manifest.error_stage = "pre_render_validation"
                            manifest.error_message = f"Edit/director artifact regeneration failed: {str(exc)[:180]}"
                            manifest.error_code = ErrorCode.JSON_SCHEMA_INVALID.value
                            state_mgr.save(manifest)
                            notify_error(manifest)
                            return manifest

                        _add_decision(
                            "validated",
                            "Greenscreen clips removed and timeline rebuilt",
                            f"removed={removed_count}, clips={len(clips)}, images={len(images)}",
                            severity="warning",
                        )

                        pre_ok, pre_errors = validate_pre_render(
                            audio_path=audio_path,
                            subs_path=ass_path if ass_path.exists() else None,
                            clips=clips,
                            images=images,
                            music_path=music_path if music_path and music_path.exists() else None,
                            platform=nicho.plataforma,
                            audio_duration=audio_duration,
                        )

            if not pre_ok:
                first_code = pre_errors[0][0] if pre_errors else ErrorCode.ASSET_MISSING
                _stage_end(
                    "validated",
                    "error",
                    "; ".join(m for _, m in pre_errors)[:180],
                    metadata={
                        "error_count": len(pre_errors),
                        "tool_checks": list(pre_render_tool_checks.keys()),
                    },
                )
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "pre_render_validation"
                manifest.error_message = "; ".join(m for _, m in pre_errors)[:200]
                manifest.error_code = first_code.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.timings["validated"] = round(time.time() - validated_t0, 2)
            validated_metadata = {
                "tool_checks": list(pre_render_tool_checks.keys()),
            }
            if composition_check:
                warning_count = composition_check.get("warning_count")
                if warning_count is None and isinstance(composition_check.get("warnings"), list):
                    warning_count = len(composition_check.get("warnings", []))
                if warning_count is not None:
                    validated_metadata["composition_warning_count"] = int(warning_count)

            _stage_end("validated", "completed", metadata=validated_metadata)

            _stage_start("render", "Render")
            render_t0 = time.time()

            video_path, thumb_path, render_error, render_backend = render_video_with_fallback(
                clips=clips,
                audio_path=audio_path,
                subs_path=ass_path if ass_path.exists() else None,
                music_path=music_path if music_path and music_path.exists() else None,
                images=images,
                timestamp=timestamp,
                temp_dir=settings.temp_dir,
                output_dir=output_target,
                nicho_slug=nicho_slug,
                gancho=manifest.gancho or story.hook,
                titulo=manifest.titulo or story.topic,
                duracion_audio=audio_duration,
                velocidad=velocidad,
                num_clips=len(clips),
                duraciones_clips=duraciones,
                timeline_path=timeline_path,
                timeline_payload=timeline_payload,
                director_path=Path(manifest.director_json_path) if manifest.director_json_path else None,
                style_playbook=manifest.style_playbook or "",
            )

            if render_error or not video_path:
                render_error_code = _infer_render_error_code(render_backend, render_error)
                # Self-healing attempt
                fix = attempt_healing(
                    manifest, FailureType.RENDER, "render",
                    render_error or "No output",
                    json.dumps({"velocidad": velocidad}),
                    error_code=render_error_code,
                )
                if fix:
                    try:
                        render_fixes = json.loads(fix) if isinstance(fix, str) else fix
                        video_path, thumb_path, render_error2, render_backend2 = render_video_with_fallback(
                            clips=clips,
                            audio_path=audio_path,
                            subs_path=ass_path if ass_path.exists() else None,
                            music_path=music_path if music_path and music_path.exists() else None,
                            images=images,
                            timestamp=timestamp,
                            temp_dir=settings.temp_dir,
                            output_dir=output_target,
                            nicho_slug=nicho_slug,
                            gancho=manifest.gancho or story.hook,
                            titulo=manifest.titulo or story.topic,
                            duracion_audio=audio_duration,
                            velocidad=velocidad,
                            num_clips=len(clips),
                            duraciones_clips=duraciones,
                            timeline_path=timeline_path,
                            timeline_payload=timeline_payload,
                            render_fixes=render_fixes,
                            director_path=Path(manifest.director_json_path) if manifest.director_json_path else None,
                            style_playbook=manifest.style_playbook or "",
                        )
                        render_backend = render_backend2
                        if render_error2:
                            render_error = render_error2
                    except Exception:
                        pass

                if render_error or not video_path:
                    final_render_error_code = _infer_render_error_code(render_backend, render_error)
                    _stage_end("render", "error", (render_error or "Render failed")[:180])
                    manifest.status = JobStatus.ERROR.value
                    manifest.error_stage = "render"
                    manifest.error_message = render_error or "Render failed"
                    manifest.error_code = final_render_error_code.value
                    state_mgr.save(manifest)
                    notify_error(manifest)
                    return manifest

            manifest.video_path = str(video_path)

            split_state = dict(manifest.ab_visual_split or {})
            if bool(split_state.get("enabled", False)) and bool(split_state.get("saar_enabled", False)):
                if len(clips) < 2:
                    split_state["saar_candidate_count"] = 0
                    split_state["saar_selection_mode"] = "size_bytes_desc"
                    split_state["saar_selection_reason"] = "insufficient_clips_for_ab_split"
                    split_state["saar_winner_applied"] = False
                    _add_decision(
                        "render",
                        "SaarComposer A/B skipped",
                        "insufficient clips for A/B pairing",
                        severity="warning",
                    )
                elif not Path(audio_path).exists():
                    split_state["saar_candidate_count"] = 0
                    split_state["saar_selection_mode"] = "size_bytes_desc"
                    split_state["saar_selection_reason"] = "missing_audio_track"
                    split_state["saar_winner_applied"] = False
                    _add_decision(
                        "render",
                        "SaarComposer A/B skipped",
                        "audio track missing",
                        severity="warning",
                    )
                else:
                    try:
                        scene_data = _build_saar_scene_data(clips)
                        composer = SaarComposer(settings.temp_dir)
                        saar_prefix = f"{timestamp}_{nicho_slug}"
                        raw_candidates = composer.build_ab_split_renders(
                            scene_data=scene_data,
                            audio_track=str(audio_path),
                            output_prefix=saar_prefix,
                        )
                        candidate_paths = [Path(path) for path in raw_candidates if path]
                        selected_saar_path, saar_metadata = _select_saar_variant(
                            candidate_paths,
                            expected_duration=float(manifest.duration_seconds or audio_duration or 0.0),
                        )
                        split_state.update(saar_metadata)

                        selected_variant = str(saar_metadata.get("saar_selected_variant", "") or "")
                        if selected_variant:
                            manifest.ab_variant = selected_variant
                            split_state["selected_variant"] = selected_variant

                        if selected_saar_path and bool(split_state.get("saar_use_winner", False)):
                            if str(selected_saar_path) != str(video_path):
                                shutil.copy2(str(selected_saar_path), str(video_path))
                            split_state["saar_winner_applied"] = True
                            render_backend = "saar_composer"
                            _add_decision(
                                "render",
                                "SaarComposer winner applied",
                                f"variant={selected_variant or 'A'}",
                            )
                        elif selected_saar_path:
                            split_state["saar_winner_applied"] = False
                            _add_decision(
                                "render",
                                "SaarComposer candidates generated",
                                (
                                    f"winner={selected_variant or 'A'}, "
                                    f"apply_winner={bool(split_state.get('saar_use_winner', False))}"
                                ),
                            )
                        else:
                            split_state["saar_winner_applied"] = False
                            _add_decision(
                                "render",
                                "SaarComposer A/B produced no valid candidates",
                                str(split_state.get("saar_selection_reason", "no_valid_candidates"))[:180],
                                severity="warning",
                            )
                    except Exception as exc:
                        try:
                            split_state["saar_candidate_count"] = int(split_state.get("saar_candidate_count", 0) or 0)
                        except (TypeError, ValueError):
                            split_state["saar_candidate_count"] = 0
                        split_state["saar_selection_mode"] = str(split_state.get("saar_selection_mode", "size_bytes_desc"))
                        split_state["saar_selection_reason"] = "composer_exception"
                        split_state["saar_error"] = str(exc)[:220]
                        split_state["saar_winner_applied"] = False
                        _add_decision(
                            "render",
                            "SaarComposer A/B failed",
                            str(exc)[:180],
                            severity="warning",
                        )

                manifest.ab_visual_split = split_state

            # Optional OpenMontage enhancement chain (free/local).
            if settings.enable_openmontage_free_tools and settings.openmontage_enable_enhancement:
                graded_tmp = settings.temp_dir / f"graded_{timestamp}.mp4"
                graded_path = apply_color_grade(video_path, graded_tmp, profile="cinematic_warm")
                if graded_path and graded_path.exists():
                    shutil.move(str(graded_path), str(video_path))
                    _add_decision("render", "OpenMontage color_grade applied", video_path.name)

            # Optional OpenMontage video utilities (reframe/trim).
            if settings.enable_openmontage_free_tools and settings.openmontage_enable_video_utilities:
                platform_key = str(nicho.plataforma).lower()
                target_aspect = "portrait" if any(k in platform_key for k in ["tiktok", "reel", "short"]) else "landscape"
                reframed_tmp = settings.temp_dir / f"reframed_{timestamp}.mp4"
                reframed_path = apply_auto_reframe(video_path, reframed_tmp, target_aspect=target_aspect)
                if reframed_path and reframed_path.exists():
                    shutil.move(str(reframed_path), str(video_path))
                    _add_decision("render", "OpenMontage auto_reframe applied", target_aspect)

                trim_target = max(0.0, float(manifest.duration_seconds or audio_duration))
                if trim_target > 0:
                    trimmed_tmp = settings.temp_dir / f"trimmed_{timestamp}.mp4"
                    trimmed_path = apply_video_trim(video_path, trimmed_tmp, 0.0, trim_target)
                    if trimmed_path and trimmed_path.exists():
                        shutil.move(str(trimmed_path), str(video_path))
                        _add_decision("render", "OpenMontage video_trimmer applied", f"0-{trim_target:.2f}s")

            manifest.thumbnail_path = str(thumb_path) if thumb_path else ""
            if manifest.thumbnail_path:
                manifest.publish_cover_path = manifest.thumbnail_path
            cost_governance.record_stage_actual("render", est_render)
            manifest.timings["render"] = round(time.time() - render_t0, 2)
            manifest.render_backend = render_backend or "ffmpeg"
            _stage_end("render", "completed", metadata={"backend": manifest.render_backend})
            _add_decision(
                "render",
                f"Render completed ({manifest.render_backend})",
                Path(manifest.video_path).name,
            )
            progress.advance(main_task)

            # ── Stage 11: Post-render QA (V16: A/V sync + safe-zone + frame sampling) ──
            progress.update(main_task, description="[cyan]🔬 Post-render QA...")
            qa_t0 = time.time()
            _stage_start("qa_post", "Post-render QA")
            try:
                from pipeline.post_render_qa import post_render_qa
                from pipeline.duration_validator import get_max_duration

                platform_max_duration = float(get_max_duration(nicho.plataforma))
                qa_passed, qa_issues = post_render_qa(
                    video_path,
                    expected_width=1080, expected_height=1920,
                    min_duration=10.0, max_duration=platform_max_duration,
                    subs_path=ass_path if ass_path.exists() else None,
                    platform=nicho.plataforma,
                    reference_promise=story.reference_delivery_promise,
                    reference_avg_cut_seconds=story.reference_avg_cut_seconds,
                )
                manifest.qa_passed = qa_passed
                manifest.qa_issues = qa_issues
                manifest.post_render_report = {
                    "passed": qa_passed,
                    "issues": qa_issues,
                    "checked_at": int(time.time() * 1000),
                    "reference_promise": story.reference_delivery_promise,
                    "reference_avg_cut_seconds": story.reference_avg_cut_seconds,
                }
                _update_ab_variant_selection(qa_passed, qa_issues, qa_skipped=False)

                if not qa_passed:
                    manifest.status = JobStatus.MANUAL_REVIEW.value
                    review_path = settings.review_dir / video_path.name
                    if video_path != review_path:
                        shutil.move(str(video_path), str(review_path))
                        manifest.video_path = str(review_path)
                        video_path = review_path
                    output_target = settings.review_dir
                    _add_decision(
                        "qa_post",
                        "Post-render QA flagged manual review",
                        "; ".join(qa_issues)[:200],
                        severity="warning",
                    )
                    _stage_end("qa_post", "error", "QA issues detected")
                else:
                    _add_decision("qa_post", "Post-render QA passed")
                    _stage_end("qa_post", "completed")
            except Exception as e:
                logger.debug(f"Post-render QA skipped: {e}")
                manifest.post_render_report = {
                    "passed": True,
                    "issues": [],
                    "skipped": True,
                    "skip_reason": str(e),
                    "checked_at": int(time.time() * 1000),
                }
                _update_ab_variant_selection(True, [], qa_skipped=True)
                _stage_end("qa_post", "skipped", str(e)[:180])
            manifest.timings["qa_post"] = round(time.time() - qa_t0, 2)

            # ── Stage 12: Publish ────────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]📤 Publishing...")
            _stage_start("publish", "Publish")

            from datetime import datetime

            drive_link = "N/A"
            if settings.use_drive and video_path:
                link = upload_to_drive(video_path, video_path.name)
                if link:
                    drive_link = link
                    manifest.drive_link = link

            if settings.use_drive:
                log_to_sheets({
                    "fecha": datetime.fromtimestamp(timestamp / 1000).strftime("%Y-%m-%d"),
                    "nicho": nicho_slug,
                    "titulo": manifest.titulo,
                    "gancho": manifest.gancho,
                    "cta": manifest.cta,
                    "caption": manifest.publish_description,
                    "quality_score": manifest.quality_score,
                    "viral_score": manifest.viral_score,
                    "ab_variant": manifest.ab_variant,
                    "tts_engine": tts_engine,
                    "plataforma": nicho.plataforma,
                    "drive_link": drive_link,
                    "hashtags": manifest.publish_hashtags_text,
                    "comment": manifest.publish_comment,
                    "cover_path": manifest.publish_cover_path,
                    "version": "V15_PRO",
                    "feedback_iterations": story.feedback_iterations,
                    "scenes_count": len(story.scenes),
                })

            save_result(
                settings.supabase_url, settings.supabase_anon_key,
                nicho_slug, manifest.titulo, manifest.gancho,
                manifest.viral_score,
                raw_content.get("palabras_clave", []) if raw_content else [],
                timestamp, manifest.ab_variant, manifest.quality_score,
            )
            save_performance(
                settings.supabase_url, settings.supabase_anon_key,
                nicho_slug,
                titulo=manifest.titulo,
                gancho=manifest.gancho,
                hook_score=manifest.block_scores.hook,
                desarrollo_score=manifest.block_scores.desarrollo,
                cierre_score=manifest.block_scores.cierre,
                quality_score=manifest.quality_score,
                viral_score=manifest.viral_score,
                duration_seconds=manifest.duration_seconds,
                ab_variant=manifest.ab_variant,
                cta=manifest.cta,
                tts_engine=tts_engine,
                velocidad=velocidad,
                healing_count=len(manifest.healing_attempts),
                timestamp=timestamp,
            )

            # Feed successful outputs back into local niche memory to reduce future repetition.
            try:
                memory_candidates = [
                    f"TEMA_PUBLICADO: {manifest.titulo}".strip(),
                    f"HOOK_GANADOR: {manifest.gancho[:140]}".strip() if manifest.gancho else "",
                ]
                if story.research.recommended_angles:
                    memory_candidates.append(
                        f"ANGULO_GANADOR: {story.research.recommended_angles[0][:160]}".strip()
                    )

                existing_memory = [m.strip().lower() for m in get_niche_memory_lines(nicho_slug, limit=40) if str(m).strip()]
                for candidate in memory_candidates:
                    text = str(candidate or "").strip()
                    if not text:
                        continue

                    normalized = text.lower()
                    # Skip near-duplicates by containment to keep memory compact.
                    if any(normalized in line or line in normalized for line in existing_memory if line):
                        continue

                    add_niche_memory_entry(nicho_slug, text, source="auto_publish")
                    existing_memory.append(normalized)
            except Exception as exc:
                logger.debug(f"Local memory writeback skipped: {exc}")

            # Notifications
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                notify_review(manifest)
                _add_decision("publish", "Sent to manual review", "; ".join(manifest.qa_issues)[:200], severity="warning")
            else:
                manifest.status = JobStatus.SUCCESS.value
                notify_success(manifest, drive_link)
                _add_decision("publish", "Publish success", drive_link)

            manifest.timings["publish"] = round(time.time() - t, 2)
            _stage_end(
                "publish",
                "completed",
                metadata={"status": manifest.status, "drive_link": manifest.drive_link},
            )
            progress.advance(main_task)

            # Cleanup
            cleanup_temp(timestamp)
            archive_dir = settings.output_dir
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                archive_dir = settings.review_dir
            elif manifest.video_path:
                vpath = Path(manifest.video_path)
                if settings.review_dir in vpath.parents:
                    archive_dir = settings.review_dir

            state_mgr.archive_manifest(manifest, archive_dir)

        except Exception as e:
            logger.exception(f"V15 Pipeline crashed: {e}")
            for running_stage in list(stage_clock.keys()):
                _stage_end(running_stage, "error", str(e)[:180])
            _add_decision("pipeline", "Pipeline crashed", str(e)[:220], severity="error")
            manifest.status = JobStatus.ERROR.value
            manifest.error_stage = "unknown"
            manifest.error_message = str(e)
            manifest.error_code = ErrorCode.UNKNOWN.value
            state_mgr.save(manifest)
            notify_error(manifest)

    _print_v15_summary(manifest, story)
    return manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_platform(plataforma: str) -> str:
    p = plataforma.lower()
    if "tiktok" in p:
        return "tiktok"
    if "reel" in p or "instagram" in p:
        return "reels"
    if "short" in p or "youtube" in p:
        return "shorts"
    if "facebook" in p:
        return "facebook"
    return "shorts"


def _audience_for_nicho(slug: str) -> str:
    audiences = {
        "finanzas": "hombres 18-35, interesados en dinero y libertad financiera",
        "historia": "curiosos 20-45, fascinados por misterios y hechos oscuros",
        "curiosidades": "audiencia general 16-35, amantes de datos sorprendentes",
        "historias_reddit": "audiencia general 16-40, enganchada a drama, conflictos y plot twists",
        "ia_herramientas": "creadores y freelancers 18-40, interesados en automatizar y monetizar con IA",
    }
    return audiences.get(slug, "audiencia general interesada en contenido viral")


def _clean_tts_text(text: str) -> str:
    import re
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r'[{}\[\]|\\^~*_#@"]', " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace(", ", ", ").replace(". ", ". ")
    return text.strip()


def _print_v15_summary(manifest: JobManifest, story: StoryState):
    """Print V15 enhanced summary."""
    from rich.table import Table

    color = "green" if manifest.status == "success" else "red" if manifest.status == "error" else "yellow"
    table = Table(title=f"[{color}]V15 PRO — {manifest.status.upper()}[/{color}]")
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("Job ID", manifest.job_id)
    table.add_row("Mode", manifest.execution_mode)
    if manifest.reference_url:
        table.add_row("Reference", manifest.reference_url[:60])
    table.add_row("Nicho", manifest.nicho_slug)
    table.add_row("Platform", story.platform)
    table.add_row("Título", (manifest.titulo or story.topic)[:60])
    table.add_row("Hook", (manifest.gancho or story.hook)[:60])
    table.add_row("Quality", f"{manifest.quality_score}")
    table.add_row("Scenes", str(len(story.scenes)))
    table.add_row("Feedback Iterations", str(story.feedback_iterations))
    table.add_row("Duration", f"{manifest.duration_seconds:.1f}s")
    table.add_row("Video", manifest.video_path or "N/A")
    table.add_row("Healing", str(len(manifest.healing_attempts)))
    table.add_row("Cost", f"actual=${manifest.cost_actual_usd:.4f}, estimate=${manifest.cost_estimate_usd:.4f}")

    if manifest.budget_blocked:
        table.add_row("Budget", "[yellow]Blocked by governance policy[/yellow]")

    if manifest.timings:
        timing_str = ", ".join(f"{k}={v:.1f}s" for k, v in manifest.timings.items())
        table.add_row("Timings", timing_str)

    if manifest.error_message:
        table.add_row("Error", f"[red]{manifest.error_code}: {manifest.error_message[:60]}[/red]")

    console.print(table)
