"""Video Factory V15 PRO — Director-Based Multi-Agent Video Production.

Usage:
    python video_factory.py --test              # Quick test V15 (finanzas, 1 video)
    python video_factory.py --test curiosidades # Quick test V15 with specific niche
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


# ── Stage Methods ────────────────────────────────────────────────────────────
# Each stage is a private function that receives the shared context dict and
# returns it (or raises).  The main loop in run_pipeline() stays short.
# ─────────────────────────────────────────────────────────────────────────────

def _stage_memory(ctx: dict) -> dict:
    """Stage 1: Read Memory / Context."""
    from services.supabase_client import read_memory
    from services.niche_memory import build_niche_memory_context
    from services.trends import get_trending_context

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]🧠 Reading memory...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    nicho_slug = ctx["nicho_slug"]
    manual_idea_lines = ctx["manual_idea_lines"]

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

    ctx["memoria"] = memoria
    ctx["trending"] = trending
    manifest.timings["memory"] = _elapsed(timer)
    ctx["progress"].advance(ctx["task_id"])
    return ctx


def _stage_content_gen(ctx: dict) -> dict:
    """Stage 2: Generate Content."""
    from pipeline.content_gen import generate_content, ContentGenerationError
    from publishers.telegram import notify_error

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]🤖 Generating content...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    manual_idea_lines = ctx["manual_idea_lines"]

    if not state.is_stage_done(manifest, "content_gen"):
        try:
            ctx["raw_content"] = generate_content(
                nicho,
                ctx["trending"],
                ctx["memoria"],
                manual_ideas=manual_idea_lines,
            )
            manifest.raw_content_data = ctx["raw_content"]
        except ContentGenerationError as e:
            manifest.status = JobStatus.ERROR.value
            manifest.error_stage = "content_gen"
            manifest.error_message = str(e)
            manifest.error_code = ErrorCode.CONTENT_GEN_API_FAIL.value
            state.save(manifest)
            notify_error(manifest)
            ctx["abort"] = True
            return ctx
        state.mark_stage(manifest, "content_gen", _elapsed(timer))
    else:
        ctx["raw_content"] = manifest.raw_content_data

        state.mark_stage(manifest, "content_gen", _elapsed(timer))
    ctx["progress"].advance(ctx["task_id"])
    return ctx


def _stage_quality_gate(ctx: dict) -> dict:
    """Stage 3: Quality Gate + Self-Healing."""
    from pipeline.quality_gate import validate_and_score
    from pipeline.self_healer import attempt_healing
    from publishers.telegram import notify_error
    from services.publish_package import build_publish_package

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]🔎 Quality check...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    raw_content = ctx["raw_content"]
    
    if settings.enable_crew_quality_gate:
        try:
            from agents.crew_quality_gate import CrewQualityGate
            ctx["progress"].update(ctx["task_id"], description="[cyan]🕵️‍♂️ CrewAI Quality debate...")
            gate = CrewQualityGate(max_debate_rounds=settings.crew_max_debate_rounds)
            
            # Build context for CrewAI Editor 
            context_str = f"PRECEDENCIA: {getattr(state, 'precedence_rule', '')} | NICHO: {nicho.estilo_narrativo}"
            crew_result = gate.run(raw_content, nicho.slug, context=context_str)
            
            # Update raw_content with CrewAI's polished version
            raw_content = crew_result.verified_script_data

            # Persist debate metadata for audit and manual review context.
            manifest.crew_fact_report = crew_result.fact_report
            manifest.crew_quality_status = crew_result.quality_status
            manifest.crew_debate_log = crew_result.debate_log
            logger.info("CrewAI discussion applied successfully.")
        except Exception as e:
            logger.warning(f"CrewAI quality gate bypassed due to error: {e}")

    ctx["progress"].update(ctx["task_id"], description="[cyan]🔎 Quality check...")
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
        ctx["abort"] = True
        return ctx

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

    # ── V16.1: Mejora automática de metadatos con TitleGeneratorAgent ─────
    # Enriquece títulos, descripciones y hashtags usando Gemini Flash + SEO
    try:
        from agents.title_generator import generate_metadata
        nicho_actual = ctx.get("nicho", None)
        nicho_slug_actual = ctx.get("nicho_slug", "default")
        meta_seo = generate_metadata(
            guion=content.guion[:800],
            nicho=nicho_slug_actual,
            titulo_actual=manifest.titulo,
            variantes=3,
        )
        # Actualizar metadatos con versión SEO-optimizada
        if meta_seo.get("titulo_recomendado"):
            manifest.publish_title = meta_seo["titulo_recomendado"]
        if meta_seo.get("descripcion_recomendada"):
            manifest.publish_description = meta_seo["descripcion_recomendada"]
        if meta_seo.get("hashtags_string"):
            manifest.publish_hashtags_text = meta_seo["hashtags_string"]
        # Guardar variantes para A/B testing en el manifest
        manifest.seo_title_variants = meta_seo.get("titulos", [])
        manifest.seo_description_variants = meta_seo.get("descripciones", [])
        manifest.seo_hashtags = meta_seo.get("hashtags", [])
        logger.info(f"V16.1 TitleGenerator: título SEO → '{manifest.publish_title[:60]}'")
    except Exception as e:
        logger.warning(f"V16.1 TitleGenerator: falló (no bloqueante) — {e}")
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
    ctx["progress"].advance(ctx["task_id"])
    ctx["content"] = content
    ctx["quality"] = quality
    ctx["raw_content"] = raw_content
    return ctx


def _stage_tts(ctx: dict) -> dict:
    """Stage 4: TTS Audio Generation."""
    from pipeline.tts_engine import generate_tts, clean_tts_text
    from pipeline.self_healer import attempt_healing
    from publishers.telegram import notify_error

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]🗣️ Generating TTS...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    content = ctx["content"]
    timestamp = ctx["timestamp"]

    guion_tts = " ".join(filter(None, [content.gancho, content.guion, content.cta]))
    guion_tts = clean_tts_text(guion_tts)

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
                ctx["abort"] = True
                return ctx

        manifest.tts_engine_used = tts_engine

    manifest.audio_path = str(audio_path)
    state.mark_stage(manifest, "tts", _elapsed(timer))
    ctx["progress"].advance(ctx["task_id"])
    ctx["audio_path"] = audio_path
    ctx["vtt_path"] = vtt_path
    ctx["guion_tts"] = guion_tts
    ctx["tts_engine"] = tts_engine
    return ctx


def _stage_subtitles(ctx: dict) -> dict:
    """Stage 5: Subtitles (script-locked timing)."""
    from pipeline.tts_engine import get_audio_duration
    from pipeline.subtitles import generate_timed_ass_from_text
    from pipeline.duration_validator import validate_duration

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]📝 Creating subtitles...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    audio_path = ctx["audio_path"]
    timestamp = ctx["timestamp"]
    guion_tts = ctx["guion_tts"]

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
    ctx["progress"].advance(ctx["task_id"])
    ctx["ass_path"] = ass_path
    ctx["audio_duration"] = audio_duration
    return ctx


def _stage_media(ctx: dict) -> dict:
    """Stage 6: Media (Stock clips, Images, Music, SFX).

    V16.1: Usa VideoCompositionMasterPRO para clips 100% frescos y temáticos.
    Nunca reutiliza clips de videos anteriores. Analiza el guion escena por
    escena y selecciona clips directamente relevantes al contenido narrado.
    """
    from pipeline.image_gen import generate_images
    from pipeline.music import fetch_music
    from pipeline.sfx import fetch_sfx

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]🎨 Generating media...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    nicho_slug = ctx["nicho_slug"]
    content = ctx["content"]
    timestamp = ctx["timestamp"]
    audio_duration = ctx["audio_duration"]

    keywords = content.palabras_clave[:nicho.keywords_count]

    # ── V16.1: VideoCompositionMasterPRO ───────────────────────────────────
    # Clips 100% frescos, temáticos y sin repetición histórica.
    # Analiza el guion escena por escena con LLM antes de buscar.
    stock_clips = []
    try:
        from pipeline.composition_master import fetch_fresh_stock_videos
        ctx["progress"].update(
            ctx["task_id"],
            description="[cyan]🎬 CompositionMaster: buscando clips frescos..."
        )
        stock_clips = fetch_fresh_stock_videos(
            guion=content.guion or "",
            tema=content.titulo or " ".join(keywords[:3]),
            nicho_slug=nicho_slug,
            keywords=keywords,
            num_clips=nicho.num_clips,
            job_id=manifest.job_id,
        )
        logger.info(
            f"🎬 CompositionMaster: {len(stock_clips)} clips frescos y temáticos "
            f"seleccionados para '{content.titulo[:50]}'"
        )
        # Guardar info de composición en el manifest para auditoría
        manifest.stage_artifacts["composition_clips"] = len(stock_clips)
    except Exception as e:
        # Fallback: sistema legacy de video_stock si hay error
        logger.warning(f"CompositionMaster: fallo (usando sistema legacy) — {e}")
        from pipeline.video_stock import fetch_stock_videos
        stock_clips = fetch_stock_videos(keywords, nicho.num_clips)
        logger.info(f"📦 Stock (legacy): {len(stock_clips)} clips recuperados")

    ctx["progress"].update(ctx["task_id"], description="[cyan]🎨 Generating images...")

    images = generate_images(
        content.prompt_imagen or (keywords[0] if keywords else nicho.nombre),
        nicho.direccion_visual,
        manifest.ab_variant,
        timestamp,
        settings.temp_dir,
        count=max(4, min(10, int(settings.generated_images_count))),
    )

    # --- Lyria 3 AI music (with Pixabay/Jamendo fallback) ---
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
    ctx["progress"].advance(ctx["task_id"])
    ctx["stock_clips"] = stock_clips
    ctx["images"] = images
    ctx["music_path"] = music_path
    ctx["sfx_paths"] = sfx_paths
    ctx["keywords"] = keywords
    return ctx




def _stage_download(ctx: dict) -> dict:
    """Stage 7: Download clips + Pre-render validation."""
    from pipeline.renderer import download_clips
    from pipeline.pre_render_validator import validate_pre_render
    from publishers.telegram import notify_error

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]⬇️ Downloading clips...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho = ctx["nicho"]
    stock_clips = ctx["stock_clips"]
    images = ctx["images"]
    timestamp = ctx["timestamp"]
    audio_path = ctx["audio_path"]
    ass_path = ctx["ass_path"]
    music_path = ctx["music_path"]
    audio_duration = ctx["audio_duration"]

    clips = download_clips(stock_clips, timestamp, settings.temp_dir)
    manifest.clip_paths = [str(p) for p in clips]

    if not clips and not images:
        manifest.status = JobStatus.ERROR.value
        manifest.error_stage = "download"
        manifest.error_message = "No clips and no images"
        manifest.error_code = ErrorCode.ASSET_MISSING.value
        state.save(manifest)
        notify_error(manifest)
        ctx["abort"] = True
        return ctx

    logger.info(f"📊 Total clips: {len(clips)} (Stock: {len(stock_clips)})")
    state.mark_stage(manifest, "combine", _elapsed(timer))
    ctx["progress"].advance(ctx["task_id"])

    # ── Stage 7.5: Pre-Render Validation ──
    ctx["progress"].update(ctx["task_id"], description="[cyan]✅ Validating assets...")
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
        ctx["abort"] = True
        return ctx

    state.mark_stage(manifest, "validated")
    ctx["clips"] = clips
    return ctx


def _stage_render(ctx: dict) -> dict:
    """Stage 8: Render video + Post-render QA."""
    from pipeline.renderer import render_video
    from pipeline.self_healer import attempt_healing
    from publishers.telegram import notify_error

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]🎥 Rendering video...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    nicho_slug = ctx["nicho_slug"]
    content = ctx["content"]
    clips = ctx["clips"]
    audio_path = ctx["audio_path"]
    ass_path = ctx["ass_path"]
    music_path = ctx["music_path"]
    images = ctx["images"]
    timestamp = ctx["timestamp"]
    audio_duration = ctx["audio_duration"]

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
        manim_path=ctx.get("manim_overlay", getattr(manifest, "manim_overlay_path", None)),
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
                    manim_path=ctx.get("manim_overlay", getattr(manifest, "manim_overlay_path", None)),
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
        ctx["abort"] = True
        return ctx

    manifest.video_path = str(video_path)
    manifest.thumbnail_path = str(thumb_path) if thumb_path else ""
    if manifest.thumbnail_path:
        manifest.publish_cover_path = manifest.thumbnail_path

    # ── V16.1: Thumbnail con IA si no fue generado por el renderer ────────
    # Genera thumbnail 9:16 con Gemini Imagen 3 si no existe un thumbnail
    if not manifest.thumbnail_path or not Path(manifest.thumbnail_path).exists():
        try:
            from tools.graphics.thumbnail_generator import generate_thumbnail
            thumb_result = generate_thumbnail(
                titulo=manifest.titulo or ctx.get("nicho_slug", "video"),
                nicho=ctx.get("nicho_slug", "default"),
                hook=manifest.gancho or "",
            )
            if thumb_result.get("thumbnail_path") and Path(thumb_result["thumbnail_path"]).exists():
                manifest.thumbnail_path = thumb_result["thumbnail_path"]
                manifest.publish_cover_path = thumb_result["thumbnail_path"]
                logger.info(f"V16.1 ThumbnailGenerator: generado → {manifest.thumbnail_path}")
        except Exception as e:
            logger.warning(f"V16.1 ThumbnailGenerator: falló (no bloqueante) — {e}")

    # ── V16.1: Exportar schema de edición con FullEditingEngine ───────────
    # Genera el JSON markup completo para auditoría y posible re-render
    try:
        from tools.editing.EditingEngine import build_editing_schema
        schema_path = settings.output_dir / f"schema_{manifest.job_id}.json"
        scene_data_for_schema = [
            {"visual_1": str(p), "duration": 4.0}
            for p in ctx.get("clips", [])[:10]
        ]
        if scene_data_for_schema:
            build_editing_schema(
                scene_data=scene_data_for_schema,
                voiceover_path=str(ctx.get("audio_path", "")),
                music_path=str(ctx.get("music_path", "")),
                thumbnail_path=manifest.thumbnail_path or "",
                fx_preset="default",
                export_path=str(schema_path),
            )
            manifest.stage_artifacts["editing_schema"] = str(schema_path)
            logger.info(f"V16.1 FullEditingEngine: schema exportado → {schema_path.name}")
    except Exception as e:
        logger.warning(f"V16.1 FullEditingEngine: falló (no bloqueante) — {e}")

    state.mark_stage(manifest, "render", _elapsed(timer))
    ctx["progress"].advance(ctx["task_id"])

    # ── Stage 8.5: Post-Render QA ──
    ctx["progress"].update(ctx["task_id"], description="[cyan]🔬 Post-render QA...")
    try:
        from pipeline.post_render_qa import post_render_qa
        from pipeline.duration_validator import get_max_duration

        platform_max_duration = float(get_max_duration(ctx["nicho"].plataforma))
        qa_passed, qa_issues = post_render_qa(
            video_path,
            expected_width=1080,
            expected_height=1920,
            min_duration=10.0,
            max_duration=platform_max_duration,
        )
        manifest.qa_passed = qa_passed
        manifest.qa_issues = qa_issues

        if not qa_passed:
            logger.warning("⚠️ Post-render QA found issues — sending to review")
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

    ctx["video_path"] = video_path
    ctx["output_target"] = output_target
    return ctx


def _stage_publish(ctx: dict) -> dict:
    """Stage 9: Publish (Drive, Sheets, Supabase, Telegram)."""
    from publishers.telegram import notify_success, notify_review
    from publishers.drive_sheets import upload_to_drive, log_to_sheets
    from services.supabase_client import save_result, save_performance

    timer = _stage_timer()
    ctx["progress"].update(ctx["task_id"], description="[cyan]📤 Publishing...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    content = ctx["content"]
    quality = ctx["quality"]
    video_path = ctx["video_path"]
    timestamp = ctx["timestamp"]
    tts_engine = ctx["tts_engine"]
    nicho_slug = ctx["nicho_slug"]

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
            "plataforma": ctx["nicho"].plataforma,
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
    ctx["progress"].advance(ctx["task_id"])
    return ctx


def _stage_manim(ctx: dict) -> dict:
    """Stage X: Optional Manim Financial Animation."""
    from pipeline.manim_gen import generate_manim_overlay
    timer = _stage_timer()
    
    manifest = ctx["manifest"]
    nicho = ctx["nicho"]
    content = ctx["content"]
    timestamp = ctx["timestamp"]
    state = ctx["state"]
    manifest.manim_overlay_path = manifest.manim_overlay_path or ""
    
    if settings.enable_manim_animations and nicho.slug == settings.manim_enabled_nichos:
        # Keep this optional: failures should never break core rendering.
        ctx["progress"].update(ctx["task_id"], description="[cyan]📊 Generating Manim visuals...")
        manim_vid = generate_manim_overlay(
            content.gancho, 
            nicho.slug, 
            settings.temp_dir, 
            timestamp,
        )
        if manim_vid:
            manifest.manim_overlay_path = str(manim_vid)
            ctx["manim_overlay"] = manim_vid
            logger.info(f"Attached Manim overlay: {manim_vid.name}")

    state.mark_stage(manifest, "manim", _elapsed(timer))
    ctx["progress"].advance(ctx["task_id"])
    return ctx


def _stage_cleanup(ctx: dict) -> dict:
    """Stage 10: Cleanup temp files and archive manifest."""
    from pipeline.cleanup import cleanup_temp

    ctx["progress"].update(ctx["task_id"], description="[cyan]🧹 Cleaning up...")

    manifest = ctx["manifest"]
    state = ctx["state"]
    timestamp = ctx["timestamp"]
    video_path = ctx.get("video_path")
    output_target = ctx.get("output_target", settings.output_dir)

    cleanup_temp(timestamp)
    state.archive_manifest(manifest, output_target if video_path else settings.output_dir)
    ctx["progress"].advance(ctx["task_id"])
    return ctx


# ── Main Pipeline ────────────────────────────────────────────────────────────

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
    from pipeline.cleanup import cleanup_stale_temp
    from publishers.telegram import notify_error
    from services.niche_memory import (
        get_niche_memory_lines,
        normalize_manual_ideas,
    )

    nicho = NICHOS.get(nicho_slug)
    if not nicho:
        raise ValueError(f"Unknown niche: {nicho_slug}. Available: {list(NICHOS.keys())}")

    state = StateManager(settings.temp_dir)
    
    # Initialize cost tracking (V16 PRO)
    if hasattr(settings, 'budget_usd') and hasattr(settings, 'budget_mode'):
        state.initialize_cost_tracker(settings.budget_usd, settings.budget_mode)
    else:
        # Default budget: $10 USD in warn mode
        state.initialize_cost_tracker(10.0, "warn")

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

    # Shared context dict passed to all stage methods
    ctx: dict = {
        "manifest": manifest,
        "state": state,
        "nicho": nicho,
        "nicho_slug": nicho_slug,
        "timestamp": timestamp,
        "manual_idea_lines": manual_idea_lines,
        "abort": False,
    }

    with _progress_scope() as progress:
        total_stages = 5 if dry_run else 11
        main_task = progress.add_task(
            f"[cyan]🎬 {nicho_slug.upper()} Pipeline", total=total_stages
        )
        ctx["progress"] = progress
        ctx["task_id"] = main_task

        try:
            # ── Stages 1-3: Content Generation ──
            ctx = _stage_memory(ctx)
            if ctx.get("abort"):
                _print_summary(manifest)
                return manifest

            ctx = _stage_content_gen(ctx)
            if ctx.get("abort"):
                _print_summary(manifest)
                return manifest

            ctx = _stage_quality_gate(ctx)
            if ctx.get("abort"):
                _print_summary(manifest)
                return manifest

            # ── DRY RUN EXIT ──
            if dry_run:
                manifest.status = JobStatus.DRAFT.value
                state.save(manifest)
                progress.advance(main_task)
                progress.advance(main_task)
                console.print("\n[yellow]🏁 DRY RUN complete — content generated and scored, no render.[/yellow]")
                _print_summary(manifest)
                return manifest

            # ── Stages 4-10: Production ──
            for stage_fn in [
                _stage_tts,
                _stage_subtitles,
                _stage_media,
                _stage_download,
                _stage_manim,
                _stage_render,
                _stage_publish,
                _stage_cleanup,
            ]:
                ctx = stage_fn(ctx)
                if ctx.get("abort"):
                    break

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
    test: bool = typer.Option(False, "--test", help="Quick test (default: finanzas, or specify niche)"),
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
        # --test accepts optional niche: --test curiosidades
        test_niche = niche or "finanzas"
        if test_niche not in NICHOS:
            console.print(f"[red]❌ Unknown niche: {test_niche}[/red]")
            console.print(f"Available: {', '.join(NICHOS.keys())}")
            raise typer.Exit(1)

        if use_v15:
            console.print(f"\n[yellow]🧪 TEST MODE — V15 PRO ({test_niche})[/yellow]\n")
            from core.pipeline_v15 import run_pipeline_v15
            from core.director import DirectorMode
            mode = DirectorMode.INTERACTIVE if director else DirectorMode.AUTO
            run_pipeline_v15(
                test_niche,
                mode=mode,
                reference_url=reference_url,
                manual_ideas=manual_ideas,
            )
        else:
            console.print(f"\n[yellow]🧪 TEST MODE — V14 Classic ({test_niche})[/yellow]\n")
            run_pipeline(test_niche, manual_ideas=manual_ideas)

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
