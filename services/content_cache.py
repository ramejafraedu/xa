"""Content-addressed cache for heavy deterministic calls (TTS, LLM, ...).

The idea is simple: many expensive calls in the pipeline are deterministic
for a given input (same text → same TTS audio, same prompt → same model
answer). We persist those outputs under `settings.temp_dir / "content_cache"`
keyed by a sha1 of the inputs. A TTL prevents stale reuse and a size budget
keeps disk usage bounded.

MODULE CONTRACT:
  get_cached(namespace, payload, suffix) → Optional[Path]  (path still valid, mtime refreshed)
  put_cached(namespace, payload, src_path, suffix) → Path
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import settings


_DEFAULT_TTL_HOURS = 24 * 30  # 30 days — audio/images rarely change
_DEFAULT_MAX_MB = 2048  # 2 GB soft budget across all namespaces


def _cache_root() -> Path:
    root = Path(settings.temp_dir) / "content_cache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _namespace_dir(namespace: str) -> Path:
    safe = "".join(c if c.isalnum() or c in {"_", "-"} else "_" for c in namespace)[:40] or "misc"
    path = _cache_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stable_key(payload: Any) -> str:
    """Return a sha1 over a JSON-serialisable payload (dicts, strings, etc.)."""
    if isinstance(payload, (dict, list, tuple)):
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    else:
        blob = str(payload)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _candidate_path(namespace: str, key: str, suffix: str) -> Path:
    clean_suffix = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ""
    return _namespace_dir(namespace) / f"{key}{clean_suffix}"


def get_cached(
    namespace: str,
    payload: Any,
    suffix: str = ".bin",
    *,
    ttl_hours: float = _DEFAULT_TTL_HOURS,
    min_size_bytes: int = 256,
) -> Optional[Path]:
    """Return the cached file if still fresh, else None.

    Touches mtime on hit so the cleanup policy can use LRU eviction later.
    """
    key = _stable_key(payload)
    path = _candidate_path(namespace, key, suffix)
    if not path.exists():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size < min_size_bytes:
        logger.debug(f"content_cache miss (too small): {path}")
        return None
    age_hours = (time.time() - stat.st_mtime) / 3600.0
    if age_hours > ttl_hours:
        logger.debug(f"content_cache miss (stale {age_hours:.1f}h): {path}")
        return None
    try:
        path.touch()
    except OSError:
        pass
    logger.info(f"content_cache HIT [{namespace}] → {path.name}")
    return path


def put_cached(
    namespace: str,
    payload: Any,
    src_path: Path,
    suffix: str = ".bin",
) -> Optional[Path]:
    """Copy `src_path` into the cache keyed by payload. Returns the stored path."""
    if not src_path.exists():
        return None
    key = _stable_key(payload)
    dest = _candidate_path(namespace, key, suffix)
    try:
        shutil.copyfile(src_path, dest)
        logger.info(f"content_cache STORE [{namespace}] → {dest.name}")
        _enforce_budget()
        return dest
    except OSError as exc:
        logger.warning(f"content_cache store failed for {src_path.name}: {exc}")
        return None


def _enforce_budget(max_mb: int = _DEFAULT_MAX_MB) -> None:
    """Evict oldest files when total cache size goes above `max_mb`."""
    root = _cache_root()
    files: list[tuple[float, int, Path]] = []
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size

    limit = max_mb * 1024 * 1024
    if total <= limit:
        return

    files.sort(key=lambda item: item[0])  # oldest first
    to_free = total - limit
    freed = 0
    for _, size, path in files:
        try:
            path.unlink()
            freed += size
            if freed >= to_free:
                break
        except OSError:
            continue

    if freed > 0:
        logger.info(
            f"content_cache eviction: freed {freed / (1024 * 1024):.1f}MB "
            f"(limit {max_mb}MB)"
        )
