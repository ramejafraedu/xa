"""Music — Pixabay Music + Jamendo fallback.

Replaces n8n nodes: 🎵 Pixabay Music Fallback + 🎵 Jamendo Música + 🔗 Merge Música.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import download_file, get_json


def fetch_music(genre: str, output_path: Path) -> tuple[bool, str]:
    """Download background music. Returns (success, source)."""
    return fetch_music_by_order(genre, output_path)


def fetch_music_by_order(
    genre: str,
    output_path: Path,
    provider_order: Optional[list[str]] = None,
) -> tuple[bool, str]:
    """Download music using a preferred provider order."""
    if output_path.exists() and output_path.stat().st_size > 1000:
        logger.info("Music already cached, skipping")
        return True, "cached"

    provider_order = provider_order or ["pixabay", "jamendo"]

    for provider in provider_order:
        if provider == "pixabay":
            url = _get_pixabay_music(genre)
            if url and download_file(url, output_path, timeout=30):
                logger.info(f"Music downloaded (Pixabay): {genre}")
                return True, "pixabay"

        elif provider == "jamendo":
            url = _get_jamendo_music(genre)
            if url and download_file(url, output_path, timeout=30):
                logger.info(f"Music downloaded (Jamendo): {genre}")
                return True, "jamendo"

    logger.warning(f"No music found for genre: {genre}")
    return False, "none"


def _get_pixabay_music(genre: str) -> Optional[str]:
    """Get music URL from Pixabay."""
    if not settings.pixabay_api_key:
        return None

    try:
        url = (
            f"https://pixabay.com/api/music/"
            f"?key={settings.pixabay_api_key}"
            f"&genre={genre}&per_page=3"
        )
        data = get_json(url, max_retries=1)
        hits = data.get("hits", [])
        if hits and hits[0].get("audio"):
            return hits[0]["audio"]
    except Exception as e:
        logger.debug(f"Pixabay music error: {e}")

    return None


def _get_jamendo_music(genre: str) -> Optional[str]:
    """Get music URL from Jamendo."""
    try:
        url = "https://api.jamendo.com/v3.0/tracks/"
        params = {
            "client_id": settings.jamendo_client_id,
            "format": "json",
            "limit": "1",
            "tags": genre,
            "audioformat": "mp32",
        }
        data = get_json(url, params=params, max_retries=1)
        results = data.get("results", [])
        if results and results[0].get("audio"):
            return results[0]["audio"]
    except Exception as e:
        logger.debug(f"Jamendo error: {e}")

    return None
