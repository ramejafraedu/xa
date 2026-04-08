"""Helpers to assemble publish-ready metadata for each generated video."""
from __future__ import annotations

import re

from config import settings

_HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")


def _extract_hashtags(text: str) -> list[str]:
    tags: list[str] = []
    for raw in _HASHTAG_RE.findall(text or ""):
        tag = f"#{raw.lower()}"
        if tag not in tags:
            tags.append(tag)
    return tags


def build_publish_package(
    title: str,
    hook: str,
    cta: str,
    caption: str,
    cover_path: str = "",
) -> dict:
    """Build normalized publish metadata used by dashboard/publishers."""
    title_clean = re.sub(r"\s+", " ", str(title or "")).strip()
    hook_clean = re.sub(r"\s+", " ", str(hook or "")).strip()
    cta_clean = re.sub(r"\s+", " ", str(cta or "")).strip()
    caption_clean = re.sub(r"\s+", " ", str(caption or "")).strip()

    hashtags: list[str] = []
    for source in (_extract_hashtags(caption_clean), _extract_hashtags(settings.default_hashtags)):
        for tag in source:
            if tag not in hashtags:
                hashtags.append(tag)

    if not hashtags:
        hashtags = ["#viral", "#fyp", "#shorts"]

    caption_no_tags = _HASHTAG_RE.sub("", caption_clean).strip()
    description = " ".join(filter(None, [hook_clean, caption_no_tags, cta_clean]))
    description = re.sub(r"\s+", " ", description).strip()[:500]

    hashtags_text = " ".join(hashtags[:12])
    comment_parts = [p for p in [title_clean, cta_clean] if p]
    comment = "\n\n".join(comment_parts)
    if hashtags_text:
        comment = (comment + "\n\n" + hashtags_text).strip()

    return {
        "title": title_clean,
        "description": description,
        "hashtags": hashtags[:12],
        "hashtags_text": hashtags_text,
        "comment": comment,
        "cover_path": str(cover_path or "").strip(),
    }
