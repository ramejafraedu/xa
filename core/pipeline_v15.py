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
from core.openmontage_free import (
    apply_auto_reframe,
    apply_color_grade,
    apply_playbook_to_story,
    apply_video_trim,
    generate_vtt_from_audio,
)
from core.reference_context import load_reference_context
from core.state import StoryState, get_style_for_platform
from models.content import (
    BlockScores,
    ErrorCode,
    FailureType,
    JobManifest,
    JobStatus,
    VideoContent,
)
from services.niche_memory import get_niche_memory_lines, normalize_manual_ideas
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


def run_pipeline_v15(
    nicho_slug: str,
    mode: DirectorMode = DirectorMode.AUTO,
    dry_run: bool = False,
    resume_job_id: str = "",
    reference_url: str = "",
    manual_ideas: str | list[str] | None = None,
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
    from pipeline.subtitles import vtt_to_ass, generate_timed_ass_from_text
    from pipeline.quality_gate import validate_and_score
    from pipeline.self_healer import attempt_healing
    from pipeline.renderer import download_clips
    from pipeline.renderer_remotion import render_video_with_fallback
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

        updated_running_entry = False
        for entry in reversed(manifest.stage_trace):
            if entry.get("stage") == stage_key and entry.get("state") == "running":
                entry["state"] = state
                entry["ended_at"] = int(time.time() * 1000)
                entry["elapsed_seconds"] = elapsed
                if detail:
                    entry["detail"] = detail
                if metadata:
                    entry["metadata"] = metadata
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
                "metadata": metadata or {},
            })

        checkpoint_status = "completed" if state == "completed" else "error" if state == "error" else state
        checkpoint_artifacts = _checkpoint_artifacts(stage_key)
        try:
            state_mgr.write_stage_checkpoint(
                manifest,
                stage=stage_key,
                status=checkpoint_status,
                artifacts=checkpoint_artifacts,
                metadata=metadata or {},
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
            }
        elif stage_key == "publish":
            artifacts["publish_log"] = {
                "status": manifest.status,
                "drive_link": manifest.drive_link,
            }
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
            progress.advance(main_task)

            # ── Stage 2: Script Generation (prompt chaining) ─────────
            t = time.time()
            progress.update(main_task, description="[cyan]✍️ Script Agent...")

            script_approved = False
            script_attempts = 0

            while not script_approved and script_attempts < 3:
                correction = ""
                if script_attempts > 0:
                    correction = result.notes if hasattr(result, 'notes') else ""

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
                script_approved = result.approved or result.edited

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

            assets = asset_agent.run(story, nicho, timestamp, settings.temp_dir)

            stock_urls = assets.get("stock_clips", [])
            images = assets.get("images", [])
            music_path = assets.get("music_path")
            sfx_paths = assets.get("sfx_paths", [])

            manifest.image_paths = [str(p) for p in images]
            manifest.sfx_paths = [str(p) for p in sfx_paths]

            # Checkpoint: assets
            asset_summary = (
                f"📦 Stock clips: {len(stock_urls)}\n"
                f"🖼️ Images: {len(images)}\n"
                f"🎵 Music: {'✅' if music_path else '❌'}\n"
                f"🔊 SFX: {len(sfx_paths)}"
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
            )
            progress.advance(main_task)

            # ── Stage 8: TTS ─────────────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🗣️ TTS...")
            _stage_start("tts", "Narration TTS")

            if settings.elevenlabs_api_key and settings.provider_allowed("elevenlabs", usage="media"):
                preferred_tts_provider = "elevenlabs"
            elif settings.provider_allowed("gemini", usage="media"):
                preferred_tts_provider = "gemini"
            else:
                preferred_tts_provider = "edge_tts"
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

            # Use scene-joined text or V14 style
            guion_tts = story.scene_texts_joined() or " ".join(
                filter(None, [manifest.gancho, manifest.guion, manifest.cta])
            )
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
                    )

                # Last-resort reliability fallback:
                # if strict-free blocks Gemini and edge path failed, try Gemini once.
                if (
                    not tts_ok
                    and settings.v15_strict_free_media_tools
                    and bool(settings.gemini_api_key)
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

            manifest.audio_path = str(audio_path)
            manifest.tts_engine_used = tts_engine
            audio_duration = get_audio_duration(audio_path)
            manifest.duration_seconds = audio_duration

            tts_actual = settings.est_cost_tts_usd if tts_engine in {"gemini", "elevenlabs"} else 0.0
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
            subtitles_ready = False

            # Primary subtitles provider: AssemblyAI (when API key is configured).
            if settings.assemblyai_api_key:
                try:
                    from pipeline.subtitles_assemblyai import generate_ass_assemblyai

                    ass_events = generate_ass_assemblyai(
                        audio_path=audio_path,
                        ass_path=ass_path,
                        api_key=settings.assemblyai_api_key,
                        language_code="es",
                    )
                    if ass_events > 0:
                        subtitles_ready = True
                        _add_decision(
                            "subtitles",
                            "AssemblyAI subtitles applied (primary)",
                            f"events={ass_events}",
                        )
                except Exception as assembly_exc:
                    logger.warning(
                        "AssemblyAI subtitles failed, falling back to WhisperX/OpenMontage: "
                        f"{assembly_exc}"
                    )

            if not subtitles_ready:
                try:
                    from pipeline.subtitles_whisperx import generate_ass_whisperx

                    events = generate_ass_whisperx(audio_path, ass_path)
                    if events <= 0:
                        raise Exception("WhisperX produced no events")
                    subtitles_ready = True
                    _add_decision(
                        "subtitles",
                        "WhisperX subtitle fallback applied",
                        f"events={events}",
                    )
                except Exception as whisper_exc:
                    logger.warning(
                        "⚠️ WhisperX fallback failed, using secondary fallback: "
                        f"{whisper_exc}"
                    )

            if not subtitles_ready:
                om_vtt_path = generate_vtt_from_audio(audio_path, settings.temp_dir)
                if om_vtt_path and om_vtt_path.exists() and om_vtt_path.stat().st_size > 20:
                    events = vtt_to_ass(om_vtt_path, ass_path)
                    subtitles_ready = events > 0
                    _add_decision(
                        "subtitles",
                        "OpenMontage subtitle_gen applied",
                        om_vtt_path.name,
                    )
                elif vtt_path.exists() and vtt_path.stat().st_size > 20:
                    events = vtt_to_ass(vtt_path, ass_path)
                    subtitles_ready = events > 0
                    if subtitles_ready:
                        _add_decision(
                            "subtitles",
                            "Edge VTT subtitle fallback applied",
                            vtt_path.name,
                        )
                else:
                    events = generate_timed_ass_from_text(guion_tts, audio_duration, ass_path)
                    subtitles_ready = events > 0
                    _add_decision(
                        "subtitles",
                        "Timed text subtitle fallback applied",
                        f"events={events}",
                    )

            manifest.subs_path = str(ass_path)

            # Duration validation
            audio_duration, was_trimmed = validate_duration(
                audio_duration, nicho.plataforma, audio_path,
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
            )
            manifest.timeline_json_path = str(timeline_path)
            manifest.timings["combine"] = round(time.time() - combine_t0, 2)
            _stage_end(
                "combine",
                "completed",
                metadata={"timeline_scenes": len(timeline_payload.get("scenes", []))},
            )
            _add_decision(
                "combine",
                "Timeline assembled",
                f"scenes={len(timeline_payload.get('scenes', []))}",
            )

            # Determine velocidad from dominant mood
            velocidad = raw_content.get("velocidad_cortes", "rapido") if raw_content else "rapido"

            output_target = settings.output_dir
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                output_target = settings.review_dir

            # Pre-render validation
            validated_t0 = time.time()
            _stage_start("validated", "Pre-render Validation")
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
                        )

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
                _stage_end("validated", "error", "; ".join(m for _, m in pre_errors)[:180])
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "pre_render_validation"
                manifest.error_message = "; ".join(m for _, m in pre_errors)[:200]
                manifest.error_code = first_code.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.timings["validated"] = round(time.time() - validated_t0, 2)
            _stage_end("validated", "completed")

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
            )

            if render_error or not video_path:
                # Self-healing attempt
                fix = attempt_healing(
                    manifest, FailureType.RENDER, "render",
                    render_error or "No output",
                    json.dumps({"velocidad": velocidad}),
                    error_code=ErrorCode.FFMPEG_FILTER_FAIL,
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
                        )
                        render_backend = render_backend2
                        if render_error2:
                            render_error = render_error2
                    except Exception:
                        pass

                if render_error or not video_path:
                    _stage_end("render", "error", (render_error or "Render failed")[:180])
                    manifest.status = JobStatus.ERROR.value
                    manifest.error_stage = "render"
                    manifest.error_message = render_error or "Render failed"
                    manifest.error_code = ErrorCode.FFMPEG_FILTER_FAIL.value
                    state_mgr.save(manifest)
                    notify_error(manifest)
                    return manifest

            manifest.video_path = str(video_path)

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
                qa_passed, qa_issues = post_render_qa(
                    video_path,
                    expected_width=1080, expected_height=1920,
                    min_duration=10.0, max_duration=120.0,
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
        "salud": "mujeres 25-50, interesadas en bienestar y longevidad",
        "recetas": "mujeres 20-45, buscando recetas fáciles y deliciosas",
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
