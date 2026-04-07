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
import shutil
import sys
import threading
import time
from urllib.parse import quote
from datetime import date, datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import FastAPI, BackgroundTasks, Request, Body, HTTPException
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


def _read_json_file(path: Path) -> Optional[dict]:
    """Read JSON file and return dict when valid."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _manifest_path_candidates(job_id: str) -> list[Path]:
    """Return potential locations for a manifest file."""
    filename = f"job_manifest_{job_id}.json"
    return [
        settings.temp_dir / filename,
        settings.output_dir / filename,
        settings.review_dir / filename,
    ]


def _load_manifest_by_job_id(job_id: str) -> Optional[dict]:
    """Load manifest from temp/output/review locations."""
    for path in _manifest_path_candidates(job_id):
        data = _read_json_file(path)
        if data:
            data["_manifest_path"] = str(path)
            return data
    return None


def _collect_recent_manifests(limit: int = 50) -> list[dict]:
    """Collect recent manifests across temp/output/review."""
    manifest_files: list[Path] = []
    for folder in [settings.temp_dir, settings.output_dir, settings.review_dir]:
        if folder.exists():
            manifest_files.extend(folder.glob("job_manifest_*.json"))

    manifest_files = sorted(
        set(manifest_files),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:limit]

    results: list[dict] = []
    for path in manifest_files:
        data = _read_json_file(path)
        if data:
            data["_manifest_path"] = str(path)
            results.append(data)
    return results


def _resolve_downloadable_video(video_name: str, dir_hint: str = "") -> Optional[Path]:
    """Resolve a video file from output/review directories safely."""
    safe_name = Path(video_name).name
    if safe_name != video_name:
        return None

    ordered_dirs: list[Path] = []
    normalized_hint = (dir_hint or "").strip().lower()
    if normalized_hint in {"output"}:
        ordered_dirs.append(settings.output_dir)
    elif normalized_hint in {"review", "review_manual"}:
        ordered_dirs.append(settings.review_dir)

    for base in [settings.output_dir, settings.review_dir]:
        if base not in ordered_dirs:
            ordered_dirs.append(base)

    for base in ordered_dirs:
        candidate = base / safe_name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _clean_manifest_for_save(manifest: dict) -> dict:
    """Remove transient keys before writing manifest JSON."""
    return {k: v for k, v in manifest.items() if not str(k).startswith("_")}


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
        manifest = _load_manifest_by_job_id(j.get("job_id", ""))
        if manifest:
            j["execution_mode"] = manifest.get("execution_mode", settings.execution_mode_label())
            j["reference_url"] = manifest.get("reference_url", "")
            j["reference_delivery_promise"] = manifest.get("reference_delivery_promise", "")
            j["reference_hook_seconds"] = manifest.get("reference_hook_seconds", 0.0)
            j["reference_avg_cut_seconds"] = manifest.get("reference_avg_cut_seconds", 0.0)
            j["cost_actual_usd"] = manifest.get("cost_actual_usd", 0.0)
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
                    "execution_mode": data.get("execution_mode", settings.execution_mode_label()),
                    "reference_url": data.get("reference_url", ""),
                    "reference_delivery_promise": data.get("reference_delivery_promise", ""),
                    "reference_hook_seconds": data.get("reference_hook_seconds", 0.0),
                    "reference_avg_cut_seconds": data.get("reference_avg_cut_seconds", 0.0),
                    "timeline_json_path": data.get("timeline_json_path", ""),
                    "cost_actual_usd": data.get("cost_actual_usd", 0.0),
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
                    "execution_mode": data.get("execution_mode", settings.execution_mode_label()),
                    "reference_url": data.get("reference_url", ""),
                    "reference_delivery_promise": data.get("reference_delivery_promise", ""),
                    "reference_hook_seconds": data.get("reference_hook_seconds", 0.0),
                    "reference_avg_cut_seconds": data.get("reference_avg_cut_seconds", 0.0),
                    "timeline_json_path": data.get("timeline_json_path", ""),
                    "cost_actual_usd": data.get("cost_actual_usd", 0.0),
                    "source": "review",
                })
            except Exception:
                pass

    return sorted(jobs, key=lambda x: x.get("timestamp", 0), reverse=True)[:30]


@app.post("/api/run/{nicho_slug}")
async def run_niche(
    nicho_slug: str,
    background_tasks: BackgroundTasks,
    dry_run: bool = False,
    reference_url: str = "",
):
    """Trigger a pipeline run for a niche."""
    if nicho_slug not in NICHOS:
        return {"error": f"Unknown niche: {nicho_slug}"}
    if nicho_slug in _active_runs:
        return {"error": f"{nicho_slug} is already running", "job_id": _active_runs[nicho_slug].get("job_id")}

    _active_runs[nicho_slug] = {
        "started": time.time(),
        "job_id": "starting...",
        "reference_url": reference_url,
    }
    background_tasks.add_task(_run_pipeline_bg, nicho_slug, dry_run, "", reference_url)
    return {
        "status": "started",
        "nicho": nicho_slug,
        "dry_run": dry_run,
        "reference_url": reference_url,
    }


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

    _active_runs[manifest.nicho_slug] = {
        "started": time.time(),
        "job_id": job_id,
        "reference_url": getattr(manifest, "reference_url", ""),
    }
    background_tasks.add_task(
        _run_pipeline_bg,
        manifest.nicho_slug,
        False,
        job_id,
        getattr(manifest, "reference_url", ""),
    )
    return {"status": "resuming", "job_id": job_id}


@app.get("/api/active")
async def active_runs():
    """List currently running pipelines."""
    return _active_runs


@app.get("/api/jobs/{job_id}")
async def job_detail(job_id: str):
    """Return full manifest details for a specific job."""
    manifest = _load_manifest_by_job_id(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    manifest["associated_reference"] = {
        "url": manifest.get("reference_url", ""),
        "notes": manifest.get("reference_notes", ""),
        "delivery_promise": manifest.get("reference_delivery_promise", ""),
        "hook_seconds": manifest.get("reference_hook_seconds", 0.0),
        "avg_cut_seconds": manifest.get("reference_avg_cut_seconds", 0.0),
        "video_available": manifest.get("reference_video_available", False),
        "analysis": manifest.get("reference_analysis", {}),
    }
    return manifest


@app.get("/api/review")
async def review_queue():
    """List jobs currently in manual review queue."""
    items = []
    if settings.review_dir.exists():
        for path in sorted(settings.review_dir.glob("job_manifest_*.json"), reverse=True)[:50]:
            data = _read_json_file(path)
            if not data:
                continue
            items.append(
                {
                    "job_id": data.get("job_id", ""),
                    "nicho": data.get("nicho_slug", ""),
                    "status": data.get("status", ""),
                    "titulo": data.get("titulo", "")[:70],
                    "qa_issues": data.get("qa_issues", []),
                    "reference_url": data.get("reference_url", ""),
                    "timestamp": data.get("timestamp", 0),
                    "video_path": data.get("video_path", ""),
                    "manifest_path": str(path),
                }
            )

    items = sorted(items, key=lambda x: x.get("timestamp", 0), reverse=True)
    return {
        "count": len(items),
        "items": items,
    }


@app.get("/api/review/{job_id}")
async def review_detail(job_id: str):
    """Return full review payload for a single manual-review job."""
    manifest = _load_manifest_by_job_id(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    return {
        "job_id": manifest.get("job_id", ""),
        "status": manifest.get("status", ""),
        "nicho": manifest.get("nicho_slug", ""),
        "titulo": manifest.get("titulo", ""),
        "video_path": manifest.get("video_path", ""),
        "qa_issues": manifest.get("qa_issues", []),
        "timings": manifest.get("timings", {}),
        "associated_reference": {
            "url": manifest.get("reference_url", ""),
            "notes": manifest.get("reference_notes", ""),
            "delivery_promise": manifest.get("reference_delivery_promise", ""),
            "hook_seconds": manifest.get("reference_hook_seconds", 0.0),
            "avg_cut_seconds": manifest.get("reference_avg_cut_seconds", 0.0),
            "video_available": manifest.get("reference_video_available", False),
            "analysis": manifest.get("reference_analysis", {}),
        },
        "manifest_path": manifest.get("_manifest_path", ""),
    }


@app.post("/api/review/{job_id}/approve")
async def review_approve(job_id: str):
    """Approve a manual-review job and move manifest/video to output."""
    manifest = _load_manifest_by_job_id(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    source_manifest_path = Path(str(manifest.get("_manifest_path", "")))
    now_iso = datetime.now().isoformat(timespec="seconds")

    # Move reviewed video to output if currently under review dir.
    video_path_str = str(manifest.get("video_path", "") or "")
    if video_path_str:
        video_path = Path(video_path_str)
        if video_path.exists() and settings.review_dir in video_path.parents:
            target_video = settings.output_dir / video_path.name
            if target_video.exists():
                target_video = settings.output_dir / f"{video_path.stem}_{job_id}.mp4"
            target_video.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(video_path), str(target_video))
            manifest["video_path"] = str(target_video)

    manifest["status"] = JobStatus.SUCCESS.value
    manifest["error_stage"] = ""
    manifest["error_message"] = ""
    manifest["review_resolution"] = "approved"
    manifest["review_resolved_at"] = now_iso

    output_manifest_path = settings.output_dir / f"job_manifest_{job_id}.json"
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    output_manifest_path.write_text(
        json.dumps(_clean_manifest_for_save(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if source_manifest_path.exists() and source_manifest_path != output_manifest_path:
        source_manifest_path.unlink(missing_ok=True)

    return {
        "status": "approved",
        "job_id": job_id,
        "manifest_path": str(output_manifest_path),
        "video_path": manifest.get("video_path", ""),
    }


@app.post("/api/review/{job_id}/reject")
async def review_reject(job_id: str, payload: dict = Body({})):
    """Reject a manual-review job and persist rejection reason."""
    manifest = _load_manifest_by_job_id(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    reason = str(payload.get("reason", "") or "Rejected in manual review").strip()
    source_manifest_path = Path(str(manifest.get("_manifest_path", "")))
    review_manifest_path = settings.review_dir / f"job_manifest_{job_id}.json"
    review_manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest["status"] = JobStatus.ERROR.value
    manifest["error_stage"] = "manual_review"
    manifest["error_message"] = reason
    manifest["review_resolution"] = "rejected"
    manifest["review_resolved_at"] = datetime.now().isoformat(timespec="seconds")

    review_manifest_path.write_text(
        json.dumps(_clean_manifest_for_save(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if source_manifest_path.exists() and source_manifest_path != review_manifest_path:
        source_manifest_path.unlink(missing_ok=True)

    return {
        "status": "rejected",
        "job_id": job_id,
        "reason": reason,
        "manifest_path": str(review_manifest_path),
    }


@app.get("/api/execution-mode")
async def execution_mode_status():
    """Return current execution mode and active feature flags."""
    return {
        "mode": settings.execution_mode_label(),
        "feature_flags": settings.active_feature_flags(),
        "daily_budget_usd": float(settings.daily_budget_usd),
    }


@app.get("/api/providers/status")
async def provider_status():
    """Return provider health/scoring state used by ProviderSelector."""
    provider_state_path = settings.temp_dir / "provider_health.json"
    provider_state = _read_json_file(provider_state_path) or {}
    return {
        "mode": settings.execution_mode_label(),
        "feature_flags": settings.active_feature_flags(),
        "provider_state_path": str(provider_state_path),
        "provider_health": provider_state,
    }


@app.get("/api/costs")
async def costs_summary():
    """Return cost governance summary and recent per-job costs."""
    budget_state = _read_json_file(settings.budget_state_path) or {}
    today_key = date.today().isoformat()
    today_spend = float(budget_state.get(today_key, 0.0))
    daily_budget = float(settings.daily_budget_usd)
    remaining = round(max(daily_budget - today_spend, 0.0), 4) if daily_budget > 0 else None

    manifests = _collect_recent_manifests(limit=60)
    total_actual = round(sum(float(m.get("cost_actual_usd", 0.0)) for m in manifests), 4)
    total_estimate = round(sum(float(m.get("cost_estimate_usd", 0.0)) for m in manifests), 4)

    recent_jobs = []
    for m in manifests[:20]:
        recent_jobs.append({
            "job_id": m.get("job_id", ""),
            "nicho": m.get("nicho_slug", ""),
            "status": m.get("status", ""),
            "execution_mode": m.get("execution_mode", settings.execution_mode_label()),
            "reference_url": m.get("reference_url", ""),
            "cost_actual_usd": float(m.get("cost_actual_usd", 0.0)),
            "cost_estimate_usd": float(m.get("cost_estimate_usd", 0.0)),
            "budget_blocked": bool(m.get("budget_blocked", False)),
            "timestamp": m.get("timestamp", 0),
        })

    return {
        "mode": settings.execution_mode_label(),
        "feature_flags": settings.active_feature_flags(),
        "daily_budget_usd": daily_budget,
        "today_spend_usd": round(today_spend, 4),
        "remaining_budget_usd": remaining,
        "recent_jobs_total_actual_usd": total_actual,
        "recent_jobs_total_estimate_usd": total_estimate,
        "budget_state": budget_state,
        "jobs": recent_jobs,
    }


@app.get("/api/health/trends")
async def trends_health(nicho_slug: str = "finanzas"):
    """Quick health snapshot of research/trending sources for a niche."""
    nicho_obj = NICHOS.get(nicho_slug)
    query = nicho_obj.nombre if nicho_obj else nicho_slug

    try:
        from services.trends import get_trending_signals

        signals = get_trending_signals(query, settings.rapidapi_key)
        return {
            "nicho_slug": nicho_slug,
            "query": query,
            "sources": {
                "google_trends": len(signals.get("google_trends", [])),
                "youtube_hot": len(signals.get("youtube_hot", [])),
                "reddit_hot": len(signals.get("reddit_hot", [])),
                "news_headlines": len(signals.get("news_headlines", [])),
                "tiktok_hashtags": len(signals.get("tiktok_hashtags", [])),
            },
            "merged_topics": signals.get("merged_topics", []),
            "cache_ttl_seconds": signals.get("cache_ttl_seconds", 0),
        }
    except Exception as exc:
        return {
            "nicho_slug": nicho_slug,
            "query": query,
            "error": str(exc),
        }


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
                    "download_url": f"/api/videos/download/{quote(f.name)}?dir={d.name}",
                })
    return videos


@app.get("/api/videos/download/{video_name}")
async def download_video(video_name: str, dir: str = ""):
    """Download a generated MP4 from output/review directories."""
    video_path = _resolve_downloadable_video(video_name, dir)
    if not video_path:
        raise HTTPException(status_code=404, detail="Video not found")

    return FileResponse(
        path=str(video_path),
        filename=video_path.name,
        media_type="video/mp4",
    )


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

def _run_pipeline_bg(
    nicho_slug: str,
    dry_run: bool = False,
    resume_id: str = "",
    reference_url: str = "",
):
    """Run pipeline in background thread using V15 mode WEB."""
    try:
        from core.pipeline_v15 import run_pipeline_v15
        logger.info(f"🚀 Starting V15 WEB Pipeline for {nicho_slug}")
        result = run_pipeline_v15(
            nicho_slug,
            dry_run=dry_run,
            resume_job_id=resume_id,
            mode=DirectorMode.WEB,
            reference_url=reference_url,
        )
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
