"""SFX — Freesound sound effects for transitions.

Replaces n8n node: 🔊 Freesound SFX.
Downloads whoosh, impact, and transition sounds.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import get_json, download_file


SFX_QUERIES = ["whoosh", "impact", "transition"]


def fetch_sfx(timestamp: int, temp_dir: Path) -> list[Path]:
    """Download SFX files from Freesound.

    Returns list of downloaded SFX file paths.
    """
    if not settings.freesound_api_key:
        logger.debug("Freesound API key not set, skipping SFX")
        return []

    sfx_dir = temp_dir / f"sfx_{timestamp}"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for i, query in enumerate(SFX_QUERIES):
        try:
            url = (
                f"https://freesound.org/apiv2/search/text/"
                f"?query={query}"
                f"&filter=duration:[0+TO+2]"
                f"&fields=id,previews"
                f"&token={settings.freesound_api_key}"
                f"&page_size=1"
            )
            data = get_json(url, max_retries=1)
            sound_results = data.get("results", [])
            if not sound_results:
                continue

            preview_url = sound_results[0].get("previews", {}).get("preview-lq-mp3")
            if not preview_url:
                continue

            output = sfx_dir / f"sfx_{i}.mp3"
            if download_file(preview_url, output, timeout=10):
                results.append(output)
                logger.debug(f"SFX '{query}' downloaded")

        except Exception as e:
            logger.debug(f"SFX '{query}' failed: {e}")

    logger.info(f"SFX downloaded: {len(results)}/{len(SFX_QUERIES)}")
    return results
