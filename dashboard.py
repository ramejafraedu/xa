"""Video Factory V14 — Web Dashboard Server.

Serves a premium dark-mode dashboard at http://localhost:8000
with real-time log streaming, niche controls, and job history.

Usage:
    python dashboard.py              # Start dashboard on port 8000
    python dashboard.py --port 9000  # Custom port
"""
from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import FastAPI, BackgroundTasks, Request, Body
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from config import settings, NICHOS
from core.director import WEB_CHECKPOINTS, WEB_RESOLUTIONS, DirectorMode
from models.content import JobManifest, JobStatus
from state_manager import StateManager

# ---------------------------------------------------------------------------
# Log queue for real-time streaming to browser
# ---------------------------------------------------------------------------
_log_queue: queue.Queue[str] = queue.Queue(maxsize=500)


def _log_sink(message):
    """Custom loguru sink that pushes to the SSE queue."""
    record = message.record
    entry = json.dumps({
        "time": record["time"].strftime("%H:%M:%S"),
        "level": record["level"].name,
        "module": record["name"],
        "message": record["message"],
    })
    try:
        _log_queue.put_nowait(entry)
    except queue.Full:
        try:
            _log_queue.get_nowait()
            _log_queue.put_nowait(entry)
        except queue.Empty:
            pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Video Factory V14", docs_url="/api/docs")

# Static files
_static_dir = Path(__file__).resolve().parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Active pipeline runs
_active_runs: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the dashboard."""
    html_path = _static_dir / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard not found. Check static/index.html</h1>")


@app.get("/api/status")
async def system_status():
    """System health check."""
    import shutil
    ffmpeg_ok = settings.check_ffmpeg()
    disk_ok = settings.check_disk_space()
    usage = shutil.disk_usage(settings.workspace)
    missing_keys = settings.validate_required_keys()

    return {
        "ffmpeg": ffmpeg_ok,
        "disk_free_gb": round(usage.free / (1024 ** 3), 1),
        "disk_ok": disk_ok,
        "missing_keys": missing_keys,
        "workspace": str(settings.workspace),
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.get("/api/nichos")
async def list_nichos():
    """List all configured nichos with their settings."""
    result = []
    for slug, nicho in NICHOS.items():
        result.append({
            "slug": slug,
            "nombre": nicho.nombre,
            "tono": nicho.tono,
            "plataforma": nicho.plataforma,
            "voz": nicho.voz_gemini,
            "horas": nicho.horas,
            "num_clips": nicho.num_clips,
            "is_running": slug in _active_runs,
        })
    return result


@app.get("/api/jobs")
async def list_jobs():
    """List recent jobs from manifest files."""
    jobs = []
    # Check temp for in-progress
    state = StateManager(settings.temp_dir)
    resumable = state.list_resumable_jobs()
    for j in resumable:
        j["source"] = "temp"
        jobs.append(j)

    # Check output for completed
    output_dir = settings.output_dir
    if output_dir.exists():
        for f in sorted(output_dir.glob("job_manifest_*.json"), reverse=True)[:20]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                jobs.append({
                    "job_id": data.get("job_id", ""),
                    "nicho": data.get("nicho_slug", ""),
                    "status": data.get("status", ""),
                    "titulo": data.get("titulo", "")[:50],
                    "quality_score": data.get("quality_score", 0),
                    "viral_score": data.get("viral_score", 0),
                    "duration": data.get("duration_seconds", 0),
                    "video_path": data.get("video_path", ""),
                    "timestamp": data.get("timestamp", 0),
                    "timings": data.get("timings", {}),
                    "healing_count": len(data.get("healing_attempts", [])),
                    "error_message": data.get("error_message", ""),
                    "source": "output",
                })
            except Exception:
                pass

    # Check review_manual
    review_dir = settings.review_dir
    if review_dir.exists():
        for f in sorted(review_dir.glob("job_manifest_*.json"), reverse=True)[:10]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                jobs.append({
                    "job_id": data.get("job_id", ""),
                    "nicho": data.get("nicho_slug", ""),
                    "status": "manual_review",
                    "titulo": data.get("titulo", "")[:50],
                    "quality_score": data.get("quality_score", 0),
                    "timestamp": data.get("timestamp", 0),
                    "source": "review",
                })
            except Exception:
                pass

    return sorted(jobs, key=lambda x: x.get("timestamp", 0), reverse=True)[:30]


@app.post("/api/run/{nicho_slug}")
async def run_niche(nicho_slug: str, background_tasks: BackgroundTasks, dry_run: bool = False):
    """Trigger a pipeline run for a niche."""
    if nicho_slug not in NICHOS:
        return {"error": f"Unknown niche: {nicho_slug}"}
    if nicho_slug in _active_runs:
        return {"error": f"{nicho_slug} is already running", "job_id": _active_runs[nicho_slug].get("job_id")}

    _active_runs[nicho_slug] = {"started": time.time(), "job_id": "starting..."}
    background_tasks.add_task(_run_pipeline_bg, nicho_slug, dry_run)
    return {"status": "started", "nicho": nicho_slug, "dry_run": dry_run}


@app.post("/api/run-all")
async def run_all(background_tasks: BackgroundTasks):
    """Trigger all 5 nichos sequentially."""
    background_tasks.add_task(_run_all_bg)
    return {"status": "started", "nichos": list(NICHOS.keys())}


@app.post("/api/resume/{job_id}")
async def resume_job(job_id: str, background_tasks: BackgroundTasks):
    """Resume a crashed job."""
    state = StateManager(settings.temp_dir)
    manifest = state.load(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    _active_runs[manifest.nicho_slug] = {"started": time.time(), "job_id": job_id}
    background_tasks.add_task(_run_pipeline_bg, manifest.nicho_slug, False, job_id)
    return {"status": "resuming", "job_id": job_id}


@app.get("/api/active")
async def active_runs():
    """List currently running pipelines."""
    return _active_runs


@app.get("/api/logs")
async def stream_logs(request: Request):
    """Server-Sent Events endpoint for real-time logs."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                entry = _log_queue.get_nowait()
                yield {"event": "log", "data": entry}
            except queue.Empty:
                await asyncio.sleep(0.3)

    return EventSourceResponse(event_generator())


@app.get("/api/videos")
async def list_videos():
    """List generated video files."""
    videos = []
    for d in [settings.output_dir, settings.review_dir]:
        if d.exists():
            for f in sorted(d.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
                videos.append({
                    "name": f.name,
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                    "created": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "dir": d.name,
                })
    return videos


@app.get("/api/checkpoints")
async def get_checkpoints():
    """Return all pending checkpoints awaiting human approval."""
    return {"checkpoints": WEB_CHECKPOINTS}


@app.post("/api/checkpoints/{job_id}/resolve")
async def resolve_checkpoint(job_id: str, payload: dict = Body(...)):
    """Resolve a pending checkpoint with a decision."""
    if job_id not in WEB_CHECKPOINTS:
        return {"error": "Checkpoint not found", "job_id": job_id}
    
    WEB_RESOLUTIONS[job_id] = {
        "decision": payload.get("decision", "approve"),
        "notes": payload.get("notes", "")
    }
    return {"status": "resolved", "job_id": job_id}


# ---------------------------------------------------------------------------
# Background pipeline runners
# ---------------------------------------------------------------------------

def _run_pipeline_bg(nicho_slug: str, dry_run: bool = False, resume_id: str = ""):
    """Run pipeline in background thread using V15 mode WEB."""
    try:
        from core.pipeline_v15 import run_pipeline_v15
        logger.info(f"🚀 Starting V15 WEB Pipeline for {nicho_slug}")
        result = run_pipeline_v15(nicho_slug, dry_run=dry_run, resume_job_id=resume_id, mode=DirectorMode.WEB)
        if result:
            _active_runs[nicho_slug] = {
                "job_id": result.job_id,
                "status": result.status,
                "finished": time.time(),
            }
    except Exception as e:
        logger.error(f"Background run failed for {nicho_slug}: {e}")
    finally:
        _active_runs.pop(nicho_slug, None)


def _run_all_bg():
    """Run all nichos sequentially."""
    for slug in NICHOS:
        _run_pipeline_bg(slug)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Video Factory Dashboard")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    # Setup logging with dashboard sink
    logger.remove()
    settings.ensure_dirs()
    logger.add(sys.stderr, level="INFO", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")
    logger.add(str(settings.logs_dir / "factory.log"), level="DEBUG",
               rotation="10 MB", retention="7 days", compression="zip")
    logger.add(_log_sink, level="DEBUG")

    logger.info(f"🎬 Video Factory Dashboard starting on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
