"""Trends service — multi-source web research with graceful degradation.

Primary sources are public and stable enough for low-cost research:
    - Google Trends RSS
    - Google News RSS
    - YouTube search RSS
    - Reddit public search JSON

TikTok RapidAPI remains optional as a best-effort extra source.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote_plus

from loguru import logger

from config import settings
from services.http_client import request_with_retry, get_json


_tiktok_cooldown_until = 0.0
_youtube_cooldown_until = 0.0
_reddit_cooldown_until = 0.0
_trending_cache: dict[str, tuple[float, dict]] = {}
_TRENDING_CACHE_TTL_SECONDS = 45 * 60
_TRENDING_CACHE_FILE = settings.temp_dir / "trending_signals_cache.json"
_TRENDING_CACHE_MAX_ITEMS = 80


def _merge_unique(items: list[str], limit: int = 20) -> list[str]:
    """Deduplicate preserving order and removing trivial entries."""
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = (item or "").strip()
        if not clean or len(clean) < 3:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def _cache_key(nicho: str, rapidapi_key: str) -> str:
    """Build stable cache key that doesn't store full secrets."""
    key_hint = "with_key" if rapidapi_key else "no_key"
    return f"{(nicho or '').strip().lower()}::{key_hint}::{settings.enable_tiktok_trending_api}"


def _cache_get(key: str) -> dict | None:
    """Return cached value when TTL is valid."""
    now = time.time()

    item = _trending_cache.get(key)
    if not item:
        item = _cache_get_persistent(key)
        if not item:
            return None
        _trending_cache[key] = item

    ts, payload = item
    if now - ts > _TRENDING_CACHE_TTL_SECONDS:
        _trending_cache.pop(key, None)
        _cache_delete_persistent(key)
        return None
    return payload


def _cache_set(key: str, payload: dict) -> None:
    """Store cached signals payload."""
    now = time.time()
    _trending_cache[key] = (now, payload)
    _cache_set_persistent(key, now, payload)


def _cache_get_persistent(key: str) -> tuple[float, dict] | None:
    """Read cache entry from disk to survive process restarts."""
    data = _read_persistent_cache_file()
    entry = data.get(key)
    if not isinstance(entry, dict):
        return None

    ts = entry.get("ts")
    payload = entry.get("payload")
    if not isinstance(ts, (int, float)) or not isinstance(payload, dict):
        return None
    return float(ts), payload


def _cache_set_persistent(key: str, ts: float, payload: dict) -> None:
    """Persist cache entry to disk with bounded size and TTL pruning."""
    data = _read_persistent_cache_file()
    data[key] = {"ts": ts, "payload": payload}

    now = time.time()
    valid_items: list[tuple[str, dict]] = []
    for cache_key, entry in data.items():
        if not isinstance(entry, dict):
            continue
        entry_ts = entry.get("ts")
        if not isinstance(entry_ts, (int, float)):
            continue
        if now - float(entry_ts) > _TRENDING_CACHE_TTL_SECONDS:
            continue
        valid_items.append((cache_key, entry))

    valid_items.sort(key=lambda kv: float(kv[1].get("ts", 0.0)), reverse=True)
    trimmed = dict(valid_items[:_TRENDING_CACHE_MAX_ITEMS])
    _write_persistent_cache_file(trimmed)


def _cache_delete_persistent(key: str) -> None:
    """Delete a stale cache key from disk cache."""
    data = _read_persistent_cache_file()
    if key in data:
        data.pop(key, None)
        _write_persistent_cache_file(data)


def _read_persistent_cache_file() -> dict:
    """Read persistent cache JSON safely."""
    path = _TRENDING_CACHE_FILE
    try:
        if not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_persistent_cache_file(data: dict) -> None:
    """Write persistent cache JSON safely."""
    path = _TRENDING_CACHE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        # Cache persistence is best-effort; never break the pipeline.
        pass


def get_google_trends_rss(nicho: str, limit: int = 6) -> list[str]:
    """Fetch trending topics via Google Trends RSS (reliable, no auth)."""
    try:
        # Google Trends RSS for Mexico
        url = "https://trends.google.com/trending/rss?geo=MX"
        resp = request_with_retry("GET", url, max_retries=2, timeout=10)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        ns = {"ht": "https://trends.google.com/trends/trendingsearches/daily"}
        items = root.findall(".//item/title")
        trends = [item.text.strip() for item in items[:limit] if item.text]
        if trends:
            logger.debug(f"Google Trends RSS: {trends}")
        return trends

    except Exception as e:
        logger.debug(f"Google Trends RSS failed: {e}")
        return []


def get_google_news_rss(query: str, limit: int = 6) -> list[str]:
    """Fetch topic headlines from Google News RSS for extra web context."""
    if not query:
        return []

    try:
        encoded = quote_plus(query)
        url = (
            "https://news.google.com/rss/search"
            f"?q={encoded}&hl=es-419&gl=MX&ceid=MX:es-419"
        )
        resp = request_with_retry("GET", url, max_retries=2, timeout=12)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        items = root.findall(".//item/title")
        headlines = [item.text.strip() for item in items[:limit] if item.text]
        return headlines
    except Exception as e:
        logger.debug(f"Google News RSS failed: {e}")
        return []


def get_youtube_trending(query: str, limit: int = 6) -> list[str]:
    """Fetch YouTube trending-ish topics using public search RSS."""
    global _youtube_cooldown_until

    if not query:
        return []
    if time.time() < _youtube_cooldown_until:
        return []

    try:
        encoded = quote_plus(query)
        url = f"https://www.youtube.com/feeds/videos.xml?search_query={encoded}"
        resp = request_with_retry("GET", url, max_retries=1, timeout=12)
        if resp.status_code != 200:
            if resp.status_code in (429, 403):
                _youtube_cooldown_until = time.time() + (20 * 60)
            return []

        root = ET.fromstring(resp.text)
        atom_ns = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(f".//{atom_ns}entry/{atom_ns}title")
        titles = [e.text.strip() for e in entries[:limit] if e.text]
        return titles
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "rate" in err:
            _youtube_cooldown_until = time.time() + (20 * 60)
        logger.debug(f"YouTube RSS trending failed: {e}")
        return []


def get_reddit_trending(query: str, limit: int = 6) -> list[str]:
    """Fetch Reddit hot discussions via public search endpoint."""
    global _reddit_cooldown_until

    if not query:
        return []
    if time.time() < _reddit_cooldown_until:
        return []

    try:
        url = "https://www.reddit.com/search.json"
        headers = {
            "User-Agent": "VideoFactory/1.0 (research.trends)",
            "Accept": "application/json",
        }
        params = {
            "q": query,
            "sort": "hot",
            "limit": str(max(3, limit)),
            "t": "day",
        }
        resp = request_with_retry("GET", url, headers=headers, params=params, max_retries=1, timeout=12)
        if resp.status_code != 200:
            if resp.status_code in (429, 403):
                _reddit_cooldown_until = time.time() + (20 * 60)
            return []

        data = resp.json() if resp.content else {}
        children = data.get("data", {}).get("children", [])
        topics = []
        for child in children:
            title = child.get("data", {}).get("title", "")
            if title and len(title) >= 8:
                topics.append(title.strip())
            if len(topics) >= limit:
                break
        return topics
    except Exception as e:
        err = str(e).lower()
        if "429" in err or "rate" in err:
            _reddit_cooldown_until = time.time() + (20 * 60)
        logger.debug(f"Reddit trending failed: {e}")
        return []


def get_google_trends_pytrends(nicho: str) -> list[str]:
    """Fallback: pytrends library (may be blocked by Google)."""
    try:
        from pytrends.request import TrendReq

        pt = TrendReq(hl="es-MX", tz=360)
        kw = nicho.split()[:2]
        pt.build_payload(kw, timeframe="now 7-d", geo="MX")
        df = pt.related_queries()
        tops = []
        for k in kw:
            try:
                t = df.get(k, {}).get("top")
                if t is not None:
                    tops += list(t["query"].head(3))
            except Exception:
                pass
        return tops[:6]

    except ImportError:
        logger.debug("pytrends not installed, skipping")
        return []
    except Exception as e:
        logger.debug(f"pytrends failed: {e}")
        return []


def get_tiktok_trending(rapidapi_key: str) -> list[str]:
    """Get TikTok trending hashtags via RapidAPI."""
    global _tiktok_cooldown_until

    if not settings.enable_tiktok_trending_api:
        return []

    if not rapidapi_key:
        return []

    if time.time() < _tiktok_cooldown_until:
        return []

    try:
        url = "https://tiktok-trending.p.rapidapi.com/feed/list"
        headers = {
            "x-rapidapi-host": "tiktok-trending.p.rapidapi.com",
            "x-rapidapi-key": rapidapi_key,
        }
        params = {"region": "MX", "count": "10"}
        data = get_json(url, headers=headers, params=params, max_retries=2)

        items = data.get("data", {}).get("item_list", []) or data.get("itemList", [])
        hashtags = []
        for item in items[:5]:
            challenges = item.get("challenges", item.get("textExtra", []))
            for c in challenges:
                tag = c.get("hashtagName") or c.get("text")
                if tag:
                    hashtags.append(tag)
        return hashtags

    except Exception as e:
        err = str(e).lower()
        if "404" in err or "not found" in err:
            _tiktok_cooldown_until = time.time() + (6 * 3600)
            logger.warning("TikTok trending endpoint returned 404. Cooling down for 6h.")
        elif "429" in err or "rate" in err:
            _tiktok_cooldown_until = time.time() + (20 * 60)
            logger.warning("TikTok trending rate limited. Cooling down for 20m.")
        logger.debug(f"TikTok trending failed: {e}")
        return []


def get_trending_signals(nicho: str, rapidapi_key: str = "") -> dict:
    """Collect structured trending signals from all enabled sources."""
    cache_key = _cache_key(nicho, rapidapi_key)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    google_trends = get_google_trends_rss(nicho)
    if not google_trends:
        google_trends = get_google_trends_pytrends(nicho)

    youtube_hot = get_youtube_trending(f"{nicho} shorts", limit=4)
    reddit_hot = get_reddit_trending(nicho, limit=4)
    news_headlines = get_google_news_rss(nicho, limit=4)
    tiktok_hashtags = get_tiktok_trending(rapidapi_key) if settings.enable_tiktok_trending_api else []

    # Merge a compact set of generic trending topics for downstream agents.
    merged_topics = _merge_unique(
        google_trends + youtube_hot + reddit_hot + news_headlines,
        limit=12,
    )

    signals = {
        "google_trends": google_trends,
        "youtube_hot": youtube_hot,
        "reddit_hot": reddit_hot,
        "news_headlines": news_headlines,
        "tiktok_hashtags": tiktok_hashtags,
        "merged_topics": merged_topics,
        "cache_ttl_seconds": _TRENDING_CACHE_TTL_SECONDS,
    }
    _cache_set(cache_key, signals)
    return signals


def get_trending_context(nicho: str, rapidapi_key: str = "") -> str:
    """Build trending context string using multi-source web signals."""
    signals = get_trending_signals(nicho, rapidapi_key)
    parts = []

    google = signals.get("google_trends", [])
    if google:
        parts.append("TRENDING GOOGLE MX: " + ", ".join(google))

    youtube = signals.get("youtube_hot", [])
    if youtube:
        parts.append("YOUTUBE HOT: " + " | ".join(youtube))

    reddit = signals.get("reddit_hot", [])
    if reddit:
        parts.append("REDDIT HOT: " + " | ".join(reddit))

    news = signals.get("news_headlines", [])
    if news:
        parts.append("NEWS MX: " + " | ".join(news))

    tiktok = signals.get("tiktok_hashtags", [])
    if tiktok:
        parts.append("HASHTAGS VIRALES TIKTOK: #" + " #".join(tiktok))

    return " | ".join(parts) if parts else f"Tendencias no disponibles — usa contexto del nicho: {nicho}"
