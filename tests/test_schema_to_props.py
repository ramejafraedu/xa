"""Unit tests for pipeline.schema_to_props — the FullEditingEngine→Remotion bridge.

These tests never invoke `npx remotion` or the filesystem beyond tmp paths so
they can run on any machine without the Remotion toolchain installed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.schema_to_props import schema_to_remotion_props


@pytest.fixture
def minimal_schema() -> dict:
    """Mimic the shape produced by FullEditingEngine.build_from_scenes."""
    return {
        "version": "2.0",
        "format": "9:16",
        "resolution": {"width": 1080, "height": 1920},
        "fps": 30,
        "visual_assets": {
            "scene_000": {
                "type": "video",
                "asset": "C:/workspace/clip_0.mp4",
                "start_time": 0.0,
                "duration": 3.5,
                "effects": ["zoom_pulse"],
            },
            "scene_001": {
                "type": "image",
                "asset": "/tmp/frame.png",
                "start_time": 3.5,
                "duration": 2.5,
            },
            "caption_000": {
                "type": "caption",
                "text": "Hook retention",
                "start_time": 0.0,
                "duration": 3.5,
            },
        },
        "audio_assets": {
            "voiceover": {
                "type": "audio",
                "asset": "/tmp/voice.mp3",
                "volume": 1.0,
                "fade_out": 0.3,
            },
            "background_music": {
                "type": "audio",
                "asset": "/tmp/bgm.mp3",
                "volume": 0.14,
                "fade_in": 1.5,
                "fade_out": 2.0,
            },
        },
        "timeline": [
            {
                "type": "hook_kinetic",
                "text": "Esto te va a volar la cabeza",
                "start_time": 0.4,
                "duration": 2.6,
                "style": "bold_impact",
                "position": "center",
            },
            {
                "type": "flash_pop",
                "start_time": 5.2,
                "duration": 0.35,
                "style": "white_flash",
            },
        ],
        "metadata": {"titulo": "Video demo"},
    }


def test_scenes_are_mapped_with_correct_kinds(minimal_schema: dict):
    props = schema_to_remotion_props(minimal_schema, audio_duration=6.0)

    assert len(props["scenes"]) == 2
    assert props["scenes"][0]["kind"] == "video"
    assert props["scenes"][0]["src"].endswith("clip_0.mp4")
    assert props["scenes"][1]["kind"] == "image"
    assert props["scenes"][1]["durationSeconds"] == 2.5


def test_format_and_resolution_preserved(minimal_schema: dict):
    props = schema_to_remotion_props(minimal_schema, audio_duration=6.0)

    assert props["format"] == "9:16"
    assert props["resolution"] == {"width": 1080, "height": 1920}


def test_audio_tracks_and_overrides(minimal_schema: dict):
    props = schema_to_remotion_props(
        minimal_schema,
        voiceover_path="/override/voice.mp3",
        music_path="/override/bgm.mp3",
        audio_duration=7.0,
    )

    assert props["soundtrack"]["src"] == "/override/voice.mp3"
    assert props["music"]["src"] == "/override/bgm.mp3"
    assert props["audioDurationInSeconds"] == 7.0


def test_dynamic_overlays_are_normalized(minimal_schema: dict):
    props = schema_to_remotion_props(minimal_schema, audio_duration=6.0)

    overlays = props["dynamicOverlays"]
    assert len(overlays) == 2
    hook = next(o for o in overlays if o["type"] == "hook_kinetic")
    assert hook["text"] == "Esto te va a volar la cabeza"
    assert hook["startSeconds"] == 0.4
    assert hook["durationSeconds"] == 2.6
    assert hook["position"] == "center"
    assert hook["style"] == "bold_impact"

    flash = next(o for o in overlays if o["type"] == "flash_pop")
    assert flash["style"] == "white_flash"
    assert flash["durationSeconds"] > 0


def test_fallback_title_when_no_scenes():
    schema = {
        "format": "9:16",
        "visual_assets": {},
        "audio_assets": {},
        "timeline": [],
        "metadata": {"titulo": "Solo audio"},
    }

    props = schema_to_remotion_props(schema, audio_duration=10.0)
    assert len(props["scenes"]) == 1
    assert props["scenes"][0]["kind"] == "title"
    assert props["scenes"][0]["durationSeconds"] >= 3.0


def test_schema_can_be_loaded_from_path(tmp_path: Path, minimal_schema: dict):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(minimal_schema), encoding="utf-8")

    props = schema_to_remotion_props(schema_path, audio_duration=6.0)
    assert len(props["scenes"]) == 2
    assert props["dynamicOverlays"]


def test_render_modules_are_importable():
    """Smoke test: renderer_remotion must import cleanly (guards against the
    historical IndentationError regression at L63)."""
    import importlib

    mod = importlib.import_module("pipeline.renderer_remotion")
    assert hasattr(mod, "render_with_remotion")
    assert hasattr(mod, "_normalize_timeline_props")
    assert hasattr(mod, "_run_remotion_with_recovery")
