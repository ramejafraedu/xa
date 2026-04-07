"""Trends service — RSS first (stable), pytrends as bonus.

Google Trends via pytrends gets blocked constantly. RSS feeds from
Google News are more reliable. TikTok trending via RapidAPI is optional.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import request_with_retry, get_json


_tiktok_cooldown_until = 0.0


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
        from urllib.parse import quote_plus

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


def get_trending_context(nicho: str, rapidapi_key: str = "") -> str:
    """Build trending context string. RSS first, then pytrends, then TikTok."""
    parts = []

    # 1. RSS (most reliable)
    rss_trends = get_google_trends_rss(nicho)
    if rss_trends:
        parts.append("TRENDING GOOGLE MX: " + ", ".join(rss_trends))

    # 2. pytrends bonus (may fail)
    if not rss_trends:
        py_trends = get_google_trends_pytrends(nicho)
        if py_trends:
            parts.append("TRENDING GOOGLE MX: " + ", ".join(py_trends))

    # 3. TikTok
    tiktok = get_tiktok_trending(rapidapi_key)
    if tiktok:
        parts.append("HASHTAGS VIRALES TIKTOK: #" + " #".join(tiktok))

    return " | ".join(parts) if parts else f"Tendencias no disponibles — usa contexto del nicho: {nicho}"
