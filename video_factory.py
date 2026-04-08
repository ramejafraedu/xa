"""Video Factory V15 PRO — Director-Based Multi-Agent Video Production.

Usage:
    python video_factory.py --test              # Quick test V15 (finanzas, 1 video)
    python video_factory.py --director finanzas # Interactive mode (approve each stage)
    python video_factory.py --v15 finanzas      # V15 autonomous (multi-agent + coherence)
    python video_factory.py finanzas            # V14 classic mode (backward compat)
    python video_factory.py --all-now           # Run all 5 nichos (V15)
    python video_factory.py --schedule          # Start scheduler (V15 if SCHEDULER_USE_V15=true)
    python video_factory.py --dry-run finanzas  # Content gen + QA only, no render
    python video_factory.py --resume JOB_ID     # Resume a crashed job
    python video_factory.py --render-only JOB_ID # Re-render from existing assets
    python video_factory.py --publish-only JOB_ID # Re-publish an already-rendered video

V15 UPGRADE:
  - Multi-agent system: Research → Script → Scene → Assets → Editor
  - StoryState: global narrative memory for coherence
  - Director: human-in-the-loop checkpoints (--director mode)
  - Feedback Loop: AI quality review with auto-regeneration
  - V14 pipeline preserved as fallback

MODULE CONTRACT:
  Entry point → orchestrates all pipeline modules → produces final video + manifest
  Each stage is checkpointed via StateManager for crash recovery.
  Idempotent: re-running a completed stage with same input_hash = noop.
"""
from __future__ import annotations

import os
import json
import shutil
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# Ensure we can import from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

from config import settings, NICHOS, app_config
from models.content import (
    ABVariant,
    BlockScores,
    ErrorCode,
    FailureType,
    JobManifest,
    JobStatus,
    PipelineResult,
    VideoContent,
)
from state_manager import StateManager

# Initialize Rich console
console = Console()
app = typer.Typer(
    name="video-factory",
    help="🎬 Video Factory V15 PRO — Director-Based Multi-Agent Video Production",
    add_completion=False,
)


class _NoopProgress:
    """Fallback progress object for non-interactive environments."""

    def add_task(self, *_args, **_kwargs) -> int:
        return 1

    def update(self, *_args, **_kwargs) -> None:
        return None

    def advance(self, *_args, **_kwargs) -> None:
        return None


def _should_use_live_progress() -> bool:
    """Enable rich live progress only when attached to an interactive TTY."""
    if os.getenv("VIDEO_FACTORY_DISABLE_PROGRESS", "").strip().lower() in {"1", "true", "yes"}:
        return False
    return bool(sys.stdout and sys.stdout.isatty())


@contextmanager
def _progress_scope():
    """Return a rich Progress context on TTY, else a no-op progress facade."""
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


def _setup_logging():
    """Configure Loguru with file + console output. Secrets are never logged."""
    logger.remove()
    settings.ensure_dirs()

    # Console: colorful, human-readable
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level="INFO",
        colorize=True,
    )

    # File: structured, with rotation
    logger.add(
        str(settings.logs_dir / "factory.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
        compression="zip",
    )


def _preflight_checks() -> bool:
    """Run all system checks. Fail-fast for critical missing keys."""
    console.print(Panel("🔍 Preflight Checks", style="bold cyan"))
    checks = []

    # Fail-fast: critical keys
    try:
        settings.fail_fast_validate()
    except SystemExit as e:
        console.print(f"[red]{e}[/red]")
        return False

    # FFmpeg
    if settings.check_ffmpeg():
        checks.append(("FFmpeg", "✅", "green"))
    else:
        checks.append(("FFmpeg", "❌ Not found", "red"))
        return False

    # Disk space
    if settings.check_disk_space():
        usage = shutil.disk_usage(settings.workspace)
        free_gb = usage.free / (1024 ** 3)
        checks.append(("Disk Space", f"✅ {free_gb:.1f} GB free", "green"))
    else:
        checks.append(("Disk Space", "❌ Low", "red"))
        return False

    # Warning-level keys
    missing = settings.validate_required_keys()
    if missing:
        for key in missing:
            checks.append((key, "⚠️ Missing", "yellow"))
    else:
        checks.append(("API Keys", "✅ All set", "green"))

    # MEGA Upgrade providers status
    gemini_key_count = len(settings.get_gemini_keys())
    if gemini_key_count > 0:
        checks.append(("Gemini Keys", f"✅ {gemini_key_count}/4 keys activas (rotación)", "green"))
    else:
        checks.append(("Gemini Keys", "❌ Ninguna configurada", "red"))

    if hasattr(settings, 'use_lyria_music') and settings.use_lyria_music:
        checks.append(("Lyria 3 (AI music)", "✅ Active", "green"))
    else:
        checks.append(("Lyria 3 (AI music)", "⬜ Disabled", "dim"))

    if settings.use_whisperx:
        try:
            import whisperx
            checks.append(("WhisperX (subs)", "✅ Installed", "green"))
        except ImportError:
            checks.append(("WhisperX (subs)", "⬜ Not installed", "dim"))
    else:
        checks.append(("WhisperX (subs)", "⬜ Disabled", "dim"))

    if settings.use_piper_tts:
        if settings.piper_ready():
            checks.append(("Piper TTS (offline)", "✅ Ready", "green"))
        else:
            checks.append(("Piper TTS (offline)", "⚠️ Model missing", "yellow"))
    else:
        checks.append(("Piper TTS (offline)", "⬜ Disabled", "dim"))

    if settings.force_ffmpeg_renderer:
        checks.append(("Remotion (render)", "⬜ Forced OFF (FFmpeg only)", "dim"))
    elif settings.use_remotion:
        from pipeline.renderer_remotion import is_remotion_available
        if is_remotion_available():
            checks.append(("Remotion (render)", "✅ Ready", "green"))
        else:
            checks.append(("Remotion (render)", "⚠️ Not configured", "yellow"))
    else:
        checks.append(("Remotion (render)", "⬜ Disabled", "dim"))

    # Print results
    table = Table(title="System Status")
    table.add_column("Component", style="bold")
    table.add_column("Status")
    for name, status, color in checks:
        table.add_row(name, f"[{color}]{status}[/{color}]")
    console.print(table)

    return True


def _stage_timer():
    """Simple stage timer context."""
    return {"start": time.time()}


def _elapsed(timer: dict) -> float:
    return round(time.time() - timer["start"], 2)


def run_pipeline(
    nicho_slug: str,
    dry_run: bool = False,
    resume_job_id: str = "",
    manual_ideas: str | list[str] | None = None,
) -> JobManifest:
    """Execute the full video generation pipeline for a niche.

    Args:
        nicho_slug: Niche identifier.
        dry_run: If True, only run content_gen + quality_gate, skip render.
        resume_job_id: If set, resume this specific job instead of creating new.
        manual_ideas: Optional manual direction lines to bias topic and hook.

    Returns:
        JobManifest with full audit trail.
    """
    from pipeline.content_gen import generate_content, ContentGenerationError
    from pipeline.quality_gate import validate_and_score
    from pipeline.self_healer import attempt_healing
    from pipeline.tts_engine import generate_tts, get_audio_duration
    from pipeline.subtitles import generate_timed_ass_from_text
    from pipeline.image_gen import generate_images
    from pipeline.video_stock import fetch_stock_videos
    from pipeline.music import fetch_music
    from pipeline.sfx import fetch_sfx
    from pipeline.renderer import download_clips, render_video
    from pipeline.duration_validator import validate_duration
    from pipeline.pre_render_validator import validate_pre_render
    from pipeline.cleanup import cleanup_temp, cleanup_stale_temp
    from publishers.telegram import notify_success, notify_error, notify_review
    from publishers.drive_sheets import upload_to_drive, log_to_sheets
    from services.publish_package import build_publish_package
    from services.supabase_client import read_memory, save_result, save_performance
    from services.niche_memory import (
        build_niche_memory_context,
        get_niche_memory_lines,
        normalize_manual_ideas,
    )
    from services.trends import get_trending_context

    nicho = NICHOS.get(nicho_slug)
    if not nicho:
        raise ValueError(f"Unknown niche: {nicho_slug}. Available: {list(NICHOS.keys())}")

    state = StateManager(settings.temp_dir)

    # Resume or create new
    if resume_job_id:
        manifest = state.load(resume_job_id)
        if not manifest:
            console.print(f"[red]Job {resume_job_id} not found[/red]")
            raise typer.Exit(1)
        timestamp = manifest.timestamp
        job_id = manifest.job_id
    else:
        timestamp = int(time.time() * 1000)
        job_id = f"{nicho_slug}_{timestamp}"
        manifest = JobManifest(
            job_id=job_id,
            nicho_slug=nicho_slug,
            timestamp=timestamp,
            plataforma=nicho.plataforma,
            model_version=settings.inference_model,
        )

    # V14 entrypoint should explicitly tag manifests as v14 to avoid alert confusion.
    manifest.pipeline_type = "v14"
    manual_idea_lines = normalize_manual_ideas(manual_ideas)
    niche_memory_lines = get_niche_memory_lines(nicho_slug, limit=10)

    if resume_job_id and not manual_idea_lines and getattr(manifest, "manual_ideas", None):
        manual_idea_lines = normalize_manual_ideas(getattr(manifest, "manual_ideas", []))

    if resume_job_id and not niche_memory_lines and getattr(manifest, "niche_memory_snapshot", None):
        niche_memory_lines = [
            str(x).strip() for x in getattr(manifest, "niche_memory_snapshot", []) if str(x).strip()
        ]

    manifest.manual_ideas = manual_idea_lines
    manifest.niche_memory_snapshot = niche_memory_lines

    settings.ensure_dirs()
    cleanup_stale_temp()

    with _progress_scope() as progress:
        total_stages = 5 if dry_run else 10
        main_task = progress.add_task(
            f"[cyan]🎬 {nicho_slug.upper()} Pipeline", total=total_stages
        )

        try:
            # ── Stage 1: Read Memory ─────────────────────────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]🧠 Reading memory...")
            if not state.is_stage_done(manifest, "content_gen"):
                memoria = read_memory(
                    settings.supabase_url, settings.supabase_anon_key, nicho_slug
                )
                local_memory_ctx = build_niche_memory_context(nicho_slug, limit=8)
                if local_memory_ctx != "Sin memoria local por nicho":
                    if memoria and memoria != "Sin memoria previa":
                        memoria = f"{memoria} | MEMORIA_LOCAL: {local_memory_ctx}"
                    else:
                        memoria = f"MEMORIA_LOCAL: {local_memory_ctx}"
                if manual_idea_lines:
                    manual_ctx = " | ".join(manual_idea_lines)
                    memoria = (
                        f"{memoria} | IDEAS_MANUALES: {manual_ctx}"
                        if memoria
                        else f"IDEAS_MANUALES: {manual_ctx}"
                    )
                trending = get_trending_context(nicho.nombre, settings.rapidapi_key)
            else:
                memoria = "Sin memoria previa"
                trending = ""
            manifest.timings["memory"] = _elapsed(timer)
            progress.advance(main_task)

            # ── Stage 2: Generate Content ────────────────────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]🤖 Generating content...")
            raw_content = {}  # Inicializar antes del bloque (fix: NameError en --resume)
            if not state.is_stage_done(manifest, "content_gen"):
                try:
                    raw_content = generate_content(
                        nicho,
                        trending,
                        memoria,
                        manual_ideas=manual_idea_lines,
                    )
                except ContentGenerationError as e:
                    manifest.status = JobStatus.ERROR.value
                    manifest.error_stage = "content_gen"
                    manifest.error_message = str(e)
                    manifest.error_code = ErrorCode.CONTENT_GEN_API_FAIL.value
                    state.save(manifest)
                    notify_error(manifest)
                    return manifest

                state.mark_stage(manifest, "content_gen", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 3: Quality Gate ────────────────────────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]🔎 Quality check...")
            content, quality, errors = validate_and_score(raw_content, nicho)

            # Self-healing loop
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
                        except (json.JSONDecodeError, Exception):
                            pass
                else:
                    fix = attempt_healing(
                        manifest, FailureType.PROMPT, "quality_gate",
                        error_detail, content.guion[:500] if content else "",
                        nicho=nicho,
                        error_code=primary_code,
                    )
                    if fix:
                        try:
                            fixed_data = json.loads(fix) if isinstance(fix, str) and fix.strip().startswith("{") else raw_content
                            content, quality, errors = validate_and_score(fixed_data, nicho)
                            continue
                        except (json.JSONDecodeError, Exception):
                            pass
                break

            if content is None:
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "quality_gate"
                manifest.error_message = "Content validation failed after healing"
                manifest.error_code = ErrorCode.JSON_SCHEMA_INVALID.value
                state.save(manifest)
                notify_error(manifest)
                return manifest

            # Update manifest with content
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

            if not quality.is_approved:
                manifest.status = JobStatus.MANUAL_REVIEW.value
                logger.warning(f"Content sent to manual review (score: {quality.quality_score})")

            state.mark_stage(manifest, "quality_gate", _elapsed(timer))
            progress.advance(main_task)

            # ── DRY RUN EXIT ─────────────────────────────────────────
            if dry_run:
                manifest.status = JobStatus.DRAFT.value
                state.save(manifest)
                progress.advance(main_task)
                progress.advance(main_task)
                console.print("\n[yellow]🏁 DRY RUN complete — content generated and scored, no render.[/yellow]")
                _print_summary(manifest)
                return manifest

            # ── Stage 4: TTS ─────────────────────────────────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]🗣️ Generating TTS...")
            guion_tts = " ".join(filter(None, [content.gancho, content.guion, content.cta]))
            guion_tts = _clean_tts_text(guion_tts)

            audio_path = settings.temp_dir / f"audio_{timestamp}.mp3"
            vtt_path = settings.temp_dir / f"subs_{timestamp}.vtt"

            # Idempotency: skip if audio already exists with same input
            tts_hash = state.compute_input_hash(guion_tts)
            if state.should_skip_stage(manifest, "tts", audio_path, tts_hash):
                tts_engine = manifest.tts_engine_used or "cached"
            else:
                tts_ok, tts_engine = generate_tts(
                    guion_tts, audio_path,
                    voz_gemini=nicho.voz_gemini,
                    voz_edge=nicho.voz_edge,
                    rate_tts=nicho.rate_tts,
                    pitch_tts=nicho.pitch_tts,
                    subs_vtt_path=vtt_path,
                    enforce_provider_policy=False,
                )

                if not tts_ok:
                    fix = attempt_healing(
                        manifest, FailureType.AUDIO, "tts",
                        "TTS generation failed for both Gemini and Edge-TTS",
                        error_code=ErrorCode.TTS_EMPTY_AUDIO,
                    )
                    if fix:
                        fix_data = json.loads(fix) if isinstance(fix, str) else {}
                        if fix_data.get("action") == "retry_edge_tts":
                            tts_ok, tts_engine = generate_tts(
                                guion_tts, audio_path,
                                voz_gemini="",
                                voz_edge=nicho.voz_edge,
                                rate_tts=nicho.rate_tts,
                                pitch_tts=nicho.pitch_tts,
                                subs_vtt_path=vtt_path,
                                enforce_provider_policy=False,
                            )

                    if not tts_ok:
                        manifest.status = JobStatus.ERROR.value
                        manifest.error_stage = "tts"
                        manifest.error_message = "TTS failed after healing"
                        manifest.error_code = ErrorCode.TTS_EMPTY_AUDIO.value
                        state.save(manifest)
                        notify_error(manifest)
                        return manifest

                manifest.tts_engine_used = tts_engine

            manifest.audio_path = str(audio_path)
            state.mark_stage(manifest, "tts", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 5: Subtitles (script-locked timing) ────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]📝 Creating subtitles...")
            ass_path = settings.temp_dir / f"subs_{timestamp}.ass"
            audio_duration = get_audio_duration(audio_path)

            if not state.should_skip_stage(manifest, "subtitles", ass_path):
                subtitle_events = generate_timed_ass_from_text(guion_tts, audio_duration, ass_path)
                logger.info(f"Script-locked subtitles: {subtitle_events} events")

            manifest.subs_path = str(ass_path)
            manifest.duration_seconds = audio_duration

            # Duration validation
            audio_duration, was_trimmed = validate_duration(
                audio_duration, nicho.plataforma, audio_path,
                niche_slug=nicho.slug,
            )
            if was_trimmed:
                manifest.duration_seconds = audio_duration

            state.mark_stage(manifest, "subtitles", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 6: Media (Stock, Lyria → Pixabay) ────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]🎨 Generating media...")
            keywords = content.palabras_clave[:nicho.keywords_count]

            stock_clips = fetch_stock_videos(keywords, nicho.num_clips)
            logger.info(f"📦 Stock: fetching {len(stock_clips)} clips from Pexels")

            images = generate_images(
                content.prompt_imagen or (keywords[0] if keywords else nicho.nombre),
                nicho.direccion_visual,
                manifest.ab_variant,
                timestamp,
                settings.temp_dir,
                count=max(4, min(10, int(settings.generated_images_count))),
            )

            # --- Lyria 3 AI music (NEW — with Pixabay/Jamendo fallback) ---
            music_path = settings.temp_dir / f"musica_{timestamp}.mp3"
            try:
                from pipeline.music_ai import fetch_music_with_fallback
                fetch_music_with_fallback(
                    content.mood_musica or nicho.genero_musica,
                    music_path,
                    duration_seconds=audio_duration,
                    nicho=nicho_slug,
                )
            except Exception:
                fetch_music(content.mood_musica or nicho.genero_musica, music_path)

            sfx_paths = fetch_sfx(timestamp, settings.temp_dir)

            manifest.image_paths = [str(p) for p in images]
            manifest.sfx_paths = [str(p) for p in sfx_paths]

            state.mark_stage(manifest, "media", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 7: Download clips ─────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]⬇️ Downloading clips...")
            clips = download_clips(stock_clips, timestamp, settings.temp_dir)
            manifest.clip_paths = [str(p) for p in clips]

            if not clips and not images:
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "download"
                manifest.error_message = "No clips and no images"
                manifest.error_code = ErrorCode.ASSET_MISSING.value
                state.save(manifest)
                notify_error(manifest)
                return manifest

            logger.info(f"📊 Total clips: {len(clips)} (Stock: {len(stock_clips)})")
            state.mark_stage(manifest, "combine", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 7.5: Pre-Render Validation ─────────────────────
            progress.update(main_task, description="[cyan]✅ Validating assets...")
            pre_ok, pre_errors = validate_pre_render(
                audio_path=audio_path,
                subs_path=ass_path if ass_path.exists() else None,
                clips=clips,
                images=images,
                music_path=music_path if music_path.exists() else None,
                platform=nicho.plataforma,
                audio_duration=audio_duration,
            )

            if not pre_ok:
                first_code = pre_errors[0][0] if pre_errors else ErrorCode.ASSET_MISSING
                all_msgs = "; ".join(msg for _, msg in pre_errors)
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "pre_render_validation"
                manifest.error_message = all_msgs[:200]
                manifest.error_code = first_code.value
                state.save(manifest)
                notify_error(manifest)
                return manifest

            state.mark_stage(manifest, "validated")

            # ── Stage 8: Render ──────────────────────────────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]🎥 Rendering video...")
            output_target = settings.output_dir
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                output_target = settings.review_dir

            video_path, thumb_path, render_error = render_video(
                clips=clips,
                audio_path=audio_path,
                subs_path=ass_path if ass_path.exists() else None,
                music_path=music_path if music_path.exists() else None,
                images=images,
                timestamp=timestamp,
                temp_dir=settings.temp_dir,
                output_dir=output_target,
                nicho_slug=nicho_slug,
                gancho=content.gancho,
                titulo=content.titulo,
                duracion_audio=audio_duration,
                velocidad=content.velocidad_cortes.value if hasattr(content.velocidad_cortes, 'value') else str(content.velocidad_cortes),
                num_clips=content.num_clips,
                duraciones_clips=[float(d) for d in content.duraciones_clips] if content.duraciones_clips else None,
            )

            if render_error:
                render_code = ErrorCode.FFMPEG_FILTER_FAIL
                if "timeout" in render_error.lower():
                    render_code = ErrorCode.FFMPEG_TIMEOUT
                elif "concat" in render_error.lower():
                    render_code = ErrorCode.FFMPEG_CONCAT_FAIL

                fix = attempt_healing(
                    manifest, FailureType.RENDER, "render",
                    render_error, json.dumps({"velocidad": str(content.velocidad_cortes)}),
                    error_code=render_code,
                )
                if fix:
                    try:
                        render_fixes = json.loads(fix) if isinstance(fix, str) else fix
                        video_path, thumb_path, render_error2 = render_video(
                            clips=clips,
                            audio_path=audio_path,
                            subs_path=ass_path if ass_path.exists() else None,
                            music_path=music_path if music_path.exists() else None,
                            images=images,
                            timestamp=timestamp,
                            temp_dir=settings.temp_dir,
                            output_dir=output_target,
                            nicho_slug=nicho_slug,
                            gancho=content.gancho,
                            titulo=content.titulo,
                            duracion_audio=audio_duration,
                            velocidad=content.velocidad_cortes.value if hasattr(content.velocidad_cortes, 'value') else str(content.velocidad_cortes),
                            num_clips=content.num_clips,
                            render_fixes=render_fixes,
                        )
                        if render_error2:
                            render_error = render_error2
                    except Exception:
                        pass

            if render_error or not video_path:
                manifest.status = JobStatus.ERROR.value
                manifest.error_stage = "render"
                manifest.error_message = render_error or "Render produced no output"
                manifest.error_code = ErrorCode.FFMPEG_FILTER_FAIL.value
                state.save(manifest)
                notify_error(manifest)
                return manifest

            manifest.video_path = str(video_path)
            manifest.thumbnail_path = str(thumb_path) if thumb_path else ""
            if manifest.thumbnail_path:
                manifest.publish_cover_path = manifest.thumbnail_path
            state.mark_stage(manifest, "render", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 8.5: Post-Render QA ─────────────────────────────
            progress.update(main_task, description="[cyan]🔬 Post-render QA...")
            try:
                from pipeline.post_render_qa import post_render_qa
                qa_passed, qa_issues = post_render_qa(
                    video_path,
                    expected_width=1080,
                    expected_height=1920,
                    min_duration=10.0,
                    max_duration=120.0,
                )
                manifest.qa_passed = qa_passed
                manifest.qa_issues = qa_issues

                if not qa_passed:
                    logger.warning(f"⚠️ Post-render QA found issues — sending to review")
                    # Move to review instead of failing completely
                    if manifest.status != JobStatus.MANUAL_REVIEW.value:
                        manifest.status = JobStatus.MANUAL_REVIEW.value
                        # Move video to review dir
                        review_path = settings.review_dir / video_path.name
                        if video_path != review_path:
                            import shutil as sh
                            sh.move(str(video_path), str(review_path))
                            manifest.video_path = str(review_path)
                            video_path = review_path
            except Exception as e:
                logger.debug(f"Post-render QA skipped: {e}")

            # ── Stage 9: Publish ─────────────────────────────────────
            timer = _stage_timer()
            progress.update(main_task, description="[cyan]📤 Publishing...")

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
                    "titulo": content.titulo,
                    "gancho": content.gancho,
                    "cta": content.cta,
                    "caption": manifest.publish_description,
                    "hook_score": quality.block_scores.hook,
                    "score_desarrollo": quality.block_scores.desarrollo,
                    "score_cierre": quality.block_scores.cierre,
                    "quality_score": quality.quality_score,
                    "quality_status": quality.quality_status,
                    "ab_variant": manifest.ab_variant,
                    "viral_score": content.viral_score,
                    "velocidad": str(content.velocidad_cortes),
                    "tts_engine": tts_engine,
                    "plataforma": nicho.plataforma,
                    "num_clips": content.num_clips,
                    "hashtags": manifest.publish_hashtags_text,
                    "comment": manifest.publish_comment,
                    "cover_path": manifest.publish_cover_path,
                    "drive_link": drive_link,
                    "timestamp": timestamp,
                })

            # Save to Supabase: basic result + performance metrics
            save_result(
                settings.supabase_url, settings.supabase_anon_key,
                nicho_slug, content.titulo, content.gancho,
                content.viral_score, content.palabras_clave, timestamp,
                manifest.ab_variant, quality.quality_score,
            )
            save_performance(
                settings.supabase_url, settings.supabase_anon_key,
                nicho_slug,
                titulo=content.titulo,
                gancho=content.gancho,
                hook_score=quality.block_scores.hook,
                desarrollo_score=quality.block_scores.desarrollo,
                cierre_score=quality.block_scores.cierre,
                quality_score=quality.quality_score,
                viral_score=content.viral_score,
                duration_seconds=manifest.duration_seconds,
                ab_variant=manifest.ab_variant,
                cta=content.cta,
                tts_engine=tts_engine,
                velocidad=str(content.velocidad_cortes),
                healing_count=len(manifest.healing_attempts),
                timestamp=timestamp,
            )

            # Notifications
            if manifest.status == JobStatus.MANUAL_REVIEW.value:
                notify_review(manifest)
            else:
                manifest.status = JobStatus.SUCCESS.value
                notify_success(manifest, drive_link)

            state.mark_stage(manifest, "publish", _elapsed(timer))
            progress.advance(main_task)

            # ── Stage 10: Cleanup ────────────────────────────────────
            progress.update(main_task, description="[cyan]🧹 Cleaning up...")
            cleanup_temp(timestamp)
            # Archive manifest to output dir for audit trail
            state.archive_manifest(manifest, output_target if video_path else settings.output_dir)
            progress.advance(main_task)

        except Exception as e:
            logger.exception(f"Pipeline crashed: {e}")
            manifest.status = JobStatus.ERROR.value
            manifest.error_stage = "unknown"
            manifest.error_message = str(e)
            manifest.error_code = ErrorCode.UNKNOWN.value
            state.save(manifest)
            notify_error(manifest)

    _print_summary(manifest)
    return manifest


def _clean_tts_text(text: str) -> str:
    """Clean text for TTS input."""
    import re
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r'[{}\[\]|\\^~*_#@"]', " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace(", ", ", ").replace(". ", ". ")
    return text.strip()


def _print_summary(manifest: JobManifest):
    """Print a final summary table."""
    color = "green" if manifest.status == "success" else "red" if manifest.status == "error" else "yellow"
    table = Table(title=f"[{color}]Pipeline Result: {manifest.status.upper()}[/{color}]")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Job ID", manifest.job_id)
    table.add_row("Nicho", manifest.nicho_slug)
    table.add_row("Titulo", manifest.titulo[:60] if manifest.titulo else "N/A")
    table.add_row("Quality", f"{manifest.quality_score} (H:{manifest.block_scores.hook}/D:{manifest.block_scores.desarrollo}/C:{manifest.block_scores.cierre})")
    table.add_row("Duration", f"{manifest.duration_seconds:.1f}s")
    table.add_row("Video", manifest.video_path or "N/A")
    table.add_row("Healing", str(len(manifest.healing_attempts)))
    table.add_row("Input Hash", manifest.input_hash or "N/A")
    if manifest.timings:
        timing_str = ", ".join(f"{k}={v:.1f}s" for k, v in manifest.timings.items())
        table.add_row("Timings", timing_str)
    if manifest.error_message:
        table.add_row("Error", f"[red]{manifest.error_code}: {manifest.error_message[:60]}[/red]")
    console.print(table)


# ── CLI Commands ─────────────────────────────────────────────────────────────

@app.command()
def run(
    niche: str = typer.Argument(None, help="Niche: finanzas, historia, curiosidades, historias_reddit, ia_herramientas"),
    test: bool = typer.Option(False, "--test", help="Quick test with finanzas (V15)"),
    all_now: bool = typer.Option(False, "--all-now", help="Run all 5 nichos immediately (V15)"),
    schedule: bool = typer.Option(False, "--schedule", help="Start 24/7 scheduler"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Content gen + QA only, no render"),
    resume: str = typer.Option("", "--resume", help="Resume a crashed job by JOB_ID"),
    render_only: str = typer.Option("", "--render-only", help="Re-render from existing assets by JOB_ID"),
    publish_only: str = typer.Option("", "--publish-only", help="Re-publish already-rendered video by JOB_ID"),
    reference_url: str = typer.Option("", "--reference-url", help="Reference URL to guide script/scene generation (V15)"),
    manual_ideas: str = typer.Option("", "--manual-ideas", help="Ideas manuales prioritarias (usa | o saltos de linea)"),
    # ── V15 PRO flags ──
    director: bool = typer.Option(False, "--director", help="🎬 V15 Interactive mode (approve each stage)"),
    v15: bool = typer.Option(False, "--v15", help="🚀 V15 Autonomous mode (multi-agent + coherence)"),
    v14: bool = typer.Option(False, "--v14", help="⚙️ Force V14 classic pipeline"),
):
    """🎬 Video Factory V15 PRO — Director-Based Multi-Agent Video Production"""
    _setup_logging()

    # Determine version & mode
    use_v15 = director or v15 or test or all_now or (schedule and settings.scheduler_use_v15)
    if v14:
        use_v15 = False  # Explicit V14 override

    version_label = "V15 PRO" if use_v15 else "V14"
    mode_label = "Director (Interactive)" if director else "Autonomous" if use_v15 else "Classic"

    console.print(Panel(
        f"[bold magenta]🎬 Video Factory {version_label}[/bold magenta]\n"
        f"[dim]{mode_label} Mode[/dim]",
        border_style="magenta",
    ))

    if not _preflight_checks():
        console.print("[red]❌ Preflight checks failed.[/red]")
        raise typer.Exit(1)

    # --resume: resume a crashed job (V14 only for now)
    if resume:
        console.print(f"\n[cyan]🔄 Resuming job: {resume}[/cyan]\n")
        state = StateManager(settings.temp_dir)
        manifest = state.load(resume)
        if not manifest:
            console.print(f"[red]Job {resume} not found in temp/[/red]")
            jobs = state.list_resumable_jobs()
            if jobs:
                table = Table(title="Resumable Jobs")
                table.add_column("Job ID"); table.add_column("Nicho"); table.add_column("Status"); table.add_column("Titulo")
                for j in jobs:
                    table.add_row(j["job_id"], j["nicho"], j["status"], j["titulo"])
                console.print(table)
            raise typer.Exit(1)
        run_pipeline(
            manifest.nicho_slug,
            resume_job_id=resume,
            manual_ideas=manual_ideas or getattr(manifest, "manual_ideas", []),
        )

    elif render_only:
        console.print(f"\n[cyan]🎥 Render-only for job: {render_only}[/cyan]\n")
        run_pipeline("", resume_job_id=render_only)

    elif publish_only:
        console.print(f"\n[cyan]📤 Publish-only for job: {publish_only}[/cyan]\n")
        state = StateManager(settings.temp_dir)
        manifest = state.load(publish_only)
        if manifest and manifest.video_path:
            from publishers.telegram import notify_success
            notify_success(manifest)
            console.print("[green]✅ Notification sent[/green]")
        else:
            console.print("[red]Job not found or no video path[/red]")

    elif test:
        if use_v15:
            console.print("\n[yellow]🧪 TEST MODE — V15 PRO (finanzas)[/yellow]\n")
            from core.pipeline_v15 import run_pipeline_v15
            from core.director import DirectorMode
            mode = DirectorMode.INTERACTIVE if director else DirectorMode.AUTO
            run_pipeline_v15(
                "finanzas",
                mode=mode,
                reference_url=reference_url,
                manual_ideas=manual_ideas,
            )
        else:
            console.print("\n[yellow]🧪 TEST MODE — V14 Classic (finanzas)[/yellow]\n")
            run_pipeline("finanzas", manual_ideas=manual_ideas)

    elif dry_run and niche:
        if use_v15:
            console.print(f"\n[yellow]🏜️ DRY RUN V15 — {niche}[/yellow]\n")
            from core.pipeline_v15 import run_pipeline_v15
            from core.director import DirectorMode
            mode = DirectorMode.INTERACTIVE if director else DirectorMode.AUTO
            run_pipeline_v15(
                niche,
                mode=mode,
                dry_run=True,
                reference_url=reference_url,
                manual_ideas=manual_ideas,
            )
        else:
            console.print(f"\n[yellow]🏜️ DRY RUN V14 — {niche}[/yellow]\n")
            run_pipeline(niche, dry_run=True, manual_ideas=manual_ideas)

    elif all_now:
        console.print(f"\n[cyan]🚀 Running all 5 nichos ({version_label})...[/cyan]\n")
        for slug in NICHOS:
            console.print(f"\n{'='*60}")
            console.print(f"[bold]{slug.upper()}[/bold]")
            console.print(f"{'='*60}")
            if use_v15:
                from core.pipeline_v15 import run_pipeline_v15
                from core.director import DirectorMode
                run_pipeline_v15(
                    slug,
                    mode=DirectorMode.AUTO,
                    reference_url=reference_url,
                    manual_ideas=manual_ideas,
                )
            else:
                run_pipeline(slug, manual_ideas=manual_ideas)

    elif schedule:
        from scheduler import start_scheduler
        start_scheduler()

    elif director and niche:
        # V15 Interactive mode
        if niche not in NICHOS:
            console.print(f"[red]❌ Unknown niche: {niche}[/red]")
            console.print(f"Available: {', '.join(NICHOS.keys())}")
            raise typer.Exit(1)
        console.print(f"\n[cyan]🎬 V15 DIRECTOR MODE — {niche}[/cyan]")
        console.print("[dim]You'll approve/edit at each stage[/dim]\n")
        from core.pipeline_v15 import run_pipeline_v15
        from core.director import DirectorMode
        run_pipeline_v15(
            niche,
            mode=DirectorMode.INTERACTIVE,
            reference_url=reference_url,
            manual_ideas=manual_ideas,
        )

    elif v15 and niche:
        # V15 Autonomous mode
        if niche not in NICHOS:
            console.print(f"[red]❌ Unknown niche: {niche}[/red]")
            console.print(f"Available: {', '.join(NICHOS.keys())}")
            raise typer.Exit(1)
        console.print(f"\n[cyan]🚀 V15 AUTONOMOUS — {niche}[/cyan]\n")
        from core.pipeline_v15 import run_pipeline_v15
        from core.director import DirectorMode
        run_pipeline_v15(
            niche,
            mode=DirectorMode.AUTO,
            reference_url=reference_url,
            manual_ideas=manual_ideas,
        )

    elif niche:
        # Default: V14 classic (backward compatible)
        if niche not in NICHOS:
            console.print(f"[red]❌ Unknown niche: {niche}[/red]")
            console.print(f"Available: {', '.join(NICHOS.keys())}")
            raise typer.Exit(1)
        if use_v15:
            from core.pipeline_v15 import run_pipeline_v15
            from core.director import DirectorMode
            run_pipeline_v15(
                niche,
                mode=DirectorMode.AUTO,
                reference_url=reference_url,
                manual_ideas=manual_ideas,
            )
        else:
            run_pipeline(niche, manual_ideas=manual_ideas)

    else:
        console.print("\n[cyan]📋 Starting scheduler (24/7 mode)...[/cyan]")
        console.print("[dim]Use Ctrl+C to stop[/dim]\n")
        from scheduler import start_scheduler
        start_scheduler()


if __name__ == "__main__":
    app()
