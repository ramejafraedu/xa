"""Editable local memory per niche.

Stores reusable editorial notes per niche and supports CRUD + move operations.
This memory is local (workspace file system) and is injected into prompts.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from config import NICHOS, settings


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _validate_nicho_slug(nicho_slug: str) -> str:
    slug = (nicho_slug or "").strip().lower()
    if slug not in NICHOS:
        raise ValueError(f"Unknown niche: {nicho_slug}")
    return slug


def _memory_dir() -> Path:
    path = settings.workspace / "niche_memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _memory_file(nicho_slug: str) -> Path:
    slug = _validate_nicho_slug(nicho_slug)
    return _memory_dir() / f"{slug}.json"


def _read_entries(nicho_slug: str) -> list[dict]:
    path = _memory_file(nicho_slug)
    if not path.exists():
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"Failed reading local niche memory ({nicho_slug}): {exc}")
        return []

    if isinstance(raw, dict):
        raw_entries = raw.get("entries", [])
    elif isinstance(raw, list):
        raw_entries = raw
    else:
        raw_entries = []

    entries: list[dict] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        entries.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                "text": text,
                "source": str(item.get("source", "manual")),
                "created_at": str(item.get("created_at") or _now_iso()),
                "updated_at": str(item.get("updated_at") or _now_iso()),
            }
        )
    return entries


def _write_entries(nicho_slug: str, entries: list[dict]) -> None:
    path = _memory_file(nicho_slug)
    payload = {
        "nicho_slug": _validate_nicho_slug(nicho_slug),
        "updated_at": _now_iso(),
        "entries": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_manual_ideas(raw: str | list[str] | None, limit: int = 8) -> list[str]:
    """Normalize manual ideas from text or list into a compact deduped list."""
    if raw is None:
        return []

    parts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            parts.append(str(item))
    else:
        text = str(raw).replace("|", "\n")
        parts.extend(text.splitlines())

    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = re.sub(r"^[\s\-\*•\d\.)]+", "", str(part)).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
        if len(out) >= max(1, int(limit)):
            break
    return out


def list_niche_memory(nicho_slug: Optional[str] = None) -> dict[str, list[dict]]:
    """Return memory entries for one niche or all niches."""
    if nicho_slug:
        slug = _validate_nicho_slug(nicho_slug)
        return {slug: _read_entries(slug)}

    result: dict[str, list[dict]] = {}
    for slug in NICHOS.keys():
        result[slug] = _read_entries(slug)
    return result


def get_niche_memory_entries(nicho_slug: str, limit: int = 10) -> list[dict]:
    entries = _read_entries(nicho_slug)
    return entries[: max(1, int(limit))]


def get_niche_memory_lines(nicho_slug: str, limit: int = 8) -> list[str]:
    return [e.get("text", "") for e in get_niche_memory_entries(nicho_slug, limit=limit) if e.get("text")]


def build_niche_memory_context(nicho_slug: str, limit: int = 8) -> str:
    lines = get_niche_memory_lines(nicho_slug, limit=limit)
    if not lines:
        return "Sin memoria local por nicho"
    return " | ".join(lines)


def add_niche_memory_entry(nicho_slug: str, text: str, source: str = "manual") -> dict:
    """Add one memory note to a niche."""
    slug = _validate_nicho_slug(nicho_slug)
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("Memory text is required")

    entries = _read_entries(slug)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "text": clean_text,
        "source": str(source or "manual"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    entries.insert(0, entry)
    _write_entries(slug, entries)
    return entry


def update_niche_memory_entry(nicho_slug: str, entry_id: str, text: str) -> Optional[dict]:
    """Update text of an existing memory entry."""
    slug = _validate_nicho_slug(nicho_slug)
    target_id = str(entry_id or "").strip()
    clean_text = str(text or "").strip()
    if not target_id or not clean_text:
        raise ValueError("entry_id and text are required")

    entries = _read_entries(slug)
    updated: Optional[dict] = None
    for item in entries:
        if str(item.get("id", "")) != target_id:
            continue
        item["text"] = clean_text
        item["updated_at"] = _now_iso()
        updated = item
        break

    if updated is None:
        return None

    _write_entries(slug, entries)
    return updated


def delete_niche_memory_entry(nicho_slug: str, entry_id: str) -> bool:
    """Delete a memory entry by id."""
    slug = _validate_nicho_slug(nicho_slug)
    target_id = str(entry_id or "").strip()
    if not target_id:
        raise ValueError("entry_id is required")

    entries = _read_entries(slug)
    kept = [item for item in entries if str(item.get("id", "")) != target_id]
    if len(kept) == len(entries):
        return False

    _write_entries(slug, kept)
    return True


def move_niche_memory_entry(source_slug: str, target_slug: str, entry_id: str) -> Optional[dict]:
    """Move one entry from one niche to another niche."""
    source = _validate_nicho_slug(source_slug)
    target = _validate_nicho_slug(target_slug)
    if source == target:
        raise ValueError("source and target niches must be different")

    target_id = str(entry_id or "").strip()
    if not target_id:
        raise ValueError("entry_id is required")

    source_entries = _read_entries(source)
    moved: Optional[dict] = None
    remaining: list[dict] = []

    for item in source_entries:
        if str(item.get("id", "")) == target_id and moved is None:
            moved = {
                "id": str(item.get("id", "") or uuid.uuid4().hex[:12]),
                "text": str(item.get("text", "")).strip(),
                "source": f"moved_from:{source}",
                "created_at": str(item.get("created_at") or _now_iso()),
                "updated_at": _now_iso(),
            }
        else:
            remaining.append(item)

    if not moved:
        return None

    target_entries = _read_entries(target)
    target_entries.insert(0, moved)

    _write_entries(source, remaining)
    _write_entries(target, target_entries)
    return moved
