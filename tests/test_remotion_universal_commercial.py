#!/usr/bin/env python
"""Smoke test for UniversalCommercial remotion normalization support."""

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config import settings
from core.state import StoryState
from agents.editor_agent import EditorAgent
from pipeline.renderer_remotion import (
    _normalize_timeline_props,
    _resolve_composition_id,
    build_director_artifacts,
)


def test_resolve_composition_id_prefers_timeline() -> None:
    payload = {
        "composition_id": "UniversalCommercial",
        "meta": {"composition_id": "CinematicRenderer"},
    }
    resolved = _resolve_composition_id({"composition_id": "CinematicRenderer"}, payload)
    assert resolved == "UniversalCommercial", f"Unexpected composition resolution: {resolved}"


def test_universal_commercial_normalization() -> None:
    fake_image = str((settings.workspace / "temp" / "feature_demo.png").resolve())
    fake_music = str((settings.workspace / "temp" / "bgm_demo.mp3").resolve())

    payload = {
        "theme": "unknown-theme",
        "projectInfo": {
            "name": "Demo Brand",
            "tagline": "Crece con video",
        },
        "script": {
            "hook": "Hook demo",
            "solution": "Solution demo",
            "cta": "Actua ahora",
            "features": [
                {
                    "title": "Feature A",
                    "subtitle": "Beneficio principal",
                    "imagePath": fake_image,
                }
            ],
        },
        "music": {
            "src": fake_music,
            "volume": 0.7,
        },
    }

    normalized = _normalize_timeline_props(
        payload,
        metadata={"titulo": "Fallback title"},
        composition_id="UniversalCommercial",
    )

    assert normalized["style"]["theme"] == "minimal"
    assert normalized["projectInfo"]["name"] == "Demo Brand"
    assert normalized["script"]["hook"] == "Hook demo"
    assert normalized["script"]["features"], "Expected normalized feature list"
    assert normalized["script"]["features"][0].get("imagePath", "").startswith("workspace/")
    assert normalized["audio"].get("bgmPath", "").startswith("workspace/")


def test_editor_timeline_embeds_composition_id() -> None:
    timeline_path = settings.temp_dir / "timeline_universal_commercial_smoke.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)

    editor = EditorAgent()
    state = StoryState(topic="Demo topic", hook="Demo hook")

    payload = editor.build_timeline_json(
        state=state,
        media_paths=[],
        decisions=[],
        audio_duration=6.0,
        timeline_path=timeline_path,
        composition_id="UniversalCommercial",
    )

    assert payload.get("composition_id") == "UniversalCommercial"
    assert payload.get("meta", {}).get("composition_id") == "UniversalCommercial"

    persisted = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert persisted.get("composition_id") == "UniversalCommercial"
    assert persisted.get("meta", {}).get("composition_id") == "UniversalCommercial"

    timeline_path.unlink(missing_ok=True)


def test_director_artifacts_generation() -> None:
    timeline_payload = {
        "composition_id": "UniversalCommercial",
        "projectInfo": {"name": "Demo Brand"},
        "style": {
            "theme": "minimal",
            "primaryColor": "#334155",
            "accentColor": "#F97316",
        },
        "script": {
            "hook": "Hook",
            "solution": "Solution",
            "cta": "CTA",
            "features": [
                {
                    "title": "Feature A",
                    "subtitle": "Benefit",
                }
            ],
        },
    }

    director_path, director_meta_path, director_payload, director_meta = build_director_artifacts(
        timeline_payload=timeline_payload,
        metadata={
            "timestamp": 1775940000000,
            "job_id": "test_job",
            "composition_id": "UniversalCommercial",
        },
        artifacts_dir=settings.temp_dir,
    )

    assert director_path.exists(), "director.json file missing"
    assert director_meta_path.exists(), "director_meta.json file missing"
    assert director_payload.get("composition_id") == "UniversalCommercial"
    assert director_meta.get("props_hash"), "Missing props_hash in director_meta"

    persisted_director = json.loads(director_path.read_text(encoding="utf-8"))
    persisted_meta = json.loads(director_meta_path.read_text(encoding="utf-8"))
    assert persisted_director.get("version") == "1.0"
    assert persisted_meta.get("version") == "1.0"

    director_path.unlink(missing_ok=True)
    director_meta_path.unlink(missing_ok=True)


def main() -> int:
    print("\n=== UNIVERSAL COMMERCIAL NORMALIZATION SMOKE ===")

    test_resolve_composition_id_prefers_timeline()
    print("PASS: composition id resolution")

    test_universal_commercial_normalization()
    print("PASS: universal payload normalization")

    test_editor_timeline_embeds_composition_id()
    print("PASS: editor timeline composition wiring")

    test_director_artifacts_generation()
    print("PASS: director artifacts generation")

    print("\nAll UniversalCommercial smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
