"""Reference URL analysis and caching utilities for V15/V16."""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from pathlib import Path

import httpx
from loguru import logger

from services.http_client import request_with_retry


def load_reference_context(url: str, cache_path: Path) -> dict:
    """Load analyzed reference context from cache or fetch it from web."""
    clean_url = (url or "").strip()
    if not clean_url:
        return {}

    cache = _read_cache(cache_path)
    cache_key = hashlib.sha1(clean_url.encode("utf-8")).hexdigest()
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return cached

    context = _fetch_reference_context(clean_url)
    if not context:
        return {}

    cache[cache_key] = context
    _write_cache(cache_path, cache)
    return context


def _fetch_reference_context(url: str) -> dict:
    """Fetch URL and extract lightweight structured context."""
    try:
        response = request_with_retry("GET", url, max_retries=2, timeout=20)
        if response.status_code >= 400:
            logger.warning(f"Reference fetch failed ({response.status_code}): {url[:120]}")
            return {}

        return _context_from_html(url, response.text or "")

    except Exception as exc:
        # Some Windows environments fail TLS chain validation with external URLs.
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            logger.warning("Reference fetch SSL verify failed, retrying with verify=False")
            try:
                with httpx.Client(timeout=20.0, follow_redirects=True, verify=False) as client:
                    resp = client.get(url)
                if resp.status_code >= 400:
                    logger.warning(f"Reference insecure fetch failed ({resp.status_code}): {url[:120]}")
                    return {}
                return _context_from_html(url, resp.text or "")
            except Exception as inner_exc:
                logger.warning(f"Reference insecure fetch error: {inner_exc}")
                return {}

        logger.warning(f"Reference fetch error: {exc}")
        return {}


def _context_from_html(url: str, raw_html: str) -> dict:
    """Build structured context from an HTML document."""
    title = _extract_title(raw_html)
    body = _extract_clean_text(raw_html)
    summary = _build_summary(body)
    key_points = _extract_key_points(body)

    if not summary and not key_points:
        logger.warning(f"Reference fetch produced empty context: {url[:120]}")
        return {}

    return {
        "url": url,
        "title": title,
        "summary": summary,
        "key_points": key_points,
        "fetched_at": int(time.time()),
    }


def _extract_title(raw_html: str) -> str:
    """Extract <title> as a compact string."""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    title = html.unescape(m.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    return title[:180]


def _extract_clean_text(raw_html: str) -> str:
    """Convert HTML into normalized plain text."""
    txt = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"<style[^>]*>.*?</style>", " ", txt, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"<noscript[^>]*>.*?</noscript>", " ", txt, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:12000]


def _build_summary(clean_text: str) -> str:
    """Build a compact summary from first meaningful sentences."""
    if not clean_text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", clean_text)
    picked: list[str] = []
    for s in sentences:
        s = s.strip()
        if len(s) < 40:
            continue
        picked.append(s)
        if len(" ".join(picked)) >= 480:
            break

    if not picked:
        return clean_text[:480]
    return " ".join(picked)[:700]


def _extract_key_points(clean_text: str, limit: int = 5) -> list[str]:
    """Extract key points as medium-length unique sentences."""
    points: list[str] = []
    seen: set[str] = set()

    for s in re.split(r"(?<=[.!?])\s+", clean_text):
        s = s.strip()
        if len(s) < 55 or len(s) > 220:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        points.append(s)
        if len(points) >= limit:
            break

    return points


def _read_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.debug(f"Reference cache read failed: {exc}")
    return {}


def _write_cache(path: Path, cache: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug(f"Reference cache write failed: {exc}")
