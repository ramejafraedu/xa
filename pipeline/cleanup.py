"""Cleanup — only temp/, outputs stay intact.

Replaces n8n node: 🧹 Limpiar Temp.
Only cleans workspace/temp/ files. Never touches workspace/output/.
Output retention is configurable (default: keep forever).
"""
from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

from config import settings


def cleanup_temp(timestamp: int) -> None:
    """Remove temporary files for a specific timestamp."""
    temp_dir = settings.temp_dir
    if not temp_dir.exists():
        return

    patterns = [
        f"*_{timestamp}.*",
        f"*_{timestamp}",
        f"sfx_{timestamp}",
    ]

    removed = 0
    for pattern in patterns:
        for f in temp_dir.glob(pattern):
            try:
                if f.is_dir():
                    import shutil
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink()
                removed += 1
            except Exception as e:
                logger.debug(f"Cleanup error: {e}")

    logger.info(f"Cleaned {removed} temp files for TS={timestamp}")


def cleanup_old_outputs() -> None:
    """Remove old output videos based on retention policy.

    Only runs if OUTPUT_RETENTION_DAYS > 0.
    Default is 0 (keep forever).
    """
    days = settings.output_retention_days
    if days <= 0:
        return  # Keep forever

    cutoff = time.time() - (days * 86400)
    output_dir = settings.output_dir
    if not output_dir.exists():
        return

    removed = 0
    for f in output_dir.glob("*.mp4"):
        if f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except Exception as e:
                logger.debug(f"Output cleanup error: {e}")

    if removed:
        logger.info(f"Removed {removed} output videos older than {days} days")


def cleanup_stale_temp() -> None:
    """Remove temp files older than 24 hours (safety net)."""
    temp_dir = settings.temp_dir
    if not temp_dir.exists():
        return

    cutoff = time.time() - 86400  # 24 hours
    removed = 0

    for f in temp_dir.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass

    # Clean empty dirs
    for d in sorted(temp_dir.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()  # Only works if empty
            except OSError:
                pass

    if removed:
        logger.info(f"Cleaned {removed} stale temp files (>24h)")
