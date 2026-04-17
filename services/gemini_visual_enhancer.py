"""Gemini Visual Enhancer — V16.1 PRO.

Turns a raw Spanish scene phrase (+ niche visual direction + optional style
playbook) into an ultra-specific English visual prompt that feeds:

- ``pipeline/image_gen.py`` (stock image queries and AI image prompts)
- ``pipeline/video_stock.py`` (stock video keyword seeding)

Design:
- One Gemini call per job batch (not per scene) to save budget: the enhancer
  returns a JSON list with N entries, one per scene.
- Soft-fails: on any error we return the input phrases untouched. The
  pipeline continues with legacy heuristics.
- In-memory LRU cache keyed by (niche, style, scenes hash) avoids recomputing
  in consecutive calls during the same pipeline run.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from loguru import logger

try:
    from config import settings
except Exception:  # pragma: no cover
    settings = None  # type: ignore

try:
    from services.llm_router import call_llm_primary_gemini
except Exception:  # pragma: no cover
    call_llm_primary_gemini = None  # type: ignore


_CACHE: dict[str, list[dict]] = {}
_CACHE_MAX = 64


_SYSTEM = (
    "You are a cinematic art director for viral shorts (TikTok/Reels/YouTube Shorts). "
    "Your job is to turn Spanish scene lines into ULTRA-SPECIFIC English visual prompts "
    "for stock video/image search AND for AI image generation. Each prompt must have: "
    "a concrete subject, an environment, lighting, mood, and camera framing. "
    "Never use generic words like 'a person', 'modern', 'stunning', 'amazing', 'incredible', "
    "'cutting-edge', 'beautiful'. Keep the same chronological meaning as the Spanish source."
)


def _scenes_hash(scenes: list[str]) -> str:
    h = hashlib.sha256()
    for s in scenes:
        h.update((s or "").strip().encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:24]


def _cache_key(niche_visual: str, style_playbook: Optional[str], scenes: list[str]) -> str:
    return f"{(niche_visual or '').strip()[:60]}|{style_playbook or ''}|{_scenes_hash(scenes)}"


def _extract_json_list(text: str) -> list:
    if not text:
        return []
    t = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
    t = re.sub(r"```\s*", "", t).strip()
    start = t.find("[")
    end = t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        return json.loads(t[start: end + 1])
    except Exception:
        pass
    try:
        fixed = re.sub(r",\s*([}\]])", r"\1", t[start: end + 1])
        return json.loads(fixed)
    except Exception:
        return []


def enhance_scene_prompts(
    scenes: list[str],
    niche_visual: str = "",
    style_playbook: Optional[str] = None,
    tone: str = "",
) -> list[dict]:
    """Return per-scene enriched prompts.

    Each entry has:
        {
          "visual_prompt": str,   # rich English prompt for AI image gen
          "stock_keywords": [str, ...],  # 3-6 English keywords for Pexels/Pixabay
          "mood": str
        }

    On any failure, returns empty list and caller must fall back to heuristic.
    """
    if not scenes:
        return []
    if call_llm_primary_gemini is None:
        logger.debug("[gemini_visual] LLM router unavailable")
        return []
    # Budget / policy guard: only run when Gemini is explicitly enabled.
    if settings is None or not getattr(settings, "gemini_everywhere_mode", False):
        return []

    key = _cache_key(niche_visual, style_playbook, scenes)
    if key in _CACHE:
        return _CACHE[key]

    trimmed = [((s or "").strip()[:220]) for s in scenes]
    user = (
        f"NICHE VISUAL DIRECTION: {niche_visual or '(none)'}\n"
        f"STYLE PLAYBOOK: {style_playbook or '(none)'}\n"
        f"TONE: {tone or '(none)'}\n"
        f"\nSpanish scene lines (one per scene, in order):\n"
        + "\n".join(f"{i+1}. {s}" for i, s in enumerate(trimmed))
        + "\n\n"
        "Return a STRICT JSON array with EXACTLY one object per scene, in the same order. "
        "Schema: [{\"visual_prompt\": str, \"stock_keywords\": [str, ...], \"mood\": str}]\n"
        "Rules:\n"
        "- visual_prompt: 18-35 words, concrete subject + environment + lighting + mood + framing.\n"
        "- stock_keywords: 3-6 English keywords, short and searchable (no punctuation).\n"
        "- mood: single English word (tense, mysterious, triumphant, somber, curious, ...).\n"
        "- No markdown, no code fences, no commentary — ONLY the JSON array."
    )

    try:
        text, model_used = call_llm_primary_gemini(
            system_prompt=_SYSTEM,
            user_prompt=user,
            temperature=0.55,
            timeout=40,
            max_retries=1,
            purpose="gemini_visual_enhancer",
        )
    except Exception as exc:
        logger.debug(f"[gemini_visual] LLM call failed: {exc}")
        return []

    parsed = _extract_json_list(text)
    if not isinstance(parsed, list) or len(parsed) < max(1, len(scenes) // 2):
        logger.debug(f"[gemini_visual] parse failed or short ({len(parsed)}/{len(scenes)})")
        return []

    # Normalize and pad to match scene count.
    out: list[dict] = []
    for i in range(len(scenes)):
        item = parsed[i] if i < len(parsed) and isinstance(parsed[i], dict) else {}
        out.append({
            "visual_prompt": str(item.get("visual_prompt") or "").strip()[:480],
            "stock_keywords": [str(k).strip()[:40] for k in (item.get("stock_keywords") or []) if str(k).strip()][:6],
            "mood": str(item.get("mood") or "").strip()[:30],
        })

    # Cache
    if len(_CACHE) >= _CACHE_MAX:
        # drop oldest
        try:
            first = next(iter(_CACHE))
            _CACHE.pop(first, None)
        except StopIteration:
            pass
    _CACHE[key] = out
    logger.info(
        f"[gemini_visual] enhanced {len(out)} scenes via {model_used} "
        f"(niche={niche_visual[:40]!r}, style={style_playbook})"
    )
    return out


def enhanced_keywords(
    scenes: list[str],
    niche_visual: str = "",
    style_playbook: Optional[str] = None,
    max_keywords: int = 8,
) -> list[str]:
    """Return a flat, deduped list of stock keywords derived from scenes."""
    enriched = enhance_scene_prompts(scenes, niche_visual, style_playbook)
    seen: set[str] = set()
    flat: list[str] = []
    for item in enriched:
        for k in item.get("stock_keywords", []):
            key = k.lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            flat.append(k)
            if len(flat) >= max_keywords:
                return flat
    return flat


def enhanced_visual_prompt_for_index(
    scenes: list[str],
    index: int,
    niche_visual: str = "",
    style_playbook: Optional[str] = None,
    fallback: str = "",
) -> str:
    """Return a single-scene English visual prompt."""
    enriched = enhance_scene_prompts(scenes, niche_visual, style_playbook)
    if 0 <= index < len(enriched):
        vp = enriched[index].get("visual_prompt", "")
        if vp:
            return vp
    return fallback
