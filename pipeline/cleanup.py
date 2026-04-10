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
    cleanup_video_cache()
    
    # Aggressively kill zombies
    try:
        import os
        import platform
        if platform.system().lower() == "windows":
            os.system("taskkill /F /IM node.exe >nul 2>&1")
            os.system("taskkill /F /IM chrome.exe >nul 2>&1")
        else:
            os.system("pkill -9 -f node >/dev/null 2>&1")
            os.system("pkill -9 -f chrome >/dev/null 2>&1")
    except Exception:
        pass


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


def cleanup_video_cache() -> None:
    """Enforce video cache size limit by deleting oldest files first."""
    cache_dir = settings.video_cache_dir
    if not cache_dir.exists():
        return

    max_bytes = settings.max_cache_size_gb * 1024 * 1024 * 1024
    files = [f for f in cache_dir.glob("*.mp4") if f.is_file()]
    if not files:
        return
        
    total_size = sum(f.stat().st_size for f in files)
    if total_size <= max_bytes:
        return

    # Sort by modification time (oldest first)
    files.sort(key=lambda x: x.stat().st_mtime)
    
    removed = 0
    freed = 0
    for f in files:
        if total_size <= max_bytes:
            break
        try:
            size = f.stat().st_size
            f.unlink()
            total_size -= size
            freed += size
            removed += 1
            # Note: We rely on `fetch_stock_videos` checking `cached_path.exists()` 
            # so we do not need to strictly parse and rewrite index.json here.
        except Exception:
            pass
            
    if removed:
        logger.info(f"Video Cache GC: removed {removed} files, freed {freed / (1024*1024):.1f} MB")
