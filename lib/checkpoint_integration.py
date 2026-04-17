"""Checkpoint integration shim — V16.1 PRO.

Activates ``lib/checkpoint.py`` as a secondary, additive writer alongside
``state_manager.StateManager.write_stage_checkpoint``. This gives us both:

- The legacy V15 state_manager manifest + per-stage JSON (untouched).
- The canonical V16 ``lib/checkpoint.py`` file at
  ``<checkpoints_root>/<job_id>/checkpoint_<canonical_stage>.json`` when the
  payload satisfies the strict schema; otherwise a ``.lite.json`` fallback
  with the same structure minus schema validation.

Stage name mapping translates V15/pipeline names to the canonical set used by
``lib/checkpoint.py``:

    "content_gen" / "script_agent"   -> "script"
    "image_gen" / "video_stock" /
    "asset_agent" / "composition_master" -> "assets"
    "renderer_remotion" / "render"   -> "compose"
    "research"                        -> "research"
    "publish"                         -> "publish"

Silent no-op on any error. Never raises.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import json

try:
    from loguru import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger(__name__)

try:
    from lib import checkpoint as _cp
except Exception:  # pragma: no cover
    _cp = None


_STAGE_MAP = {
    "research": "research",
    "content_gen": "script",
    "script_agent": "script",
    "script": "script",
    "scene_plan": "scene_plan",
    "asset_agent": "assets",
    "image_gen": "assets",
    "video_stock": "assets",
    "assets": "assets",
    "composition_master": "edit",
    "edit": "edit",
    "renderer_remotion": "compose",
    "render": "compose",
    "compose": "compose",
    "publish": "publish",
}


def map_stage(stage: str) -> str:
    """Map a V15/pipeline stage name to canonical V16 checkpoint stage."""
    s = (stage or "").strip().lower()
    return _STAGE_MAP.get(s, s)


def record_stage(
    pipeline_dir: Path,
    job_id: str,
    stage: str,
    artifacts: Optional[dict[str, Any]] = None,
    status: str = "completed",
    pipeline_type: Optional[str] = "v15_short",
    style_playbook: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[Path]:
    """Write a canonical checkpoint file (schema-validated when possible).

    Returns the path written or None on failure. Never raises.
    """
    if not job_id:
        return None
    canonical_stage = map_stage(stage)
    artifacts = dict(artifacts or {})
    if status == "completed":
        # Ensure canonical artifact key exists as a soft marker; strict schema
        # may still reject. We fall back to the ``.lite.json`` writer below.
        canonical_name = None
        if _cp is not None:
            canonical_name = _cp.CANONICAL_STAGE_ARTIFACTS.get(canonical_stage)
        if canonical_name and canonical_name not in artifacts:
            artifacts[canonical_name] = {
                "_lite": True,
                "stage": canonical_stage,
                "job_id": job_id,
                "note": "minimal placeholder written by checkpoint_integration",
            }

    # Try strict write first.
    if _cp is not None:
        try:
            path = _cp.write_checkpoint(
                pipeline_dir=Path(pipeline_dir),
                project_id=job_id,
                stage=canonical_stage,
                status=status,
                artifacts=artifacts,
                pipeline_type=pipeline_type,
                style_playbook=style_playbook,
                metadata=metadata or {},
            )
            logger.debug(f"[checkpoint] wrote canonical {canonical_stage} for {job_id}")
            return path
        except Exception as exc:
            logger.debug(f"[checkpoint] strict write failed for {canonical_stage}: {exc}")

    # Fallback: write a ``.lite.json`` with the same structure but no schema check.
    try:
        lite_dir = Path(pipeline_dir) / job_id
        lite_dir.mkdir(parents=True, exist_ok=True)
        lite_path = lite_dir / f"checkpoint_{canonical_stage}.lite.json"
        payload = {
            "version": "1.0",
            "project_id": job_id,
            "pipeline_type": pipeline_type or "v15_short",
            "stage": canonical_stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "artifacts": artifacts,
            "metadata": metadata or {},
        }
        if style_playbook:
            payload["style_playbook"] = style_playbook
        lite_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug(f"[checkpoint] wrote lite {canonical_stage} for {job_id}")
        return lite_path
    except Exception as exc:
        logger.debug(f"[checkpoint] lite write failed for {stage}: {exc}")
        return None
