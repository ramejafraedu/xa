"""Reactive render supervisor — Remotion/FFmpeg recovery before pipeline-level healing.

Parses stderr, calls ``propose_render_recovery`` (same JSON plans as self-healer
but without consuming manifest healing quota), applies side effects (cache,
materialize, memory, concurrency), and re-invokes a caller-provided render closure.

Optionally records outcomes in ``agents.semantic_memory`` for Phase 1 memory.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from loguru import logger

from config import settings
from models.content import ErrorCode
from pipeline.self_healer import propose_render_recovery


def infer_remotion_error_code(stderr: str) -> ErrorCode:
    """Map Remotion/Node stderr to an ErrorCode for recovery prompts."""
    if not stderr:
        return ErrorCode.UNKNOWN
    low = stderr.lower()
    if "timeout" in low or "timed out" in low or "etimedout" in low:
        return ErrorCode.FFMPEG_TIMEOUT
    if "enoent" in low or "no such file" in low:
        return ErrorCode.ASSET_MISSING
    if "enomem" in low or "out of memory" in low:
        return ErrorCode.UNKNOWN
    return ErrorCode.UNKNOWN


@contextmanager
def _temporary_settings(**overrides: object) -> Iterator[None]:
    """Restore settings attributes after a supervised retry."""
    backup: dict[str, object] = {}
    for key, value in overrides.items():
        if hasattr(settings, key):
            backup[key] = getattr(settings, key)
            setattr(settings, key, value)
    try:
        yield
    finally:
        for key, value in backup.items():
            setattr(settings, key, value)


def _remotion_disk_side_effects(plan: dict) -> None:
    """Clear caches / rebuild bundle (no long-lived settings mutation)."""
    from pipeline.renderer_remotion import _clear_remotion_cache, _force_rebuild_remotion_bundle

    action = str(plan.get("action") or "")

    if action == "remotion_frame_cache_recovery" or plan.get("clear_cache"):
        _clear_remotion_cache()
    if plan.get("rebuild_bundle"):
        _force_rebuild_remotion_bundle()


def _settings_overrides_for_plan(plan: dict) -> dict[str, object]:
    """Map healing JSON to temporary Settings overrides for one retry."""
    action = str(plan.get("action") or "")
    out: dict[str, object] = {}

    if action == "remotion_force_materialize_and_retry" or plan.get("force_materialize"):
        out["remotion_force_materialize"] = True

    mem = plan.get("increase_memory_mb") or plan.get("increase_memory")
    if isinstance(mem, (int, float)) and int(mem) > 0:
        out["remotion_compositor_memory_limit"] = int(mem)

    extra_timeout = plan.get("timeout_seconds")
    if isinstance(extra_timeout, (int, float)) and int(extra_timeout) > 0:
        cur = int(getattr(settings, "remotion_timeout_seconds", 600) or 600)
        out["remotion_timeout_seconds"] = max(cur, int(extra_timeout))

    return out


def run_remotion_supervisor_retries(
    *,
    nicho_slug: str,
    job_id: str,
    last_stderr: str,
    rerender: Callable[[], tuple[bool, str]],
    original_params: str = "{}",
    max_retries: Optional[int] = None,
) -> tuple[bool, str]:
    """After a failed Remotion render, propose recovery plans and retry.

    Args:
        last_stderr: Captured stderr from the last ``npx remotion render`` run.
        rerender: Zero-arg closure that re-runs the same Remotion render path.

    Returns:
        (True, "") on success, or (False, last_error_message) if exhausted.
    """
    if not getattr(settings, "render_supervisor_enabled", True):
        return False, last_stderr or ""

    cap = max_retries
    if cap is None:
        cap = int(getattr(settings, "render_supervisor_max_retries", 2) or 2)
    cap = max(0, min(5, cap))

    err = last_stderr or ""
    for attempt in range(cap):
        code = infer_remotion_error_code(err)
        raw = propose_render_recovery(err, original_params, code)
        if not raw:
            logger.warning("Render supervisor: no recovery proposal from self-healer")
            break
        try:
            plan = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            logger.warning("Render supervisor: invalid JSON from recovery proposal")
            break
        if not isinstance(plan, dict):
            break

        logger.info(
            f"🤖 Render supervisor attempt {attempt + 1}/{cap}: "
            f"action={plan.get('action', plan.keys())}"
        )

        # Concurrency: force 1 on cache recovery (pairs with _clear_remotion_cache)
        merged: dict[str, object] = _settings_overrides_for_plan(plan)
        if plan.get("clear_cache") or plan.get("action") == "remotion_frame_cache_recovery":
            merged["remotion_concurrency"] = 1

        try:
            with _temporary_settings(**merged):
                _remotion_disk_side_effects(plan)
                try:
                    os.sync()
                except Exception:
                    pass
                ok, msg = rerender()
        except Exception as exc:
            logger.error(f"Render supervisor rerender failed: {exc}")
            ok, msg = False, str(exc)

        _record_memory(nicho_slug, job_id, attempt, plan, ok, err)

        if ok:
            logger.info(f"✅ Render supervisor succeeded on attempt {attempt + 1}")
            return True, ""

        err = msg or err

    return False, err


def _record_memory(
    nicho_slug: str,
    job_id: str,
    attempt: int,
    plan: dict,
    success: bool,
    stderr_snippet: str,
) -> None:
    try:
        from agents.semantic_memory import (
            MEMORY_KIND_RENDER_OUTCOME,
            get_semantic_memory_store,
        )

        store = get_semantic_memory_store()
        store.initialize()
        body = (
            f"supervised_remotion retry attempt={attempt + 1} success={success}\n"
            f"plan_keys={list(plan.keys())}\n"
            f"stderr[:800]={(stderr_snippet or '')[:800]}"
        )
        store.add_memory(
            MEMORY_KIND_RENDER_OUTCOME,
            body,
            title="render_supervisor",
            nicho_slug=nicho_slug,
            job_id=job_id,
            metadata={"success": success, "attempt": attempt + 1, "plan": plan},
        )
    except Exception as exc:
        logger.debug(f"Semantic memory record skipped: {exc}")
