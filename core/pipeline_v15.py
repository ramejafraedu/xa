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

import json
import shutil
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from agents.asset_agent import AssetAgent
from agents.editor_agent import EditorAgent
from agents.research_agent import ResearchAgent
from agents.scene_agent import SceneAgent
from agents.script_agent import ScriptAgent
from config import NICHOS, app_config, settings
from core.cost_governance import CostGovernance
from core.director import CheckpointResult, Director, DirectorMode
from core.feedback_loop import review_content, should_iterate
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
from state_manager import StateManager

console = Console()


def run_pipeline_v15(
    nicho_slug: str,
    mode: DirectorMode = DirectorMode.AUTO,
    dry_run: bool = False,
    resume_job_id: str = "",
    reference_url: str = "",
) -> JobManifest:
    """Execute the V15 multi-agent pipeline.

    This is the V15 equivalent of V14's run_pipeline().
    Compatible output: returns a JobManifest with full audit trail.

    Args:
        nicho_slug: Niche identifier.
        mode: INTERACTIVE (human checkpoints) or AUTO (autonomous).
        dry_run: If True, stop after script + scene plan.
        resume_job_id: Resume a crashed job.

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
    from pipeline.pre_render_validator import validate_pre_render
    from pipeline.cleanup import cleanup_temp, cleanup_stale_temp
    from publishers.telegram import notify_success, notify_error, notify_review
    from publishers.drive_sheets import upload_to_drive, log_to_sheets
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
    manifest.budget_daily_usd = float(settings.daily_budget_usd)
    manifest.reference_url = (reference_url or "").strip()
    if manifest.reference_url:
        manifest.reference_notes = "reference_received"

    cost_governance = CostGovernance(manifest)

    # Build initial StoryState
    story = StoryState(
        topic=nicho.nombre,
        tone=nicho.tono,
        audience=_audience_for_nicho(nicho_slug),
        platform=_resolve_platform(nicho.plataforma),
        nicho_slug=nicho_slug,
        visual_direction=nicho.direccion_visual,
        style_profile=get_style_for_platform(nicho.plataforma),
        reference_url=manifest.reference_url,
        precedence_rule=(
            "REFERENCE > RESEARCH > NICHO_DEFAULT"
            if manifest.reference_url
            else "RESEARCH > NICHO_DEFAULT"
        ),
    )

    settings.ensure_dirs()
    cleanup_stale_temp()

    # Agents
    research_agent = ResearchAgent()
    script_agent = ScriptAgent()
    scene_agent = SceneAgent()
    asset_agent = AssetAgent()
    editor_agent = EditorAgent()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        total_stages = 5 if dry_run else 12
        main_task = progress.add_task(
            f"[cyan]🎬 V15 {nicho_slug.upper()} Pipeline", total=total_stages,
        )

        try:
            # Optional reference-driven context loading.
            if manifest.reference_url:
                cache_path = settings.temp_dir / "reference_context_cache.json"
                ref_ctx = load_reference_context(manifest.reference_url, cache_path)
                if ref_ctx:
                    story.reference_title = str(ref_ctx.get("title", ""))
                    story.reference_summary = str(ref_ctx.get("summary", ""))
                    story.reference_key_points = list(ref_ctx.get("key_points", []))[:6]
                    manifest.reference_notes = "reference_context_loaded"
                else:
                    manifest.reference_notes = "reference_context_unavailable"

            # ── Stage 1: Research ────────────────────────────────────
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
            if not result.approved and result.decision.value == "reject":
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
                script_approved = result.approved or result.edited

            manifest.timings["script"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 3: Quality Gate (V14 reuse) ────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🔎 Quality Gate...")

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

            manifest.timings["quality_gate"] = round(time.time() - t, 2)
            state_mgr.mark_stage(manifest, "quality_gate")
            progress.advance(main_task)

            # ── Stage 4: Scene Planning ──────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🎬 Scene Agent...")

            scenes = scene_agent.run(story, nicho)

            # Checkpoint: scenes
            cp_result = director.checkpoint_scenes(scenes, story)
            if not cp_result.approved and cp_result.decision.value == "reject":
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
            progress.advance(main_task)

            # ── DRY RUN EXIT ─────────────────────────────────────────
            if dry_run:
                manifest.status = JobStatus.DRAFT.value
                state_mgr.save(manifest)
                console.print("\n[yellow]🏁 DRY RUN — script + scenes generated, no render.[/yellow]")
                _print_v15_summary(manifest, story)
                return manifest

            # ── Stage 6: Assets ──────────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🎨 Asset Agent...")

            allowed_assets, reason_assets, est_assets = cost_governance.reserve_stage("assets")
            if not allowed_assets:
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
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "download"
                manifest.error_message = "No clips and no images"
                manifest.error_code = ErrorCode.ASSET_MISSING.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.timings["download"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 8: TTS ─────────────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🗣️ TTS...")

            preferred_tts_provider = "gemini" if settings.provider_allowed("gemini") else "edge_tts"
            allowed_tts, reason_tts, _ = cost_governance.reserve_stage(
                "tts",
                provider=preferred_tts_provider,
            )
            if not allowed_tts:
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
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "tts"
                manifest.error_message = "TTS failed"
                manifest.error_code = ErrorCode.TTS_EMPTY_AUDIO.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.audio_path = str(audio_path)
            manifest.tts_engine_used = tts_engine
            audio_duration = get_audio_duration(audio_path)
            manifest.duration_seconds = audio_duration

            tts_actual = settings.est_cost_tts_usd if tts_engine == "gemini" else 0.0
            cost_governance.record_stage_actual("tts", tts_actual)

            manifest.timings["tts"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 9: Subtitles ───────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]📝 Subtitles...")

            ass_path = settings.temp_dir / f"subs_{timestamp}.ass"
            try:
                from pipeline.subtitles_whisperx import generate_ass_whisperx
                events = generate_ass_whisperx(audio_path, ass_path)
                if events <= 0:
                    raise Exception("WhisperX produced no events")
            except Exception as e:
                logger.warning(f"⚠️ WhisperX falló o no está instalado, usando método de respaldo: {e}")
                if vtt_path.exists() and vtt_path.stat().st_size > 20:
                    vtt_to_ass(vtt_path, ass_path)
                else:
                    generate_timed_ass_from_text(guion_tts, audio_duration, ass_path)

            manifest.subs_path = str(ass_path)

            # Duration validation
            audio_duration, was_trimmed = validate_duration(
                audio_duration, nicho.plataforma, audio_path,
            )
            if was_trimmed:
                manifest.duration_seconds = audio_duration

            manifest.timings["subtitles"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 10: Edit Decisions + Render ────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]🎥 Rendering...")

            allowed_render, reason_render, est_render = cost_governance.reserve_stage("render")
            if not allowed_render:
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "render_budget"
                manifest.error_message = reason_render
                manifest.error_code = ErrorCode.UNKNOWN.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            # Get scene-aware edit decisions
            edit_decisions = editor_agent.run(story, nicho, len(clips), audio_duration)

            # Build duraciones from edit decisions
            duraciones = [d.duration for d in edit_decisions] if edit_decisions else None

            # Determine velocidad from dominant mood
            velocidad = raw_content.get("velocidad_cortes", "rapido") if raw_content else "rapido"

            output_target = settings.output_dir
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                output_target = settings.review_dir

            # Pre-render validation
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
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "pre_render_validation"
                manifest.error_message = "; ".join(m for _, m in pre_errors)[:200]
                manifest.error_code = first_code.value
                state_mgr.save(manifest)
                notify_error(manifest)
                return manifest

            video_path, thumb_path, render_error = render_video_with_fallback(
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
                        video_path, thumb_path, render_error2 = render_video_with_fallback(
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
                            render_fixes=render_fixes,
                        )
                        if render_error2:
                            render_error = render_error2
                    except Exception:
                        pass

                if render_error or not video_path:
                    manifest.status = JobStatus.ERROR.value
                    manifest.error_stage = "render"
                    manifest.error_message = render_error or "Render failed"
                    manifest.error_code = ErrorCode.FFMPEG_FILTER_FAIL.value
                    state_mgr.save(manifest)
                    notify_error(manifest)
                    return manifest

            manifest.video_path = str(video_path)
            manifest.thumbnail_path = str(thumb_path) if thumb_path else ""
            cost_governance.record_stage_actual("render", est_render)
            manifest.timings["render"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # ── Stage 11: Post-render QA ─────────────────────────────
            progress.update(main_task, description="[cyan]🔬 Post-render QA...")
            try:
                from pipeline.post_render_qa import post_render_qa
                qa_passed, qa_issues = post_render_qa(
                    video_path,
                    expected_width=1080, expected_height=1920,
                    min_duration=10.0, max_duration=120.0,
                )
                manifest.qa_passed = qa_passed
                manifest.qa_issues = qa_issues

                if not qa_passed:
                    manifest.status = JobStatus.MANUAL_REVIEW.value
                    review_path = settings.review_dir / video_path.name
                    if video_path != review_path:
                        shutil.move(str(video_path), str(review_path))
                        manifest.video_path = str(review_path)
                        video_path = review_path
            except Exception as e:
                logger.debug(f"Post-render QA skipped: {e}")

            # ── Stage 12: Publish ────────────────────────────────────
            t = time.time()
            progress.update(main_task, description="[cyan]📤 Publishing...")

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
                    "quality_score": manifest.quality_score,
                    "viral_score": manifest.viral_score,
                    "ab_variant": manifest.ab_variant,
                    "tts_engine": tts_engine,
                    "plataforma": nicho.plataforma,
                    "drive_link": drive_link,
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
            else:
                manifest.status = JobStatus.SUCCESS.value
                notify_success(manifest, drive_link)

            manifest.timings["publish"] = round(time.time() - t, 2)
            progress.advance(main_task)

            # Cleanup
            cleanup_temp(timestamp)
            state_mgr.archive_manifest(
                manifest, output_target if video_path else settings.output_dir
            )

        except Exception as e:
            logger.exception(f"V15 Pipeline crashed: {e}")
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
