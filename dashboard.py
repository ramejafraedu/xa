"""Video Factory V16 — Web Dashboard Server (Enhanced).

Serves a premium dark-mode dashboard at http://localhost:8000
with real-time log streaming, system resource monitoring, job details,
post-render analysis, pipeline timeline, and decision audit trail.

ALL features run with FREE tools only: psutil, ffprobe, Python stdlib.

Usage:
    python dashboard.py              # Start dashboard on port 8000
    python dashboard.py --port 9000  # Custom port
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
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
from services.niche_memory import (
    add_niche_memory_entry,
    delete_niche_memory_entry,
    get_niche_memory_lines,
    list_niche_memory,
    move_niche_memory_entry,
    update_niche_memory_entry,
)
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
app = FastAPI(title="Video Factory V16", docs_url="/api/docs")

# Static files
_static_dir = Path(__file__).resolve().parent / "static"
_static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Active pipeline runs
_active_runs: dict[str, dict] = {}
_theme_proposals_path = settings.temp_dir / "theme_proposals.json"


def _read_theme_proposals_store() -> dict:
    data = _read_json_file(_theme_proposals_path)
    return data if isinstance(data, dict) else {}


def _write_theme_proposals_store(data: dict) -> None:
    settings.ensure_dirs()
    _theme_proposals_path.parent.mkdir(parents=True, exist_ok=True)
    _theme_proposals_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _persist_env_updates(updates: dict[str, str]) -> str:
    """Persist a small whitelist of runtime ops flags to .env."""
    env_path = settings.base_dir / ".env"
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    for key, value in updates.items():
        replaced = False
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        for idx, line in enumerate(lines):
            if pattern.match(line):
                lines[idx] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(env_path)


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "si", "s"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
    return default


def _recent_titles_for_nicho(nicho_slug: str, limit: int = 14) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for manifest in _collect_recent_manifests(limit=120):
        if str(manifest.get("nicho_slug", "")).strip().lower() != nicho_slug:
            continue
        title = str(manifest.get("titulo", "") or manifest.get("publish_title", "")).strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        titles.append(title)
        if len(titles) >= max(1, int(limit)):
            break
    return titles


def _extract_json_payload(text: str) -> object:
    """Best-effort extraction of JSON object/array from LLM text output."""
    clean = re.sub(r"```json\s*", "", str(text or ""), flags=re.IGNORECASE)
    clean = re.sub(r"```\s*", "", clean).strip()

    try:
        return json.loads(clean)
    except Exception:
        pass

    # Try array first
    start_arr = clean.find("[")
    end_arr = clean.rfind("]")
    if start_arr != -1 and end_arr > start_arr:
        candidate = clean[start_arr:end_arr + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    # Try object next
    start_obj = clean.find("{")
    end_obj = clean.rfind("}")
    if start_obj != -1 and end_obj > start_obj:
        candidate = clean[start_obj:end_obj + 1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    return []


def _normalize_theme_proposals(raw: object, count: int) -> list[dict]:
    proposals_raw = raw
    if isinstance(raw, dict):
        proposals_raw = raw.get("proposals", [])

    if not isinstance(proposals_raw, list):
        proposals_raw = []

    proposals: list[dict] = []
    for idx, item in enumerate(proposals_raw):
        if idx >= count:
            break

        if isinstance(item, str):
            title = item.strip()
            angle = ""
            hook = ""
            score = 7.0
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("tema") or "").strip()
            angle = str(item.get("angle") or item.get("angulo") or "").strip()
            hook = str(item.get("hook") or item.get("gancho") or "").strip()
            try:
                score = float(item.get("viral_score", item.get("score", 7.0)) or 7.0)
            except (TypeError, ValueError):
                score = 7.0
        else:
            continue

        if not title:
            continue

        score = max(0.0, min(10.0, float(score)))
        proposals.append({
            "id": uuid.uuid4().hex[:12],
            "title": title[:120],
            "angle": angle[:180],
            "hook": hook[:180],
            "viral_score": round(score, 1),
        })

    return proposals


def _fallback_theme_proposals(nicho_slug: str, count: int, seed_topics: list[str]) -> list[dict]:
    proposals: list[dict] = []
    for idx, topic in enumerate(seed_topics[:count]):
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            continue
        proposals.append({
            "id": uuid.uuid4().hex[:12],
            "title": clean_topic[:120],
            "angle": f"Revelar una perspectiva contraintuitiva sobre {clean_topic}"[:180],
            "hook": f"Esto cambia cómo entiendes {clean_topic}"[:180],
            "viral_score": round(max(6.5, 8.6 - (idx * 0.3)), 1),
        })

    while len(proposals) < count:
        n = len(proposals) + 1
        proposals.append({
            "id": uuid.uuid4().hex[:12],
            "title": f"Tema nuevo {n} para {nicho_slug}",
            "angle": "Contarlo con conflicto, evidencia y giro final",
            "hook": "Lo que casi nadie te explicó de este tema",
            "viral_score": 7.0,
        })

    return proposals[:count]


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


def _checkpoint_dir_candidates(job_id: str) -> list[Path]:
    """Return potential locations for stage checkpoint directories."""
    return [
        settings.temp_dir / "checkpoints" / job_id,
        settings.output_dir / "checkpoints" / job_id,
        settings.review_dir / "checkpoints" / job_id,
    ]


def _load_stage_checkpoints(job_id: str) -> dict:
    """Load stage checkpoints from dual-write checkpoint folders."""
    checkpoints: dict = {}
    for cp_dir in _checkpoint_dir_candidates(job_id):
        if not cp_dir.exists():
            continue
        for cp_file in sorted(cp_dir.glob("checkpoint_*.json")):
            stage = cp_file.stem.replace("checkpoint_", "", 1)
            data = _read_json_file(cp_file)
            if not data:
                continue
            checkpoints[stage] = {
                "status": data.get("status", ""),
                "timestamp": data.get("timestamp", ""),
                "path": str(cp_file),
                "checkpoint_policy": data.get("checkpoint_policy", ""),
                "human_approval_required": bool(data.get("human_approval_required", False)),
                "human_approved": bool(data.get("human_approved", False)),
            }
        if checkpoints:
            break
    return checkpoints


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
# ffprobe helper (FREE — local FFmpeg)
# ---------------------------------------------------------------------------

def _run_ffprobe(video_path: str) -> Optional[dict]:
    """Run ffprobe to extract video metadata. Returns None on failure."""
    if not video_path or not Path(video_path).exists():
        return None
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(video_path),
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)

        # Extract useful fields
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

        width = int(video_stream.get("width", 0))
        height = int(video_stream.get("height", 0))
        duration = float(fmt.get("duration", 0))
        bitrate = int(fmt.get("bit_rate", 0))
        size_bytes = int(fmt.get("size", 0))

        # Compute quality score heuristic (free, no AI)
        quality_score = _compute_quality_score(
            width, height, duration, bitrate, size_bytes
        )

        return {
            "duration": round(duration, 2),
            "width": width,
            "height": height,
            "resolution": f"{width}x{height}",
            "video_codec": video_stream.get("codec_name", "unknown"),
            "audio_codec": audio_stream.get("codec_name", "unknown"),
            "fps": _parse_fps(video_stream.get("r_frame_rate", "0/1")),
            "bitrate_kbps": round(bitrate / 1000, 1) if bitrate else 0,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "format": fmt.get("format_name", "unknown"),
            "audio_channels": int(audio_stream.get("channels", 0)),
            "audio_sample_rate": int(audio_stream.get("sample_rate", 0)),
            "quality_score": quality_score,
        }
    except Exception as e:
        logger.debug(f"ffprobe failed for {video_path}: {e}")
        return None


def _parse_fps(rate_str: str) -> float:
    """Parse ffprobe r_frame_rate like '30000/1001'."""
    try:
        parts = rate_str.split("/")
        if len(parts) == 2 and int(parts[1]) != 0:
            return round(int(parts[0]) / int(parts[1]), 2)
        return float(parts[0])
    except (ValueError, ZeroDivisionError):
        return 0.0


def _compute_quality_score(
    width: int, height: int, duration: float,
    bitrate: int, size_bytes: int,
) -> dict:
    """Compute a free quality score based on ffprobe metadata."""
    scores = {}
    total = 0

    # Resolution score (0-10)
    pixels = width * height
    if pixels >= 1920 * 1080:
        scores["resolution"] = 10
    elif pixels >= 1080 * 1920:
        scores["resolution"] = 9
    elif pixels >= 720 * 1280:
        scores["resolution"] = 7
    elif pixels >= 480 * 854:
        scores["resolution"] = 5
    else:
        scores["resolution"] = 3
    total += scores["resolution"]

    # Duration score for short-form (30-90s ideal)
    if 25 <= duration <= 90:
        scores["duration"] = 10
    elif 15 <= duration <= 120:
        scores["duration"] = 8
    elif 10 <= duration <= 180:
        scores["duration"] = 6
    else:
        scores["duration"] = 4
    total += scores["duration"]

    # Bitrate score (higher is better for quality, but too high wastes space)
    kbps = bitrate / 1000 if bitrate else 0
    if 2000 <= kbps <= 8000:
        scores["bitrate"] = 10
    elif 1000 <= kbps <= 12000:
        scores["bitrate"] = 8
    elif 500 <= kbps:
        scores["bitrate"] = 6
    else:
        scores["bitrate"] = 3
    total += scores["bitrate"]

    # Aspect ratio score (9:16 vertical is ideal for shorts)
    if width > 0 and height > 0:
        ratio = width / height
        if 0.5 <= ratio <= 0.6:  # 9:16 vertical
            scores["aspect_ratio"] = 10
        elif 1.7 <= ratio <= 1.8:  # 16:9 horizontal
            scores["aspect_ratio"] = 7
        elif 0.9 <= ratio <= 1.1:  # Square
            scores["aspect_ratio"] = 6
        else:
            scores["aspect_ratio"] = 4
    else:
        scores["aspect_ratio"] = 0
    total += scores["aspect_ratio"]

    scores["overall"] = round(total / 4, 1)
    return scores


def _generate_thumbnail(video_path: str) -> Optional[str]:
    """Generate a thumbnail from a video using ffmpeg. Returns base64 data URI."""
    if not video_path or not Path(video_path).exists():
        return None
    try:
        import base64
        import tempfile
        thumb_path = Path(tempfile.mktemp(suffix=".jpg"))
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ss", "2", "-vframes", "1",
            "-vf", "scale=320:-1",
            "-q:v", "4",
            str(thumb_path),
        ]
        subprocess.run(cmd, capture_output=True, timeout=10)
        if thumb_path.exists() and thumb_path.stat().st_size > 0:
            data = base64.b64encode(thumb_path.read_bytes()).decode()
            thumb_path.unlink(missing_ok=True)
            return f"data:image/jpeg;base64,{data}"
        thumb_path.unlink(missing_ok=True)
    except Exception as e:
        logger.debug(f"Thumbnail generation failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Resource Monitor (FREE — psutil)
# ---------------------------------------------------------------------------

def _get_system_resources() -> dict:
    """Get system resource usage using psutil (free, local)."""
    try:
        import psutil  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

        cpu_pct = psutil.cpu_percent(interval=0.5)
        cpu_freq = psutil.cpu_freq()
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = shutil.disk_usage(str(settings.workspace))

        # Top processes by CPU
        top_procs = []
        try:
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                info = proc.info
                if info["cpu_percent"] and info["cpu_percent"] > 0.5:
                    top_procs.append({
                        "pid": info["pid"],
                        "name": info["name"],
                        "cpu": round(info["cpu_percent"], 1),
                        "mem": round(info["memory_percent"] or 0, 1),
                    })
            top_procs = sorted(top_procs, key=lambda p: p["cpu"], reverse=True)[:5]
        except Exception:
            pass

        return {
            "cpu": {
                "percent": cpu_pct,
                "cores": psutil.cpu_count(logical=False) or 1,
                "threads": psutil.cpu_count(logical=True) or 1,
                "freq_mhz": round(cpu_freq.current, 0) if cpu_freq else 0,
            },
            "ram": {
                "total_gb": round(mem.total / (1024 ** 3), 2),
                "used_gb": round(mem.used / (1024 ** 3), 2),
                "available_gb": round(mem.available / (1024 ** 3), 2),
                "percent": mem.percent,
            },
            "swap": {
                "total_gb": round(swap.total / (1024 ** 3), 2),
                "used_gb": round(swap.used / (1024 ** 3), 2),
                "percent": swap.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024 ** 3), 1),
                "used_gb": round(disk.used / (1024 ** 3), 1),
                "free_gb": round(disk.free / (1024 ** 3), 1),
                "percent": round((disk.used / disk.total) * 100, 1),
            },
            "top_processes": top_procs,
            "timestamp": time.time(),
        }
    except ImportError:
        return {
            "error": "psutil not installed. Run: pip install psutil",
            "cpu": {"percent": 0, "cores": 0, "threads": 0, "freq_mhz": 0},
            "ram": {"total_gb": 0, "used_gb": 0, "available_gb": 0, "percent": 0},
            "swap": {"total_gb": 0, "used_gb": 0, "percent": 0},
            "disk": {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0},
            "top_processes": [],
            "timestamp": time.time(),
        }
    except Exception as e:
        return {"error": str(e), "timestamp": time.time()}


# ---------------------------------------------------------------------------
# Pipeline Timeline Stages
# ---------------------------------------------------------------------------

PIPELINE_STAGES = [
    {"key": "content_gen", "label": "Content Gen", "icon": "📝", "color": "#8b5cf6"},
    {"key": "quality_gate", "label": "Quality Gate", "icon": "🔍", "color": "#3b82f6"},
    {"key": "tts", "label": "TTS Audio", "icon": "🔊", "color": "#06b6d4"},
    {"key": "subtitles", "label": "Subtitles", "icon": "💬", "color": "#14b8a6"},
    {"key": "media", "label": "Media/Stock", "icon": "🎨", "color": "#10b981"},
    {"key": "combine", "label": "Combine", "icon": "🔗", "color": "#eab308"},
    {"key": "validated", "label": "Validate", "icon": "✅", "color": "#f59e0b"},
    {"key": "render", "label": "Render", "icon": "🎬", "color": "#f97316"},
    {"key": "qa_post", "label": "Post-QA", "icon": "🏥", "color": "#ec4899"},
    {"key": "publish", "label": "Publish", "icon": "🚀", "color": "#ef4444"},
]


def _get_pipeline_timeline(manifest: dict) -> list[dict]:
    """Build timeline from explicit stage_trace first, then fallback to timings/status."""
    status = str(manifest.get("status", "pending") or "pending")
    timings = manifest.get("timings", {}) if isinstance(manifest.get("timings"), dict) else {}
    trace = manifest.get("stage_trace", []) if isinstance(manifest.get("stage_trace"), list) else []

    stage_aliases: dict[str, list[str]] = {
        "content_gen": ["content_gen", "research", "script", "scene_plan", "review"],
        "quality_gate": ["quality_gate"],
        "tts": ["tts"],
        "subtitles": ["subtitles"],
        "media": ["media", "assets", "download"],
        "combine": ["combine"],
        "validated": ["validated", "pre_render_validation"],
        "render": ["render"],
        "qa_post": ["qa_post"],
        "publish": ["publish"],
    }

    def _stage_idx(stage_key: str) -> int:
        for idx, stage_def in enumerate(PIPELINE_STAGES):
            if stage_def["key"] == stage_key:
                return idx
        return -1

    def _canonical_stage(raw_stage: str) -> str:
        key = (raw_stage or "").strip().lower()
        for timeline_key, aliases in stage_aliases.items():
            if key in aliases:
                return timeline_key
        return key

    trace_events: dict[str, list[dict]] = {}
    for item in trace:
        if not isinstance(item, dict):
            continue
        stage_key = _canonical_stage(str(item.get("stage", "")))
        if stage_key not in stage_aliases:
            continue
        trace_events.setdefault(stage_key, []).append(item)

    timeline: list[dict] = []
    if trace_events:
        for stage in PIPELINE_STAGES:
            key = stage["key"]
            events = trace_events.get(key, [])

            state = "pending"
            if any(str(e.get("state", "")).lower() == "error" for e in events):
                state = "error"
            elif any(str(e.get("state", "")).lower() == "running" for e in events):
                state = "active"
            elif any(str(e.get("state", "")).lower() in {"completed", "skipped"} for e in events):
                state = "completed"

            elapsed = round(sum(float(timings.get(alias, 0) or 0) for alias in stage_aliases[key]), 2)
            if elapsed <= 0:
                for e in events:
                    try:
                        elapsed += float(e.get("elapsed_seconds", 0) or 0)
                    except Exception:
                        pass

            timeline.append({
                **stage,
                "state": state,
                "elapsed": round(elapsed, 2),
            })

        return timeline

    elapsed_list = [
        round(sum(float(timings.get(alias, 0) or 0) for alias in stage_aliases[stage["key"]]), 2)
        for stage in PIPELINE_STAGES
    ]

    completed_idx = max([i for i, elapsed in enumerate(elapsed_list) if elapsed > 0], default=-1)
    active_idx = completed_idx + 1 if completed_idx + 1 < len(PIPELINE_STAGES) else completed_idx

    if status in {"success", "completed_publish"}:
        completed_idx = len(PIPELINE_STAGES) - 1
        active_idx = -1
    elif status.startswith("completed_"):
        canonical = _canonical_stage(status.replace("completed_", ""))
        mapped = _stage_idx(canonical)
        if mapped >= 0:
            completed_idx = max(completed_idx, mapped)
            active_idx = min(mapped + 1, len(PIPELINE_STAGES) - 1)
    elif status == "running" and completed_idx < 0:
        active_idx = 0

    error_idx = -1
    if status in {"error", "manual_review"}:
        error_idx = _stage_idx(_canonical_stage(str(manifest.get("error_stage", ""))))
        if error_idx < 0 and status == "manual_review":
            error_idx = _stage_idx("qa_post")

    for i, stage in enumerate(PIPELINE_STAGES):
        state = "pending"
        if i <= completed_idx:
            state = "completed"
        elif i == active_idx and status in {"running", "pending"}:
            state = "active"

        if error_idx >= 0:
            if i < error_idx:
                state = "completed"
            elif i == error_idx:
                state = "error"

        timeline.append({
            **stage,
            "state": state,
            "elapsed": elapsed_list[i],
        })

    return timeline


# ---------------------------------------------------------------------------
# Decision Audit Trail
# ---------------------------------------------------------------------------

def _get_decision_trail(manifest: dict) -> list[dict]:
    """Build a decision audit trail from manifest data."""
    stage_icons = {
        "content_gen": "📝",
        "quality_gate": "🔍",
        "tts": "🔊",
        "subtitles": "💬",
        "media": "🎨",
        "combine": "🔗",
        "validated": "✅",
        "render": "🎬",
        "qa_post": "🏥",
        "publish": "🚀",
        "pipeline": "⚙️",
        "governance": "💰",
    }

    explicit = manifest.get("decision_trail", [])
    if isinstance(explicit, list) and explicit:
        normalized: list[dict] = []
        for item in explicit:
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage", "pipeline") or "pipeline")
            normalized.append({
                "stage": stage,
                "icon": stage_icons.get(stage, "📌"),
                "label": str(item.get("label", "Decision") or "Decision"),
                "detail": str(item.get("detail", "") or "")[:220],
                "timestamp": int(item.get("timestamp", manifest.get("timestamp", 0)) or 0),
                "severity": str(item.get("severity", "info") or "info"),
                "metadata": item.get("metadata", {}),
            })

        if normalized:
            return sorted(normalized, key=lambda x: x.get("timestamp", 0))

    decisions = []

    # Content generation decision
    if manifest.get("titulo"):
        decisions.append({
            "stage": "content_gen",
            "icon": "📝",
            "label": "Content Generated",
            "detail": f"Title: {manifest.get('titulo', '')[:60]}",
            "timestamp": manifest.get("timestamp", 0),
            "model": manifest.get("model_version", ""),
        })

    # Quality gate
    if manifest.get("quality_score", 0) > 0:
        score = manifest.get("quality_score", 0)
        decisions.append({
            "stage": "quality_gate",
            "icon": "🔍",
            "label": f"Quality Gate: {'PASS' if score >= 7 else 'FAIL'}",
            "detail": f"Score: {score}/10, Hook: {manifest.get('hook_score', 0)}/10",
            "timestamp": manifest.get("timestamp", 0),
        })

    # TTS engine decision
    if manifest.get("tts_engine_used"):
        decisions.append({
            "stage": "tts",
            "icon": "🔊",
            "label": f"TTS: {manifest.get('tts_engine_used')}",
            "detail": f"Audio: {Path(manifest.get('audio_path', '')).name or 'N/A'}",
            "timestamp": manifest.get("timestamp", 0),
        })

    # Media decisions
    clip_count = len(manifest.get("clip_paths", []))
    img_count = len(manifest.get("image_paths", []))
    if clip_count or img_count:
        decisions.append({
            "stage": "media",
            "icon": "🎨",
            "label": f"Media: {clip_count} clips, {img_count} images",
            "detail": "Stock video + generated assets",
            "timestamp": manifest.get("timestamp", 0),
        })

    # Render
    if manifest.get("video_path"):
        decisions.append({
            "stage": "render",
            "icon": "🎬",
            "label": "Video Rendered",
            "detail": Path(manifest.get("video_path", "")).name or "N/A",
            "timestamp": manifest.get("timestamp", 0),
        })

    # Healing attempts
    for heal in manifest.get("healing_attempts", []):
        decisions.append({
            "stage": heal.get("stage", "unknown"),
            "icon": "🔄",
            "label": f"Self-Heal #{heal.get('attempt', '?')}: {'✅' if heal.get('success') else '❌'}",
            "detail": heal.get("error_message", "")[:80],
            "timestamp": manifest.get("timestamp", 0),
        })

    # QA issues
    for issue in manifest.get("qa_issues", []):
        decisions.append({
            "stage": "qa_post",
            "icon": "⚠️",
            "label": "QA Issue",
            "detail": str(issue)[:80],
            "timestamp": manifest.get("timestamp", 0),
        })

    # Error
    if manifest.get("error_message"):
        decisions.append({
            "stage": manifest.get("error_stage", "unknown"),
            "icon": "❌",
            "label": f"Error at {manifest.get('error_stage', 'unknown')}",
            "detail": manifest.get("error_message", "")[:120],
            "timestamp": manifest.get("timestamp", 0),
        })

    # Cost
    if manifest.get("cost_actual_usd", 0) > 0:
        decisions.append({
            "stage": "governance",
            "icon": "💰",
            "label": f"Cost: ${manifest.get('cost_actual_usd', 0):.4f}",
            "detail": json.dumps(manifest.get("cost_breakdown", {})),
            "timestamp": manifest.get("timestamp", 0),
        })

    return decisions


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


@app.get("/job/{job_id}/manifest", response_class=HTMLResponse)
async def manifest_view(job_id: str):
    """Serve dedicated manifest viewer route."""
    html_path = _static_dir / "manifest.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Manifest viewer not found. Check static/manifest.html</h1>")


@app.get("/api/status")
async def system_status():
    """System health check."""
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


@app.get("/api/resources")
async def system_resources():
    """Real-time system resource monitor (CPU, RAM, Swap, Disk)."""
    return _get_system_resources()


@app.get("/api/resources/stream")
async def stream_resources(request: Request):
    """Server-Sent Events stream with periodic system resource snapshots."""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            payload = _get_system_resources()
            yield {"event": "resources", "data": json.dumps(payload)}
            await asyncio.sleep(2.0)

    return EventSourceResponse(event_generator())


@app.get("/api/nichos")
async def list_nichos():
    """List all configured nichos with their settings."""
    result = []
    memory_index = list_niche_memory()
    for slug, nicho in NICHOS.items():
        result.append({
            "slug": slug,
            "nombre": nicho.nombre,
            "tono": nicho.tono,
            "plataforma": nicho.plataforma,
            "voz": nicho.voz_gemini,
            "horas": nicho.horas,
            "num_clips": nicho.num_clips,
            "memory_count": len(memory_index.get(slug, [])),
            "is_running": slug in _active_runs,
        })
    return result


@app.get("/api/memory")
async def memory_list(nicho_slug: str = ""):
    """List niche memory notes for one niche or all niches."""
    try:
        memory_data = list_niche_memory(nicho_slug or None)
        total_items = sum(len(v) for v in memory_data.values())
        return {
            "nicho_slug": (nicho_slug or "all").strip().lower() or "all",
            "total_items": total_items,
            "memory": memory_data,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/memory/{nicho_slug}")
async def memory_detail(nicho_slug: str):
    """List all memory notes for one niche."""
    try:
        memory_data = list_niche_memory(nicho_slug)
        slug = nicho_slug.strip().lower()
        return {
            "nicho_slug": slug,
            "count": len(memory_data.get(slug, [])),
            "items": memory_data.get(slug, []),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/memory/{nicho_slug}")
async def memory_add(nicho_slug: str, payload: dict = Body(...)):
    """Add a new memory note for a niche."""
    try:
        entry = add_niche_memory_entry(
            nicho_slug,
            str(payload.get("text", "") or ""),
            source=str(payload.get("source", "manual") or "manual"),
        )
        return {
            "status": "created",
            "nicho_slug": nicho_slug.strip().lower(),
            "entry": entry,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/memory/{nicho_slug}/{entry_id}")
async def memory_update(nicho_slug: str, entry_id: str, payload: dict = Body(...)):
    """Update a memory note text."""
    try:
        updated = update_niche_memory_entry(
            nicho_slug,
            entry_id,
            str(payload.get("text", "") or ""),
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {
            "status": "updated",
            "nicho_slug": nicho_slug.strip().lower(),
            "entry": updated,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/memory/{nicho_slug}/{entry_id}")
async def memory_delete(nicho_slug: str, entry_id: str):
    """Delete one memory note by id."""
    try:
        deleted = delete_niche_memory_entry(nicho_slug, entry_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {
            "status": "deleted",
            "nicho_slug": nicho_slug.strip().lower(),
            "entry_id": entry_id,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/memory/{nicho_slug}/{entry_id}/move")
async def memory_move(nicho_slug: str, entry_id: str, payload: dict = Body(...)):
    """Move one memory note from source niche to target niche."""
    target_nicho = str(payload.get("target_nicho", "") or "").strip().lower()
    if not target_nicho:
        raise HTTPException(status_code=400, detail="target_nicho is required")

    try:
        moved = move_niche_memory_entry(nicho_slug, target_nicho, entry_id)
        if not moved:
            raise HTTPException(status_code=404, detail="Memory entry not found")
        return {
            "status": "moved",
            "from": nicho_slug.strip().lower(),
            "to": target_nicho,
            "entry": moved,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/jobs")
async def list_jobs(limit: int = 30, offset: int = 0):
    """List recent jobs from manifest files."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    def _publish_fields(data: dict) -> dict:
        hashtags = data.get("publish_hashtags", [])
        if isinstance(hashtags, str):
            hashtags = [tag.strip() for tag in hashtags.split() if tag.strip()]
        if not isinstance(hashtags, list):
            hashtags = []

        return {
            "publish_title": data.get("publish_title", data.get("titulo", "")),
            "publish_description": data.get("publish_description", data.get("caption", "")),
            "publish_hashtags": hashtags,
            "publish_hashtags_text": data.get("publish_hashtags_text", " ".join(hashtags)),
            "publish_comment": data.get("publish_comment", ""),
            "publish_cover_path": data.get("publish_cover_path", data.get("thumbnail_path", "")),
            "caption": data.get("caption", ""),
            "titulo_full": data.get("titulo", ""),
        }

    jobs = []
    # Check temp for in-progress
    state = StateManager(settings.temp_dir)
    resumable = state.list_resumable_jobs()
    for j in resumable:
        j["source"] = "temp"
        manifest = _load_manifest_by_job_id(j.get("job_id", ""))
        if manifest:
            j["execution_mode"] = manifest.get("execution_mode", settings.execution_mode_label())
            j["render_backend"] = manifest.get("render_backend", "")
            j["reference_url"] = manifest.get("reference_url", "")
            j["manual_ideas"] = manifest.get("manual_ideas", [])
            j["reference_delivery_promise"] = manifest.get("reference_delivery_promise", "")
            j["reference_hook_seconds"] = manifest.get("reference_hook_seconds", 0.0)
            j["reference_avg_cut_seconds"] = manifest.get("reference_avg_cut_seconds", 0.0)
            j["cost_actual_usd"] = manifest.get("cost_actual_usd", 0.0)
            j.update(_publish_fields(manifest))
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
                    "manual_ideas": data.get("manual_ideas", []),
                    "reference_delivery_promise": data.get("reference_delivery_promise", ""),
                    "reference_hook_seconds": data.get("reference_hook_seconds", 0.0),
                    "reference_avg_cut_seconds": data.get("reference_avg_cut_seconds", 0.0),
                    "timeline_json_path": data.get("timeline_json_path", ""),
                    "cost_actual_usd": data.get("cost_actual_usd", 0.0),
                    "render_backend": data.get("render_backend", ""),
                    **_publish_fields(data),
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
                    "manual_ideas": data.get("manual_ideas", []),
                    "reference_delivery_promise": data.get("reference_delivery_promise", ""),
                    "reference_hook_seconds": data.get("reference_hook_seconds", 0.0),
                    "reference_avg_cut_seconds": data.get("reference_avg_cut_seconds", 0.0),
                    "timeline_json_path": data.get("timeline_json_path", ""),
                    "cost_actual_usd": data.get("cost_actual_usd", 0.0),
                    "render_backend": data.get("render_backend", ""),
                    **_publish_fields(data),
                    "source": "review",
                })
            except Exception:
                pass

    ordered_jobs = sorted(jobs, key=lambda x: x.get("timestamp", 0), reverse=True)
    return ordered_jobs[offset: offset + limit]


@app.post("/api/run/{nicho_slug}")
async def run_niche(
    nicho_slug: str,
    background_tasks: BackgroundTasks,
    dry_run: bool = False,
    checkpoints: bool = False,
    reference_url: str = "",
    manual_ideas: str = "",
    disable_image_cache: bool = False,
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
        "manual_ideas": manual_ideas,
        "checkpoint_mode": "web" if checkpoints else "auto",
        "disable_image_cache": bool(disable_image_cache),
    }
    background_tasks.add_task(
        _run_pipeline_bg,
        nicho_slug,
        dry_run,
        "",
        reference_url,
        manual_ideas,
        checkpoints,
        disable_image_cache,
    )
    return {
        "status": "started",
        "nicho": nicho_slug,
        "dry_run": dry_run,
        "checkpoints": checkpoints,
        "reference_url": reference_url,
        "manual_ideas": manual_ideas,
        "disable_image_cache": bool(disable_image_cache),
    }


@app.post("/api/run-all")
async def run_all(
    background_tasks: BackgroundTasks,
    checkpoints: bool = False,
    reference_url: str = "",
    manual_ideas: str = "",
    disable_image_cache: bool = False,
):
    """Trigger all 5 nichos sequentially."""
    background_tasks.add_task(_run_all_bg, checkpoints, reference_url, manual_ideas, disable_image_cache)
    return {
        "status": "started",
        "nichos": list(NICHOS.keys()),
        "checkpoints": checkpoints,
        "reference_url": reference_url,
        "manual_ideas": manual_ideas,
        "disable_image_cache": bool(disable_image_cache),
    }


@app.post("/api/resume/{job_id}")
async def resume_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    checkpoints: bool = False,
    manual_ideas: str = "",
    disable_image_cache: bool = False,
):
    """Resume a crashed job."""
    state = StateManager(settings.temp_dir)
    manifest = state.load(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    resume_manual_ideas = manual_ideas
    if not resume_manual_ideas and getattr(manifest, "manual_ideas", None):
        resume_manual_ideas = " | ".join(getattr(manifest, "manual_ideas", []))

    _active_runs[manifest.nicho_slug] = {
        "started": time.time(),
        "job_id": job_id,
        "reference_url": getattr(manifest, "reference_url", ""),
        "manual_ideas": resume_manual_ideas,
        "checkpoint_mode": "web" if checkpoints else "auto",
        "disable_image_cache": bool(disable_image_cache),
    }
    background_tasks.add_task(
        _run_pipeline_bg,
        manifest.nicho_slug,
        False,
        job_id,
        getattr(manifest, "reference_url", ""),
        resume_manual_ideas,
        checkpoints,
        disable_image_cache,
    )
    return {
        "status": "resuming",
        "job_id": job_id,
        "checkpoints": checkpoints,
        "manual_ideas": resume_manual_ideas,
        "disable_image_cache": bool(disable_image_cache),
    }


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

    manifest["stage_checkpoints"] = (
        manifest.get("stage_checkpoints")
        if isinstance(manifest.get("stage_checkpoints"), dict)
        else _load_stage_checkpoints(job_id)
    )

    manifest["associated_reference"] = {
        "url": manifest.get("reference_url", ""),
        "notes": manifest.get("reference_notes", ""),
        "delivery_promise": manifest.get("reference_delivery_promise", ""),
        "hook_seconds": manifest.get("reference_hook_seconds", 0.0),
        "avg_cut_seconds": manifest.get("reference_avg_cut_seconds", 0.0),
        "video_available": manifest.get("reference_video_available", False),
        "analysis": manifest.get("reference_analysis", {}),
    }

    # Add pipeline timeline
    manifest["pipeline_timeline"] = _get_pipeline_timeline(manifest)

    # Add decision audit trail
    manifest["decision_trail"] = _get_decision_trail(manifest)

    return manifest


@app.get("/api/jobs/{job_id}/analysis")
async def job_analysis(job_id: str):
    """Post-render analysis for a job: ffprobe + quality score + thumbnail."""
    manifest = _load_manifest_by_job_id(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}

    video_path = manifest.get("video_path", "")
    probe_data = _run_ffprobe(video_path)
    thumbnail = _generate_thumbnail(video_path)

    return {
        "job_id": job_id,
        "video_path": video_path,
        "video_exists": bool(video_path and Path(video_path).exists()),
        "ffprobe": probe_data,
        "thumbnail": thumbnail,
        "render_backend": manifest.get("render_backend", ""),
        "qa_passed": bool(manifest.get("qa_passed", True)),
        "qa_issues": manifest.get("qa_issues", []),
        "post_render_report": manifest.get("post_render_report", {}),
        "stage_checkpoints": (
            manifest.get("stage_checkpoints")
            if isinstance(manifest.get("stage_checkpoints"), dict)
            else _load_stage_checkpoints(job_id)
        ),
        "pipeline_timeline": _get_pipeline_timeline(manifest),
        "decision_trail": _get_decision_trail(manifest),
        "timings": manifest.get("timings", {}),
        "total_elapsed": sum(manifest.get("timings", {}).values()),
    }


@app.get("/api/jobs/{job_id}/manifest")
async def job_manifest_raw(job_id: str):
    """Return the raw manifest JSON for inspection."""
    manifest = _load_manifest_by_job_id(job_id)
    if not manifest:
        return {"error": f"Job {job_id} not found"}
    return _clean_manifest_for_save(manifest)


@app.get("/api/pipeline-stages")
async def pipeline_stages():
    """Return the pipeline stage definitions for the timeline UI."""
    return PIPELINE_STAGES


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
                    "checkpoint_policy": data.get("checkpoint_policy", ""),
                    "human_approval_required": bool(data.get("human_approval_required", False)),
                    "human_approved": bool(data.get("human_approved", False)),
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
        "publish_title": manifest.get("publish_title", manifest.get("titulo", "")),
        "publish_description": manifest.get("publish_description", manifest.get("caption", "")),
        "publish_hashtags": manifest.get("publish_hashtags", []),
        "publish_hashtags_text": manifest.get("publish_hashtags_text", ""),
        "publish_comment": manifest.get("publish_comment", ""),
        "publish_cover_path": manifest.get("publish_cover_path", manifest.get("thumbnail_path", "")),
        "video_path": manifest.get("video_path", ""),
        "qa_issues": manifest.get("qa_issues", []),
        "timings": manifest.get("timings", {}),
        "checkpoint_policy": manifest.get("checkpoint_policy", ""),
        "human_approval_required": bool(manifest.get("human_approval_required", False)),
        "human_approved": bool(manifest.get("human_approved", False)),
        "stage_checkpoints": (
            manifest.get("stage_checkpoints")
            if isinstance(manifest.get("stage_checkpoints"), dict)
            else _load_stage_checkpoints(job_id)
        ),
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
    manifest["checkpoint_policy"] = manifest.get("checkpoint_policy", "guided")
    manifest["human_approval_required"] = False
    manifest["human_approved"] = True
    manifest["review_resolution"] = "approved"
    manifest["review_resolved_at"] = now_iso
    stage_checkpoints = manifest.get("stage_checkpoints") if isinstance(manifest.get("stage_checkpoints"), dict) else {}
    stage_checkpoints["manual_review"] = {
        "status": "completed",
        "timestamp": now_iso,
        "resolution": "approved",
    }
    manifest["stage_checkpoints"] = stage_checkpoints
    trail = manifest.get("decision_trail") if isinstance(manifest.get("decision_trail"), list) else []
    trail.append({
        "stage": "qa_post",
        "label": "Manual review approved",
        "detail": "Reviewer approved post-render output",
        "severity": "info",
        "timestamp": int(time.time() * 1000),
        "metadata": {"resolution": "approved"},
    })
    manifest["decision_trail"] = trail

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
    manifest["checkpoint_policy"] = manifest.get("checkpoint_policy", "guided")
    manifest["human_approval_required"] = True
    manifest["human_approved"] = False
    manifest["review_resolution"] = "rejected"
    manifest["review_resolved_at"] = datetime.now().isoformat(timespec="seconds")
    stage_checkpoints = manifest.get("stage_checkpoints") if isinstance(manifest.get("stage_checkpoints"), dict) else {}
    stage_checkpoints["manual_review"] = {
        "status": "rejected",
        "timestamp": manifest["review_resolved_at"],
        "resolution": "rejected",
    }
    manifest["stage_checkpoints"] = stage_checkpoints
    trail = manifest.get("decision_trail") if isinstance(manifest.get("decision_trail"), list) else []
    trail.append({
        "stage": "qa_post",
        "label": "Manual review rejected",
        "detail": reason[:220],
        "severity": "warning",
        "timestamp": int(time.time() * 1000),
        "metadata": {"resolution": "rejected"},
    })
    manifest["decision_trail"] = trail

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
    budget_state = _read_json_file(settings.budget_state_path) or {}
    today_key = date.today().isoformat()
    month_key = f"month:{date.today().strftime('%Y-%m')}"
    return {
        "mode": settings.execution_mode_label(),
        "feature_flags": settings.active_feature_flags(),
        "daily_budget_usd": float(settings.daily_budget_usd),
        "monthly_budget_usd": float(settings.monthly_budget_usd),
        "today_spend_usd": float(budget_state.get(today_key, 0.0)),
        "month_spend_usd": float(budget_state.get(month_key, 0.0)),
    }


@app.get("/api/config/operations")
async def operations_config():
    """Return runtime-editable operational config used by dashboard controls."""
    return {
        "use_remotion": bool(settings.use_remotion),
        "force_ffmpeg_renderer": bool(settings.force_ffmpeg_renderer),
        "require_remotion": bool(settings.require_remotion),
        "allow_ffmpeg_fallback": bool(settings.allow_ffmpeg_fallback),
        "prefer_stock_images": bool(settings.prefer_stock_images),
        "enable_image_cache": bool(settings.enable_image_cache),
        "generated_images_count": int(settings.generated_images_count),
        "media_cache_ttl_days": int(settings.media_cache_ttl_days),
        "feature_flags": settings.active_feature_flags(),
    }


@app.post("/api/config/operations")
async def operations_config_update(payload: dict = Body(...)):
    """Update a safe whitelist of operational flags at runtime (and persist to .env)."""
    updates: dict[str, object] = {}
    env_updates: dict[str, str] = {}

    if "use_remotion" in payload:
        value = _as_bool(payload.get("use_remotion"), default=settings.use_remotion)
        settings.use_remotion = value
        updates["use_remotion"] = value
        env_updates["USE_REMOTION"] = "true" if value else "false"

    if "force_ffmpeg_renderer" in payload:
        value = _as_bool(payload.get("force_ffmpeg_renderer"), default=settings.force_ffmpeg_renderer)
        settings.force_ffmpeg_renderer = value
        updates["force_ffmpeg_renderer"] = value
        env_updates["FORCE_FFMPEG_RENDERER"] = "true" if value else "false"

    if "require_remotion" in payload:
        value = _as_bool(payload.get("require_remotion"), default=settings.require_remotion)
        settings.require_remotion = value
        updates["require_remotion"] = value
        env_updates["REQUIRE_REMOTION"] = "true" if value else "false"

    if "allow_ffmpeg_fallback" in payload:
        value = _as_bool(payload.get("allow_ffmpeg_fallback"), default=settings.allow_ffmpeg_fallback)
        settings.allow_ffmpeg_fallback = value
        updates["allow_ffmpeg_fallback"] = value
        env_updates["ALLOW_FFMPEG_FALLBACK"] = "true" if value else "false"

    if "prefer_stock_images" in payload:
        value = _as_bool(payload.get("prefer_stock_images"), default=settings.prefer_stock_images)
        settings.prefer_stock_images = value
        updates["prefer_stock_images"] = value
        env_updates["PREFER_STOCK_IMAGES"] = "true" if value else "false"

    if "enable_image_cache" in payload:
        value = _as_bool(payload.get("enable_image_cache"), default=settings.enable_image_cache)
        settings.enable_image_cache = value
        updates["enable_image_cache"] = value
        env_updates["ENABLE_IMAGE_CACHE"] = "true" if value else "false"

    if "generated_images_count" in payload:
        try:
            value = max(1, min(20, int(payload.get("generated_images_count"))))
            settings.generated_images_count = value
            updates["generated_images_count"] = value
            env_updates["GENERATED_IMAGES_COUNT"] = str(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="generated_images_count must be an integer")

    if "media_cache_ttl_days" in payload:
        try:
            value = max(0, min(30, int(payload.get("media_cache_ttl_days"))))
            settings.media_cache_ttl_days = value
            updates["media_cache_ttl_days"] = value
            env_updates["MEDIA_CACHE_TTL_DAYS"] = str(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="media_cache_ttl_days must be an integer")

    if not updates:
        raise HTTPException(status_code=400, detail="No valid operation keys received")

    persisted_path = ""
    if _as_bool(payload.get("persist", True), default=True):
        persisted_path = _persist_env_updates(env_updates)

    return {
        "status": "updated",
        "updated": updates,
        "persisted": bool(persisted_path),
        "env_path": persisted_path,
        "feature_flags": settings.active_feature_flags(),
    }


@app.get("/api/config/remotion-diagnostics")
async def remotion_diagnostics():
    """Deep diagnostics to explain why Remotion is or is not available."""
    from pipeline.renderer_remotion import REMOTION_DIR, is_remotion_available, get_remotion_unavailability_reason

    package_json = REMOTION_DIR / "package.json"
    node_modules = REMOTION_DIR / "node_modules"
    npx_path = shutil.which("npx") or shutil.which("npx.cmd")
    node_path = shutil.which("node")

    node_version = ""
    if node_path:
        try:
            probe = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=6)
            if probe.returncode == 0:
                node_version = probe.stdout.strip()
        except Exception:
            node_version = ""

    checks = {
        "use_remotion_flag": bool(settings.use_remotion),
        "force_ffmpeg_renderer_flag": bool(settings.force_ffmpeg_renderer),
        "require_remotion_flag": bool(settings.require_remotion),
        "allow_ffmpeg_fallback_flag": bool(settings.allow_ffmpeg_fallback),
        "composer_dir_exists": REMOTION_DIR.exists(),
        "package_json_exists": package_json.exists(),
        "node_modules_exists": node_modules.exists(),
        "npx_available": bool(npx_path),
        "node_available": bool(node_path),
    }
    technical_keys = [
        "composer_dir_exists",
        "package_json_exists",
        "node_modules_exists",
        "npx_available",
        "node_available",
    ]
    missing = [k for k in technical_keys if not checks.get(k)]

    recommendation = "Remotion available"
    if not checks["node_available"]:
        recommendation = "Instala Node.js LTS y npm en el servidor"
    elif not checks["npx_available"]:
        recommendation = "Asegura npm en PATH para exponer npx"
    elif not checks["node_modules_exists"]:
        recommendation = "Ejecuta: cd remotion-composer && npm install"
    elif checks["require_remotion_flag"] and checks["force_ffmpeg_renderer_flag"]:
        recommendation = "Config invalida: REQUIRE_REMOTION=true y FORCE_FFMPEG_RENDERER=true"
    elif checks["force_ffmpeg_renderer_flag"]:
        recommendation = "Desactiva FORCE_FFMPEG_RENDERER para usar Remotion"
    elif not checks["use_remotion_flag"]:
        recommendation = "Activa USE_REMOTION=true para usar Remotion"

    unavailable_reason = get_remotion_unavailability_reason()

    return {
        "checks": checks,
        "missing_requirements": missing,
        "node_version": node_version,
        "node_path": node_path or "",
        "npx_path": npx_path or "",
        "composer_dir": str(REMOTION_DIR),
        "overall_available": bool(is_remotion_available()),
        "unavailable_reason": unavailable_reason,
        "recommendation": recommendation,
    }


@app.get("/api/themes/proposals/{nicho_slug}")
async def get_theme_proposals(nicho_slug: str):
    """Return latest generated theme proposals for one niche."""
    slug = nicho_slug.strip().lower()
    if slug not in NICHOS:
        raise HTTPException(status_code=400, detail=f"Unknown niche: {nicho_slug}")

    store = _read_theme_proposals_store()
    entry = store.get(slug, {}) if isinstance(store.get(slug, {}), dict) else {}
    return {
        "nicho_slug": slug,
        "updated_at": int(entry.get("updated_at", 0) or 0),
        "count": len(entry.get("proposals", []) if isinstance(entry.get("proposals", []), list) else []),
        "model_used": str(entry.get("model_used", "") or ""),
        "source": str(entry.get("source", "") or ""),
        "proposals": entry.get("proposals", []) if isinstance(entry.get("proposals", []), list) else [],
        "avoid_topics": entry.get("avoid_topics", []) if isinstance(entry.get("avoid_topics", []), list) else [],
    }


@app.post("/api/themes/proposals/{nicho_slug}")
async def generate_theme_proposals(nicho_slug: str, payload: dict = Body(default={})):
    """Generate theme proposals with Gemini so user can pick one before running pipeline."""
    slug = nicho_slug.strip().lower()
    if slug not in NICHOS:
        raise HTTPException(status_code=400, detail=f"Unknown niche: {nicho_slug}")

    try:
        count = max(1, min(8, int(payload.get("count", 5) or 5)))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="count must be an integer")

    context_hint = str(payload.get("context", "") or "").strip()
    recent_titles = _recent_titles_for_nicho(slug, limit=12)
    local_memory = get_niche_memory_lines(slug, limit=10)

    seed_topics: list[str] = []
    try:
        from services.trends import get_trending_signals

        signals = get_trending_signals(NICHOS[slug].nombre, settings.rapidapi_key)
        seed_topics = [str(t) for t in signals.get("merged_topics", []) if str(t).strip()]
    except Exception as exc:
        logger.debug(f"Theme proposals: trends fetch failed ({slug}): {exc}")

    model_used = ""
    raw_text = ""
    try:
        from services.llm_router import call_llm_primary_gemini

        system_prompt = (
            "Eres un estratega de contenido viral en espanol. "
            "Genera propuestas de tema NO repetidas y listas para producir video. "
            "Devuelve SOLO JSON valido con formato: "
            "{\"proposals\":[{\"title\":\"...\",\"angle\":\"...\",\"hook\":\"...\",\"viral_score\":8.5}]}."
        )
        user_prompt = (
            f"NICHO: {NICHOS[slug].nombre}\n"
            f"COUNT: {count}\n"
            f"CONTEXTO_ADICIONAL: {context_hint or 'N/A'}\n"
            f"TENDENCIAS: {' | '.join(seed_topics[:10]) or 'N/A'}\n"
            f"TEMAS_RECIENTES_A_EVITAR: {' | '.join(recent_titles[:12]) or 'N/A'}\n"
            f"MEMORIA_LOCAL: {' | '.join(local_memory[:10]) or 'N/A'}\n"
            "Reglas: titulos concretos (<=12 palabras), angulo narrativo claro, gancho potente."
        )

        raw_text, model_used = call_llm_primary_gemini(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.9,
            timeout=45,
            max_retries=1,
            purpose="dashboard_theme_proposals",
        )
    except Exception as exc:
        logger.warning(f"Theme proposals generation failed ({slug}): {exc}")

    parsed = _extract_json_payload(raw_text)
    proposals = _normalize_theme_proposals(parsed, count=count)

    avoid_set = {x.strip().lower() for x in recent_titles if str(x).strip()}
    filtered: list[dict] = []
    for item in proposals:
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        if title.lower() in avoid_set:
            continue
        filtered.append(item)
    proposals = filtered

    source = "gemini"
    if not proposals:
        proposals = _fallback_theme_proposals(slug, count=count, seed_topics=seed_topics or recent_titles)
        source = "fallback"

    store = _read_theme_proposals_store()
    store[slug] = {
        "updated_at": int(time.time()),
        "model_used": model_used,
        "source": source,
        "context": context_hint,
        "avoid_topics": recent_titles[:12],
        "proposals": proposals,
    }
    _write_theme_proposals_store(store)

    return {
        "nicho_slug": slug,
        "count": len(proposals),
        "model_used": model_used,
        "source": source,
        "avoid_topics": recent_titles[:12],
        "proposals": proposals,
    }


@app.post("/api/themes/start/{nicho_slug}")
async def start_with_theme_selection(
    nicho_slug: str,
    background_tasks: BackgroundTasks,
    payload: dict = Body(...),
):
    """Start a pipeline run using one selected proposal as manual ideas."""
    slug = nicho_slug.strip().lower()
    if slug not in NICHOS:
        return {"error": f"Unknown niche: {nicho_slug}"}

    if slug in _active_runs:
        return {"error": f"{slug} is already running", "job_id": _active_runs[slug].get("job_id")}

    proposal_id = str(payload.get("proposal_id", "") or "").strip()
    checkpoints = _as_bool(payload.get("checkpoints", False), default=False)
    dry_run = _as_bool(payload.get("dry_run", False), default=False)
    reference_url = str(payload.get("reference_url", "") or "").strip()
    extra_manual = str(payload.get("manual_ideas", "") or "").strip()
    disable_image_cache = _as_bool(payload.get("disable_image_cache", False), default=False)

    store = _read_theme_proposals_store()
    proposals = store.get(slug, {}).get("proposals", []) if isinstance(store.get(slug, {}), dict) else []
    selected = next((p for p in proposals if str(p.get("id", "")) == proposal_id), None)

    if not selected and payload.get("proposal") and isinstance(payload.get("proposal"), dict):
        selected = payload.get("proposal")

    if not selected:
        return {"error": "proposal_id not found for niche"}

    manual_parts = [
        str(selected.get("title", "") or "").strip(),
        str(selected.get("angle", "") or "").strip(),
        str(selected.get("hook", "") or "").strip(),
    ]
    if extra_manual:
        manual_parts.append(extra_manual)
    manual_ideas = " | ".join([p for p in manual_parts if p])

    if isinstance(store.get(slug, {}), dict):
        store[slug]["selected_proposal_id"] = proposal_id
        store[slug]["selected_at"] = int(time.time())
        _write_theme_proposals_store(store)

    _active_runs[slug] = {
        "started": time.time(),
        "job_id": "starting...",
        "reference_url": reference_url,
        "manual_ideas": manual_ideas,
        "checkpoint_mode": "web" if checkpoints else "auto",
        "theme_proposal_id": proposal_id,
        "disable_image_cache": disable_image_cache,
    }
    background_tasks.add_task(
        _run_pipeline_bg,
        slug,
        dry_run,
        "",
        reference_url,
        manual_ideas,
        checkpoints,
        disable_image_cache,
    )

    return {
        "status": "started",
        "nicho": slug,
        "dry_run": dry_run,
        "checkpoints": checkpoints,
        "reference_url": reference_url,
        "manual_ideas": manual_ideas,
        "selected_proposal": selected,
        "disable_image_cache": disable_image_cache,
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


@app.get("/api/providers/gemini/stats")
async def gemini_provider_stats():
    """Return aggregated Gemini usage stats (key slots + models)."""
    stats_path = settings.gemini_usage_stats_path
    data = _read_json_file(stats_path) or {}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    models = data.get("models") if isinstance(data.get("models"), dict) else {}

    now_ts = int(time.time())
    key_items = []
    cooling_keys = 0
    for key_name in sorted(keys.keys(), key=lambda x: int(x) if str(x).isdigit() else 999):
        item = keys.get(key_name) if isinstance(keys.get(key_name), dict) else {}
        cooldown_until = int(item.get("cooldown_until", 0) or 0)
        is_cooling = cooldown_until > now_ts
        if is_cooling:
            cooling_keys += 1
        key_items.append(
            {
                "key_slot": key_name,
                "attempts": int(item.get("attempts", 0) or 0),
                "success": int(item.get("success", 0) or 0),
                "failure": int(item.get("failure", 0) or 0),
                "last_latency_ms": int(item.get("last_latency_ms", 0) or 0),
                "cooldown_until": cooldown_until,
                "cooling_down": is_cooling,
                "last_error": str(item.get("last_error", "") or "")[:220],
            }
        )

    model_items = []
    cooling_models = 0
    for model_name in sorted(models.keys()):
        item = models.get(model_name) if isinstance(models.get(model_name), dict) else {}
        cooldown_until = int(item.get("cooldown_until", 0) or 0)
        is_cooling = cooldown_until > now_ts
        if is_cooling:
            cooling_models += 1
        model_items.append(
            {
                "model": model_name,
                "attempts": int(item.get("attempts", 0) or 0),
                "success": int(item.get("success", 0) or 0),
                "failure": int(item.get("failure", 0) or 0),
                "cooldown_until": cooldown_until,
                "cooling_down": is_cooling,
                "last_error": str(item.get("last_error", "") or "")[:220],
            }
        )

    return {
        "mode": settings.execution_mode_label(),
        "feature_flags": settings.active_feature_flags(),
        "stats_path": str(stats_path),
        "summary": {
            "attempts": int(summary.get("attempts", 0) or 0),
            "success": int(summary.get("success", 0) or 0),
            "failure": int(summary.get("failure", 0) or 0),
            "last_success_model": str(summary.get("last_success_model", "") or ""),
            "last_error": str(summary.get("last_error", "") or "")[:220],
            "keys_cooling": cooling_keys,
            "models_cooling": cooling_models,
            "updated_at": int(data.get("updated_at", 0) or 0),
        },
        "keys": key_items,
        "models": model_items,
    }


@app.get("/api/costs")
async def costs_summary():
    """Return cost governance summary and recent per-job costs."""
    budget_state = _read_json_file(settings.budget_state_path) or {}
    today_key = date.today().isoformat()
    month_key = f"month:{date.today().strftime('%Y-%m')}"
    today_spend = float(budget_state.get(today_key, 0.0))
    month_spend = float(budget_state.get(month_key, 0.0))
    daily_budget = float(settings.daily_budget_usd)
    monthly_budget = float(settings.monthly_budget_usd)
    remaining = round(max(daily_budget - today_spend, 0.0), 4) if daily_budget > 0 else None
    remaining_monthly = round(max(monthly_budget - month_spend, 0.0), 4) if monthly_budget > 0 else None

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
        "monthly_budget_usd": monthly_budget,
        "today_spend_usd": round(today_spend, 4),
        "month_spend_usd": round(month_spend, 4),
        "remaining_budget_usd": remaining,
        "remaining_monthly_budget_usd": remaining_monthly,
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
    manual_ideas: str = "",
    checkpoints: bool = False,
    disable_image_cache: bool = False,
):
    """Run pipeline in background thread (AUTO by default; WEB only when requested)."""
    try:
        from core.pipeline_v15 import run_pipeline_v15
        mode = DirectorMode.WEB if checkpoints else DirectorMode.AUTO
        runtime_overrides: dict[str, object] = {}
        if disable_image_cache:
            runtime_overrides["disable_image_cache"] = True
        logger.info(f"🚀 Starting V15 {mode.value.upper()} Pipeline for {nicho_slug}")
        result = run_pipeline_v15(
            nicho_slug,
            dry_run=dry_run,
            resume_job_id=resume_id,
            mode=mode,
            reference_url=reference_url,
            manual_ideas=manual_ideas,
            runtime_overrides=runtime_overrides,
        )
        if result:
            _active_runs[nicho_slug] = {
                "job_id": result.job_id,
                "status": result.status,
                "finished": time.time(),
                "checkpoint_mode": mode.value,
                "manual_ideas": list(getattr(result, "manual_ideas", []) or []),
                "disable_image_cache": bool(disable_image_cache),
            }
    except Exception as e:
        logger.error(f"Background run failed for {nicho_slug}: {e}")
    finally:
        _active_runs.pop(nicho_slug, None)


def _run_all_bg(
    checkpoints: bool = False,
    reference_url: str = "",
    manual_ideas: str = "",
    disable_image_cache: bool = False,
):
    """Run all nichos sequentially."""
    for slug in NICHOS:
        _run_pipeline_bg(
            slug,
            checkpoints=checkpoints,
            reference_url=reference_url,
            manual_ideas=manual_ideas,
            disable_image_cache=disable_image_cache,
        )


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

    public_url = (settings.public_app_url or "").strip() or f"http://{args.host}:{args.port}"
    logger.info(f"🎬 Video Factory Dashboard starting on {public_url}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
