"""Video Stock — Pexels multi-key rotation + Pixabay + Coverr fallback.

Replaces n8n nodes: 🎬 Pexels Multi-Key + 🎬 Pixabay+Coverr Fallback.
Rotates through up to 4 Pexels API keys to avoid rate limits.
"""
from __future__ import annotations

import urllib.parse
import json
import hashlib
import time
import random
from typing import Optional, Union
from pathlib import Path

from loguru import logger

from config import settings
from services.http_client import get_json, request_with_retry


_GREENSCREEN_HINTS = {
    "green screen",
    "greenscreen",
    "green-screen",
    "chroma",
    "chroma key",
    "chromakey",
    "isolated on green",
    "alpha matte",
}

# Celebration/holiday tags to exclude when searching for scientific/academic content
_CELEBRATION_TAGS = {
    "father's day", "fathers day", "dad", "papa", "papá", "dia del padre",
    "mother's day", "mothers day", "mom", "mama", "mamá", "dia de la madre",
    "birthday", "cumpleaños", "celebration", "celebracion", "holiday", "fiesta",
    "christmas", "navidad", "anniversary", "aniversario", "fireworks",
    "sparkler", "bengala", "confetti", "party", "festival", "vacation",
    "beach", "travel", "sunny", "fun", "happy", "smile", "smiling",
}

# Cartoon/animation tags to exclude for serious/scientific content
_CARTOON_ANIMATION_TAGS = {
    "cartoon", "animation", "animated", "3d render", "cartoon character",
    "superhero", "super hero", "funny", "comic", "cute", "kawaii",
    "character", "mascot", "doodle", "sketch", "sticker", "emoji",
    "caricatura", "animacion", "dibujo", "gracioso", "divertido",
    "3d", "render", "cgi", "vfx", "unreal engine", "blender",
}
_pexels_rotation_counter: list[int] = [0]

# Global rotation counter for cache hits - ensures variety in returned videos
_cache_rotation_counter: list[int] = [0]


def _rotated_cache_items(items: list[dict], num_needed: int) -> list[dict]:
    """Rotate cache items to provide variety across different video requests.

    Uses a global counter to shift the starting position, ensuring that
    repeated searches for the same keywords return different videos when possible.
    """
    if not items:
        return items

    # Increment global counter for variety
    _cache_rotation_counter[0] += 1

    # If we have more items than needed, rotate starting position
    if len(items) > num_needed:
        # Use rotation to shift starting point
        rotation = _cache_rotation_counter[0] % max(1, len(items) - num_needed + 1)
        rotated = items[rotation:] + items[:rotation]
        return rotated[:num_needed]

    return items


def fetch_stock_videos(
    keywords: list[str],
    num_needed: int = 8,
    provider_order: Optional[list[str]] = None,
    require_realistic: bool = False,
    temp_dir: Optional[Path] = None,
) -> list[dict]:
    """Fetch stock video URLs and manage local cache via index.json.

    Args:
        keywords: Search terms for stock videos
        num_needed: Target number of videos to fetch
        provider_order: Priority order for stock providers
        require_realistic: If True, filter out cartoon/animated clips

    Returns list of dicts: {"url": "http...", "cache_path": "C:/..."}
    If 'url' is empty, it means the file is already fully cached.
    """
    settings.ensure_dirs()
    index_file = settings.video_cache_dir / "index.json"

    # V16.1: Skip cache if disabled for fresh content
    cache_disabled = settings.disable_stock_cache or settings.force_fresh_assets
    # When cache disabled, put clips in temp_dir (per-job) instead of shared video_cache
    clip_dest_dir = (temp_dir or settings.temp_dir) if cache_disabled else settings.video_cache_dir
    clip_dest_dir.mkdir(parents=True, exist_ok=True)

    if cache_disabled:
        logger.info("🔄 Stock cache disabled - fetching fresh videos from APIs")
        index_data = {}
        all_items = []
    else:
        index_data = _load_cache_index(index_file)
        all_items = []
    
    ttl_seconds = max(0, int(settings.media_cache_ttl_days)) * 86400
    now_ts = int(time.time())

    seen_paths = set()
    pexels_keys = settings.pexels_keys
    target_pool = max(num_needed, min(num_needed + 4, num_needed * 2))
    provider_counts: dict[str, int] = {}

    # 1. First, check cache for all requested keywords (skipped if cache disabled)
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if kw_clean in index_data:
            fresh_entries: list[dict] = []
            for entry in index_data[kw_clean]:
                filename = str(entry.get("filename", "")).strip()
                if not filename:
                    continue
                cached_path = settings.video_cache_dir / filename
                if not cached_path.exists() or cached_path.stat().st_size <= 1000:
                    continue

                entry_ts = int(entry.get("cached_at") or cached_path.stat().st_mtime)
                if ttl_seconds and (now_ts - entry_ts) > ttl_seconds:
                    try:
                        cached_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    continue

                provider = str(entry.get("provider") or _infer_provider_from_filename(filename))
                fresh_entries.append({
                    "filename": filename,
                    "cached_at": entry_ts,
                    "provider": provider,
                })

                str_path = cached_path.as_posix()
                if str_path not in seen_paths:
                    seen_paths.add(str_path)
                    all_items.append(
                        {
                            "url": "",
                            "local_path": str_path,
                            "provider": provider,
                        }
                    )
                    provider_counts[provider] = provider_counts.get(provider, 0) + 1

            if fresh_entries:
                index_data[kw_clean] = fresh_entries
            else:
                index_data.pop(kw_clean, None)

    # If cache already covers requested count, skip provider API calls.
    if len(all_items) >= num_needed:
        if not (settings.disable_stock_cache or settings.force_fresh_assets):
            _save_cache_index(index_file, index_data)
        # Rotate items to provide variety across repeated searches
        rotated_items = _rotated_cache_items(all_items, target_pool)
        logger.info(f"Stock videos found: {len(rotated_items)} (needed: {num_needed}) [cache-only, rotated]")
        return rotated_items

    # 2. Fetch more following selected provider order.
    provider_order = provider_order or ["pexels", "pixabay", "coverr"]

    # Hybrid sourcing: keep Pexels as primary and guarantee some Pixabay presence
    # when both providers are enabled, to increase coverage diversity.
    provider_min_targets: dict[str, int] = {}
    if "pexels" in provider_order and "pixabay" in provider_order and num_needed >= 4:
        provider_min_targets["pexels"] = max(1, int(round(num_needed * 0.65)))
        provider_min_targets["pixabay"] = max(1, int(round(num_needed * 0.20)))
        if "coverr" in provider_order and num_needed >= 6:
            provider_min_targets["coverr"] = 1

    def _needs_more(provider: str) -> bool:
        total_missing = len(all_items) < target_pool
        provider_missing = provider_counts.get(provider, 0) < provider_min_targets.get(provider, 0)
        return total_missing or provider_missing

    for provider in provider_order:
        if not _needs_more(provider):
            continue

        keyword_limit = len(keywords)
        if provider == "pixabay":
            keyword_limit = min(len(keywords), 4)
        elif provider == "coverr":
            keyword_limit = min(len(keywords), 3)

        for kw in keywords[:keyword_limit]:
            if not _needs_more(provider):
                break

            if provider == "pexels":
                urls = _fetch_pexels(kw, pexels_keys, require_realistic=require_realistic)
            elif provider == "pixabay":
                urls = _fetch_pixabay_video(kw, require_realistic=require_realistic)
            elif provider == "coverr":
                urls = _fetch_coverr(kw, require_realistic=require_realistic)
            else:
                urls = []

            for u in urls:
                dest = (clip_dest_dir / u["filename"]).as_posix()
                if dest in seen_paths:
                    continue

                seen_paths.add(dest)
                all_items.append(
                    {
                        "url": u["url"],
                        "local_path": dest,
                        "provider": provider,
                    }
                )
                provider_counts[provider] = provider_counts.get(provider, 0) + 1

                # Update index only when caching
                if not cache_disabled:
                    kw_clean = kw.strip().lower()
                    if kw_clean not in index_data:
                        index_data[kw_clean] = []
                    _upsert_cache_entry(index_data[kw_clean], u["filename"], now_ts, provider)

    # Save index
    if not (settings.disable_stock_cache or settings.force_fresh_assets):
        _save_cache_index(index_file, index_data)

    if provider_counts:
        composition = ", ".join(f"{k}:{v}" for k, v in sorted(provider_counts.items()))
        logger.info(f"Stock provider mix -> {composition}")

    logger.info(f"Stock videos found: {len(all_items)} (needed: {num_needed})")
    # Return at most requested pool size
    return all_items[:target_pool]


def _rotated_pexels_keys(keys: list[str]) -> list[str]:
    if not keys:
        return []
    start = _pexels_rotation_counter[0] % len(keys)
    _pexels_rotation_counter[0] += 1
    return keys[start:] + keys[:start]


def _load_cache_index(index_file: Path) -> dict[str, list[dict]]:
    """Load/normalize index supporting legacy and structured formats."""
    if not index_file.exists():
        return {}
    try:
        raw = json.loads(index_file.read_text("utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, list[dict]] = {}
    for kw, entries in raw.items():
        kw_clean = str(kw).strip().lower()
        if not kw_clean or not isinstance(entries, list):
            continue

        out: list[dict] = []
        for item in entries:
            if isinstance(item, str):
                filename = item.strip()
                if filename:
                    out.append({
                        "filename": filename,
                        "cached_at": 0,
                        "provider": _infer_provider_from_filename(filename),
                    })
                continue

            if isinstance(item, dict):
                filename = str(item.get("filename", "")).strip()
                if not filename:
                    continue
                out.append({
                    "filename": filename,
                    "cached_at": int(item.get("cached_at") or 0),
                    "provider": str(item.get("provider") or _infer_provider_from_filename(filename)),
                })

        if out:
            normalized[kw_clean] = out

    return normalized


def _save_cache_index(index_file: Path, index_data: dict[str, list[dict]]) -> None:
    try:
        index_file.write_text(json.dumps(index_data, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        logger.warning(f"Could not save video cache index: {e}")


def _upsert_cache_entry(entries: list[dict], filename: str, cached_at: int, provider: str) -> None:
    for item in entries:
        if str(item.get("filename", "")) == filename:
            item["cached_at"] = cached_at
            item["provider"] = provider
            return
    entries.append({
        "filename": filename,
        "cached_at": cached_at,
        "provider": provider,
    })


def _fetch_pexels(keyword: str, keys: list[str], require_realistic: bool = False, page: int = 1) -> list[str]:
    """Try each Pexels key until we get videos.

    Args:
        page: Page number for pagination (1-based). Uses rotation for variety.
    """
    if not keyword or not keyword.strip():
        return []


    # Rotate page number for variety (pages 1-5)
    effective_page = random.randint(1, 5)

    q = urllib.parse.quote(keyword.strip())
    url = f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&size=large&per_page=12&page={effective_page}"

    for i, key in enumerate(_rotated_pexels_keys(keys)):
        try:
            response = request_with_retry(
                "GET", url,
                headers={"Authorization": key},
                max_retries=2,
                timeout=15,
            )

            if response.status_code == 429:
                logger.debug(f"Pexels key{i+1} rate-limited for '{keyword}'")
                continue

            if response.status_code >= 400:
                continue

            data = response.json()
            videos = data.get("videos", [])
            random.shuffle(videos)
            urls = []

            for v in videos:
                # Pexels doesn't always provide tags in the search response, 
                # but we can check the URL slug and some metadata
                video_url_slug = str(v.get("url", "")).lower()
                
                # Filter out obvious non-realistic content from slug if required
                if require_realistic and _has_cartoon_tags(video_url_slug):
                    logger.debug(f"Skipping Pexels video (cartoon slug): {video_url_slug}")
                    continue
                
                if _has_celebration_tags(video_url_slug):
                    logger.debug(f"Skipping Pexels celebration video: {video_url_slug}")
                    continue

                files = v.get("video_files", [])
                best = (
                    next((f for f in files if f.get("quality") == "hd" and f.get("height", 0) > f.get("width", 0)), None)
                    or next((f for f in files if f.get("quality") == "hd"), None)
                    or next((f for f in files if f.get("quality") == "sd"), None)
                    or (files[0] if files else None)
                )
                if best and best.get("link"):
                    vid_id = v.get("id")
                    if not vid_id:
                        vid_id = hashlib.md5(best["link"].encode()).hexdigest()[:8]
                    urls.append({"url": best["link"], "filename": f"pexels_{vid_id}.mp4"})
                
                if len(urls) >= 3:
                    break

            if urls:
                logger.debug(f"Pexels key{i+1} '{keyword}' → {len(urls)} videos")
                return urls

        except Exception as e:
            logger.debug(f"Pexels key{i+1} error: {e}")

    return []


def _fetch_pixabay_video(keyword: str, require_realistic: bool = False, page: int = 1) -> list[str]:
    """Fetch vertical videos from Pixabay."""
    if not settings.pixabay_api_key:
        return []

    try:
        # Rotate page number for variety (pages 1-3)
        effective_page = ((page - 1 + _cache_rotation_counter[0]) % 3) + 1

        q = urllib.parse.quote(keyword)
        url = (
            f"https://pixabay.com/api/videos/"
            f"?key={settings.pixabay_api_key}"
            f"&q={q}&orientation=vertical&per_page=5&min_width=720&page={effective_page}"
        )
        data = get_json(url, max_retries=2)
        hits = data.get("hits", [])
        random.shuffle(hits)
        urls = []
        for h in hits:
            tags = str(h.get("tags", "") or "")
            page_url = str(h.get("pageURL", "") or "")
            if _looks_like_greenscreen_meta(tags, page_url):
                continue
            # Skip celebration/holiday videos to avoid context mismatches
            if _has_celebration_tags(tags, page_url):
                logger.debug(f"Skipping celebration-tagged video: {page_url}")
                continue
            # Skip cartoon/animated videos when realistic content is required
            if require_realistic and _has_cartoon_tags(tags, page_url):
                logger.debug(f"Skipping cartoon-tagged video (realistic mode): {page_url}")
                continue

            vids = h.get("videos", {})
            for quality in ["medium", "large", "small"]:
                video_url = vids.get(quality, {}).get("url")
                if video_url:
                    vid_id = h.get("id")
                    if not vid_id:
                        vid_id = hashlib.md5(video_url.encode()).hexdigest()[:8]
                    urls.append({"url": video_url, "filename": f"pixabay_{vid_id}.mp4"})
                    break
        return urls

    except Exception as e:
        logger.debug(f"Pixabay video error: {e}")
        return []


def _looks_like_greenscreen_meta(*fields: str) -> bool:
    blob = " ".join(str(x or "") for x in fields).lower()
    return any(hint in blob for hint in _GREENSCREEN_HINTS)


def _has_celebration_tags(*fields: str) -> bool:
    """Check if video tags/metadata contain celebration/holiday keywords."""
    blob = " ".join(str(x or "") for x in fields).lower()
    return any(tag in blob for tag in _CELEBRATION_TAGS)


def _has_cartoon_tags(*fields: str) -> bool:
    """Check if video tags/metadata contain cartoon/animation keywords."""
    blob = " ".join(str(x or "") for x in fields).lower()
    return any(tag in blob for tag in _CARTOON_ANIMATION_TAGS)


def _fetch_coverr(keyword: str, require_realistic: bool = False) -> list[str]:
    """Fetch videos from Coverr."""
    try:
        q = urllib.parse.quote(keyword)
        url = f"https://coverr.co/api/videos/search?query={q}&page=1"
        response = request_with_retry(
            "GET", url,
            headers={"Accept": "application/json"},
            max_retries=2,
            timeout=10,
        )
        if response.status_code >= 400:
            return []

        data = response.json()
        items = data.get("hits", data.get("videos", []))
        random.shuffle(items)
        urls = []
        for item in items[:3]:
            meta_blob = " ".join(
                str(item.get(key, "") or "")
                for key in ("title", "name", "description", "tags", "slug")
            )
            if _has_celebration_tags(meta_blob):
                continue
            if require_realistic and _has_cartoon_tags(meta_blob):
                continue
            src = item.get("sources", [{}])
            if isinstance(src, list) and src:
                mp4 = src[0].get("src") or src[0].get("url")
                if mp4:
                    vid_id = item.get("id", item.get("objectID", hashlib.md5(mp4.encode()).hexdigest()[:8]))
                    urls.append({"url": mp4, "filename": f"coverr_{vid_id}.mp4"})
                    continue
            mp4 = item.get("mp4") or item.get("url")
            if mp4:
                vid_id = item.get("id", item.get("objectID", hashlib.md5(mp4.encode()).hexdigest()[:8]))
                urls.append({"url": mp4, "filename": f"coverr_{vid_id}.mp4"})
        return urls

    except Exception as e:
        logger.debug(f"Coverr error: {e}")
        return []


def _infer_provider_from_filename(filename: str) -> str:
    """Infer cached provider from filename prefix."""
    lower = (filename or "").lower()
    if lower.startswith("pexels_"):
        return "pexels"
    if lower.startswith("pixabay_"):
        return "pixabay"
    if lower.startswith("coverr_"):
        return "coverr"
    return "cache"
