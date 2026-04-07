"""Video Stock — Pexels multi-key rotation + Pixabay + Coverr fallback.

Replaces n8n nodes: 🎬 Pexels Multi-Key + 🎬 Pixabay+Coverr Fallback.
Rotates through up to 4 Pexels API keys to avoid rate limits.
"""
from __future__ import annotations

import urllib.parse
import json
import hashlib
from typing import Optional, Union
from pathlib import Path

from loguru import logger

from config import settings
from services.http_client import get_json, request_with_retry


def fetch_stock_videos(
    keywords: list[str],
    num_needed: int = 8,
    provider_order: Optional[list[str]] = None,
) -> list[dict]:
    """Fetch stock video URLs and manage local cache via index.json.

    Returns list of dicts: {"url": "http...", "cache_path": "C:/..."}
    If 'url' is empty, it means the file is already fully cached.
    """
    settings.video_cache_dir.mkdir(parents=True, exist_ok=True)
    index_file = settings.video_cache_dir / "index.json"
    
    # Load index
    try:
        index_data = json.loads(index_file.read_text("utf-8")) if index_file.exists() else {}
    except Exception:
        index_data = {}

    all_items: list[dict] = []
    seen_paths = set()
    pexels_keys = settings.pexels_keys

    # 1. First, check cache for all requested keywords
    for kw in keywords:
        kw_clean = kw.strip().lower()
        if kw_clean in index_data:
            for filename in index_data[kw_clean]:
                cached_path = settings.video_cache_dir / filename
                if cached_path.exists() and cached_path.stat().st_size > 1000:
                    str_path = cached_path.as_posix()
                    if str_path not in seen_paths:
                        seen_paths.add(str_path)
                                all_items.append(
                                    {
                                        "url": "",
                                        "local_path": str_path,
                                        "provider": _infer_provider_from_filename(filename),
                                    }
                                )

    # 2. Fetch more following selected provider order.
    provider_order = provider_order or ["pexels", "pixabay", "coverr"]

    for provider in provider_order:
        if len(all_items) >= num_needed * 2:
            break

        keyword_limit = len(keywords)
        if provider == "pixabay":
            keyword_limit = min(len(keywords), 4)
        elif provider == "coverr":
            keyword_limit = min(len(keywords), 3)

        for kw in keywords[:keyword_limit]:
            if len(all_items) >= num_needed * 2:
                break

            if provider == "pexels":
                urls = _fetch_pexels(kw, pexels_keys)
            elif provider == "pixabay":
                urls = _fetch_pixabay_video(kw)
            elif provider == "coverr":
                urls = _fetch_coverr(kw)
            else:
                urls = []

            for u in urls:
                dest = (settings.video_cache_dir / u["filename"]).as_posix()
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

                # Update index optimistically (renderer will download it there)
                kw_clean = kw.strip().lower()
                if kw_clean not in index_data:
                    index_data[kw_clean] = []
                if u["filename"] not in index_data[kw_clean]:
                    index_data[kw_clean].append(u["filename"])

    # Save index
    try:
        index_file.write_text(json.dumps(index_data, indent=2), "utf-8")
    except Exception as e:
        logger.warning(f"Could not save video cache index: {e}")

    logger.info(f"Stock videos found: {len(all_items)} (needed: {num_needed})")
    # Return at most num_needed * 2
    return all_items[:num_needed * 2]


def _fetch_pexels(keyword: str, keys: list[str]) -> list[str]:
    """Try each Pexels key until we get videos."""
    if not keyword or not keyword.strip():
        return []

    q = urllib.parse.quote(keyword.strip())
    url = f"https://api.pexels.com/videos/search?query={q}&orientation=portrait&size=large&per_page=8"

    for i, key in enumerate(keys):
        try:
            response = request_with_retry(
                "GET", url,
                headers={"Authorization": key},
                max_retries=1,
                timeout=15,
            )

            if response.status_code == 429:
                logger.debug(f"Pexels key{i+1} rate-limited for '{keyword}'")
                continue

            if response.status_code >= 400:
                continue

            data = response.json()
            videos = data.get("videos", [])
            urls = []

            for v in videos[:3]:
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

            if urls:
                logger.debug(f"Pexels key{i+1} '{keyword}' → {len(urls)} videos")
                return urls

        except Exception as e:
            logger.debug(f"Pexels key{i+1} error: {e}")

    return []


def _fetch_pixabay_video(keyword: str) -> list[str]:
    """Fetch vertical videos from Pixabay."""
    if not settings.pixabay_api_key:
        return []

    try:
        q = urllib.parse.quote(keyword)
        url = (
            f"https://pixabay.com/api/videos/"
            f"?key={settings.pixabay_api_key}"
            f"&q={q}&orientation=vertical&per_page=5&min_width=720"
        )
        data = get_json(url, max_retries=1)
        hits = data.get("hits", [])
        urls = []
        for h in hits:
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


def _fetch_coverr(keyword: str) -> list[str]:
    """Fetch videos from Coverr."""
    try:
        q = urllib.parse.quote(keyword)
        url = f"https://coverr.co/api/videos/search?query={q}&page=1"
        response = request_with_retry(
            "GET", url,
            headers={"Accept": "application/json"},
            max_retries=1,
            timeout=10,
        )
        if response.status_code >= 400:
            return []

        data = response.json()
        items = data.get("hits", data.get("videos", []))
        urls = []
        for item in items[:3]:
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
