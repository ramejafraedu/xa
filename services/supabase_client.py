"""Supabase client — memory + performance analytics.

Uses direct REST API via httpx (no supabase SDK required).

MODULE CONTRACT:
  read_memory(nicho) → str: last 10 video titles for de-duplication
  save_result(manifest) → bool: save complete result for analytics
  save_performance(manifest) → bool: save detailed performance data (hook winners, AB, scores)
  get_niche_analytics(nicho) → dict: average scores, best hooks, optimal duration by niche
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from services.http_client import get_json, post_json


def read_memory(supabase_url: str, anon_key: str, nicho_slug: str, limit: int = 10) -> str:
    """Read recent video history for a niche. Returns formatted string for prompt context."""
    if not supabase_url or not anon_key:
        return "Sin memoria previa"

    try:
        url = f"{supabase_url}/rest/v1/videos"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
        }
        params = {
            "nicho_slug": f"eq.{nicho_slug}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        rows = get_json(url, headers=headers, params=params, max_retries=2)

        if not isinstance(rows, list) or not rows:
            return "Sin memoria previa"

        entries = []
        for r in rows:
            title = r.get("titulo", "")
            hook = r.get("gancho", "")
            if title or hook:
                entries.append(f"{title} - {hook}".strip(" -"))

        return " | ".join(entries) if entries else "Sin memoria previa"

    except Exception as e:
        logger.warning(f"Supabase read failed: {e}")
        return "Sin memoria previa"


def save_result(
    supabase_url: str,
    anon_key: str,
    nicho_slug: str,
    titulo: str,
    gancho: str,
    viral_score: float,
    keywords: list[str],
    timestamp: int,
    ab_variant: str = "A",
    quality_score: float = 0,
) -> bool:
    """Save video result to Supabase for future memory."""
    if not supabase_url or not anon_key:
        return False

    try:
        url = f"{supabase_url}/rest/v1/videos"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        kw_str = ",".join(
            [f"ab_{ab_variant}", f"q_{str(quality_score).replace('.', '_')}"] + keywords
        )
        data = {
            "nicho_slug": nicho_slug,
            "titulo": titulo,
            "gancho": gancho,
            "viral_score": viral_score,
            "keywords": kw_str,
            "timestamp": str(timestamp),
        }
        post_json(url, data, headers=headers, max_retries=2)
        logger.info(f"Saved to Supabase: {nicho_slug}/{titulo[:40]}")
        return True

    except Exception as e:
        logger.warning(f"Supabase save failed: {e}")
        return False


def save_performance(
    supabase_url: str,
    anon_key: str,
    nicho_slug: str,
    *,
    titulo: str = "",
    gancho: str = "",
    hook_score: float = 0,
    desarrollo_score: float = 0,
    cierre_score: float = 0,
    quality_score: float = 0,
    viral_score: float = 0,
    duration_seconds: float = 0,
    ab_variant: str = "A",
    cta: str = "",
    tts_engine: str = "",
    velocidad: str = "",
    healing_count: int = 0,
    timestamp: int = 0,
) -> bool:
    """Save detailed performance metrics for continuous improvement.

    This data feeds back into content generation to learn:
    - Which hooks score highest per niche
    - Which AB variant performs better
    - Optimal duration per platform
    - Which CTA style works
    - Average quality trend over time
    """
    if not supabase_url or not anon_key:
        return False

    try:
        url = f"{supabase_url}/rest/v1/video_performance"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        data = {
            "nicho_slug": nicho_slug,
            "titulo": titulo[:120],
            "gancho": gancho[:200],
            "hook_score": hook_score,
            "desarrollo_score": desarrollo_score,
            "cierre_score": cierre_score,
            "quality_score": quality_score,
            "viral_score": viral_score,
            "duration_seconds": round(duration_seconds, 1),
            "ab_variant": ab_variant,
            "cta": cta[:200],
            "tts_engine": tts_engine,
            "velocidad": velocidad,
            "healing_count": healing_count,
            "timestamp": str(timestamp),
        }
        post_json(url, data, headers=headers, max_retries=1)
        logger.debug(f"Performance saved: {nicho_slug} q={quality_score}")
        return True

    except Exception as e:
        logger.debug(f"Performance save failed (non-critical): {e}")
        return False


def get_niche_analytics(supabase_url: str, anon_key: str, nicho_slug: str) -> dict:
    """Get aggregate analytics for a niche — feeds into content generation.

    Returns:
        {
            "avg_quality": float,
            "avg_hook": float,
            "avg_duration": float,
            "best_hooks": list[str],
            "best_variant": str,
            "best_cta_style": str,
            "total_videos": int,
        }
    """
    defaults = {
        "avg_quality": 0,
        "avg_hook": 0,
        "avg_duration": 0,
        "best_hooks": [],
        "best_variant": "A",
        "best_cta_style": "",
        "total_videos": 0,
    }

    if not supabase_url or not anon_key:
        return defaults

    try:
        url = f"{supabase_url}/rest/v1/video_performance"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
        }
        params = {
            "nicho_slug": f"eq.{nicho_slug}",
            "order": "quality_score.desc",
            "limit": "50",
        }
        rows = get_json(url, headers=headers, params=params, max_retries=1)

        if not isinstance(rows, list) or not rows:
            return defaults

        n = len(rows)
        avg_q = sum(r.get("quality_score", 0) for r in rows) / n
        avg_h = sum(r.get("hook_score", 0) for r in rows) / n
        avg_d = sum(r.get("duration_seconds", 0) for r in rows) / n

        # Best hooks (top 5 by quality_score)
        best_hooks = [
            r.get("gancho", "")
            for r in sorted(rows, key=lambda x: x.get("quality_score", 0), reverse=True)[:5]
            if r.get("gancho")
        ]

        # Best AB variant
        variant_a = [r for r in rows if r.get("ab_variant") == "A"]
        variant_b = [r for r in rows if r.get("ab_variant") == "B"]
        avg_a = sum(r.get("quality_score", 0) for r in variant_a) / max(len(variant_a), 1)
        avg_b = sum(r.get("quality_score", 0) for r in variant_b) / max(len(variant_b), 1)

        return {
            "avg_quality": round(avg_q, 1),
            "avg_hook": round(avg_h, 1),
            "avg_duration": round(avg_d, 1),
            "best_hooks": best_hooks,
            "best_variant": "B" if avg_b > avg_a else "A",
            "best_cta_style": rows[0].get("cta", "") if rows else "",
            "total_videos": n,
        }

    except Exception as e:
        logger.debug(f"Niche analytics failed: {e}")
        return defaults
