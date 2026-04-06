"""Video Stock — Pexels multi-key rotation + Pixabay + Coverr fallback.

Replaces n8n nodes: 🎬 Pexels Multi-Key + 🎬 Pixabay+Coverr Fallback.
Rotates through up to 4 Pexels API keys to avoid rate limits.
"""
from __future__ import annotations

import urllib.parse
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import get_json, request_with_retry


def fetch_stock_videos(
    keywords: list[str],
    num_needed: int = 8,
) -> list[str]:
    """Fetch stock video URLs from multiple sources.

    Tries Pexels with key rotation, then Pixabay, then Coverr.
    Returns list of video download URLs.
    """
    all_urls: list[str] = []
    pexels_keys = settings.pexels_keys

    # --- Pexels Multi-Key ---
    for kw in keywords:
        if len(all_urls) >= num_needed * 2:
            break  # Got enough
        urls = _fetch_pexels(kw, pexels_keys)
        all_urls.extend(urls)

    # --- Pixabay Fallback ---
    if len(all_urls) < num_needed:
        for kw in keywords[:4]:
            if len(all_urls) >= num_needed:
                break
            urls = _fetch_pixabay_video(kw)
            all_urls.extend(urls)

    # --- Coverr Fallback ---
    if len(all_urls) < num_needed:
        for kw in keywords[:3]:
            if len(all_urls) >= num_needed:
                break
            urls = _fetch_coverr(kw)
            all_urls.extend(urls)

    # Deduplicate
    seen = set()
    unique = []
    for url in all_urls:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    logger.info(f"Stock videos found: {len(unique)} (needed: {num_needed})")
    return unique


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
                    urls.append(best["link"])

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
                    urls.append(video_url)
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
                    urls.append(mp4)
                    continue
            mp4 = item.get("mp4") or item.get("url")
            if mp4:
                urls.append(mp4)
        return urls

    except Exception as e:
        logger.debug(f"Coverr error: {e}")
        return []
