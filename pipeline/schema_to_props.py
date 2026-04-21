"""Bridge FullEditingEngine schema → Remotion CinematicRenderer props.

The pipeline builds a schema via `tools.editing.EditingEngine.FullEditingEngine`
(with dynamic overlays appended by `tools.graphics.dynamic_overlays`). Remotion
consumes a different shape — the one validated by
`pipeline.renderer_remotion._normalize_timeline_props`:

    {
      "scenes": [{"kind": "video"|"image"|"title", "src", "startSeconds",
                  "durationSeconds", ...}],
      "soundtrack":   {"src", "volume", "fadeInSeconds", "fadeOutSeconds"},
      "music":        {"src", "volume", "fadeInSeconds", "fadeOutSeconds"},
      "captions":     {"words": [...], ...},  # optional
      "dynamicOverlays": [{"type", "text", "startSeconds", "durationSeconds",
                           "style", "position"}],
      "audioDurationInSeconds": float,
      "format":     "9:16" | "16:9",
      "resolution": {"width", "height"},
    }

This module is intentionally pure / side-effect free so it can be unit tested
without touching Remotion or the filesystem.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Union

from loguru import logger


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


def _infer_kind(asset_path: str) -> str:
    """Classify a local path as video / image for the Remotion composition."""
    if not asset_path:
        return "video"
    ext = Path(asset_path.split("?")[0]).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return "video"


def _resolution_for_format(fmt: str) -> dict[str, int]:
    if fmt == "16:9":
        return {"width": 1920, "height": 1080}
    return {"width": 1080, "height": 1920}


def _clamp_duration(value: Any, default: float = 3.5) -> float:
    try:
        d = float(value or default)
    except (TypeError, ValueError):
        d = default
    return max(0.8, round(d, 3))


def _collect_scene_layers(schema: dict) -> list[dict]:
    """Return video/image layers from `visual_assets` ordered by start_time.

    `FullEditingEngine.build_from_scenes` stores per-scene layers under keys
    like `scene_000`, `scene_001`. Captions live alongside but are handled
    separately; overlays from dynamic_overlays live in `schema["timeline"]`.
    """
    visual_assets = schema.get("visual_assets") or {}
    scenes: list[dict] = []
    if isinstance(visual_assets, dict):
        for key, layer in visual_assets.items():
            if not isinstance(layer, dict):
                continue
            if str(layer.get("type", "")).lower() not in {"video", "image"}:
                continue
            scenes.append({**layer, "_key": key})
    elif isinstance(visual_assets, list):
        for layer in visual_assets:
            if isinstance(layer, dict) and str(layer.get("type", "")).lower() in {"video", "image"}:
                scenes.append(dict(layer))

    scenes.sort(key=lambda item: float(item.get("start_time", 0.0) or 0.0))
    return scenes


def _collect_audio_layer(schema: dict, key: str) -> Optional[dict]:
    """Return audio layer dict from `audio_assets` by key (voiceover / music)."""
    assets = schema.get("audio_assets") or {}
    if isinstance(assets, dict):
        layer = assets.get(key)
        return dict(layer) if isinstance(layer, dict) else None
    return None


def _overlay_layers(schema: dict) -> list[dict]:
    """Return overlay dicts from `schema['timeline']` (dynamic_overlays output)."""
    timeline = schema.get("timeline") or []
    if not isinstance(timeline, list):
        return []
    overlays: list[dict] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        layer_type = str(item.get("type", "")).lower()
        if layer_type in {"video", "audio"}:
            continue
        overlays.append(item)
    return overlays


def _normalize_overlay(overlay: dict) -> Optional[dict]:
    """Shape a single overlay into the strict dynamicOverlays contract."""
    overlay_type = str(overlay.get("type", "")).strip()
    if not overlay_type:
        return None
    start = float(overlay.get("start_time", 0.0) or 0.0)
    duration = float(overlay.get("duration", 0.0) or 0.0)
    if duration <= 0:
        return None
    normalized = {
        "type": overlay_type,
        "startSeconds": round(max(0.0, start), 3),
        "durationSeconds": round(max(0.1, duration), 3),
        "style": str(overlay.get("style", "") or "default"),
        "position": str(overlay.get("position", "") or "center"),
    }
    text = overlay.get("text")
    if isinstance(text, str) and text.strip():
        normalized["text"] = text.strip()
    return normalized


def schema_to_remotion_props(
    schema: Union[dict, str, Path],
    *,
    voiceover_path: Optional[str] = None,
    music_path: Optional[str] = None,
    audio_duration: Optional[float] = None,
    titulo: Optional[str] = None,
) -> dict:
    """Convert a FullEditingEngine schema into Remotion CinematicRenderer props.

    `voiceover_path`, `music_path` and `audio_duration` let the caller override
    the values baked into the schema (useful when assets are resolved later in
    the pipeline than the schema builder).
    """
    if isinstance(schema, (str, Path)):
        schema_dict: dict = json.loads(Path(schema).read_text(encoding="utf-8"))
    elif isinstance(schema, dict):
        schema_dict = schema
    else:
        raise TypeError(f"Unsupported schema type: {type(schema)!r}")

    fmt = str(schema_dict.get("format", "9:16") or "9:16")
    resolution = schema_dict.get("resolution") or _resolution_for_format(fmt)

    scene_layers = _collect_scene_layers(schema_dict)
    scenes: list[dict] = []
    cursor = 0.0
    for idx, layer in enumerate(scene_layers):
        asset = str(layer.get("asset") or layer.get("src") or "").strip()
        if not asset:
            continue
        duration = _clamp_duration(layer.get("duration"), default=3.5)
        start = float(layer.get("start_time", cursor) or cursor)
        kind = _infer_kind(asset)
        scene: dict = {
            "id": str(layer.get("_key") or layer.get("id") or f"scene_{idx + 1}"),
            "kind": kind,
            "src": asset,
            "startSeconds": round(max(0.0, start), 3),
            "durationSeconds": duration,
            "tone": str(layer.get("tone", "steel") or "steel"),
            "fadeInFrames": int(layer.get("fade_in_frames", 6) or 6),
            "fadeOutFrames": int(layer.get("fade_out_frames", 6) or 6),
            "filter": str(layer.get("filter", "contrast(1.06) saturate(0.92) brightness(0.98)")),
        }
        if kind == "image":
            scene["animation"] = str(layer.get("animation", "kenBurns"))
            scene["animationIntensity"] = float(layer.get("animation_intensity", 1.2) or 1.2)
        scenes.append(scene)
        cursor = start + duration

    # Fallback title scene when visual_assets is empty (prevents render crash)
    if not scenes:
        scenes.append({
            "id": "fallback_title",
            "kind": "title",
            "text": (titulo or str(schema_dict.get("metadata", {}).get("titulo") or "Video Factory"))[:80],
            "startSeconds": 0.0,
            "durationSeconds": round(max(3.0, float(audio_duration or 8.0)), 3),
            "accent": "#86d8ff",
            "intensity": 1.0,
        })
        logger.warning("schema_to_remotion_props: no visual scenes found — using title fallback")

    total_seconds = round(max((s["startSeconds"] + s["durationSeconds"]) for s in scenes), 3)

    props: dict = {
        "scenes": scenes,
        "titleFontSize": 72 if fmt == "16:9" else 84,
        "titleWidth": 860 if fmt == "16:9" else 960,
        "signalLineCount": 18,
        "format": fmt,
        "resolution": {
            "width": int(resolution.get("width", 1080)),
            "height": int(resolution.get("height", 1920)),
        },
        "audioDurationInSeconds": round(float(audio_duration or total_seconds), 3),
    }

    narration = _collect_audio_layer(schema_dict, "voiceover") or {}
    narration_src = voiceover_path or str(narration.get("asset", "") or "").strip()
    if narration_src:
        props["soundtrack"] = {
            "src": narration_src,
            "volume": float(narration.get("volume", 1.0) or 1.0),
            "fadeInSeconds": float(narration.get("fade_in", 0.15) or 0.15),
            "fadeOutSeconds": float(narration.get("fade_out", 0.30) or 0.30),
        }

    bg = _collect_audio_layer(schema_dict, "background_music") or {}
    bg_src = music_path or str(bg.get("asset", "") or "").strip()
    if bg_src:
        props["music"] = {
            "src": bg_src,
            "volume": float(bg.get("volume", 0.14) or 0.14),
            "fadeInSeconds": float(bg.get("fade_in", 1.5) or 1.5),
            "fadeOutSeconds": float(bg.get("fade_out", 2.0) or 2.0),
        }

    overlays_raw = _overlay_layers(schema_dict)
    dynamic_overlays: list[dict] = []
    for overlay in overlays_raw:
        normalized = _normalize_overlay(overlay)
        if normalized:
            dynamic_overlays.append(normalized)

    audio_cap = float(props.get("audioDurationInSeconds") or 0.0)
    if dynamic_overlays and audio_cap > 0.2:
        clamped: list[dict] = []
        for o in dynamic_overlays:
            st = float(o.get("startSeconds", 0.0) or 0.0)
            dur = float(o.get("durationSeconds", 0.0) or 0.0)
            if st >= audio_cap - 0.04:
                continue
            if st + dur > audio_cap:
                dur = max(0.12, audio_cap - st - 0.02)
            o["startSeconds"] = round(max(0.0, st), 3)
            o["durationSeconds"] = round(max(0.12, dur), 3)
            clamped.append(o)
        dynamic_overlays = clamped

    if dynamic_overlays:
        props["dynamicOverlays"] = dynamic_overlays
        logger.info(
            f"schema_to_remotion_props: {len(dynamic_overlays)} dynamic overlays "
            f"(audio_cap={audio_cap:.2f}s)"
        )

    return props
