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
    build_edit_decisions_artifact,
)


def test_resolve_composition_id_prefers_timeline() -> None:
    payload = {
        "composition_id": "UniversalCommercial",
        "meta": {"composition_id": "CinematicRenderer"},
    }
    resolved = _resolve_composition_id({"composition_id": "CinematicRenderer"}, payload)
    assert resolved == "UniversalCommercial", f"Unexpected composition resolution: {resolved}"


def test_resolve_composition_id_defaults_to_universal() -> None:
    resolved = _resolve_composition_id({}, None)
    assert resolved == "UniversalCommercial", f"Expected UniversalCommercial default, got: {resolved}"


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


def test_universal_commercial_extended_theme_and_config() -> None:
    payload = {
        "theme": "anime-ghibli",
        "playbook": "finanzas",
        "themeConfig": {
            "primaryColor": "#123456",
            "accentColor": "#FEDCBA",
            "headingFont": "Raleway",
        },
        "style": {
            "layoutVariant": "spotlight",
            "kineticLevel": "intense",
            "transitionPreset": "swipe",
            "featureCardMode": "plain",
        },
        "script": {
            "hook": "Hook",
            "solution": "Solution",
            "cta": "CTA",
            "features": [{"title": "Feature A", "subtitle": "Benefit"}],
        },
    }

    normalized = _normalize_timeline_props(
        payload,
        metadata={"titulo": "Fallback title"},
        composition_id="UniversalCommercial",
    )

    assert normalized["style"]["theme"] == "anime-ghibli"
    assert normalized["style"]["primaryColor"] == "#123456"
    assert normalized["style"]["accentColor"] == "#FEDCBA"
    assert normalized["style"]["layoutVariant"] == "spotlight"
    assert normalized["style"]["kineticLevel"] == "intense"
    assert normalized["style"]["transitionPreset"] == "swipe"
    assert normalized["style"]["featureCardMode"] == "plain"
    assert normalized.get("playbook") == "finanzas"
    assert isinstance(normalized.get("themeConfig"), dict)


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


def test_editor_timeline_visual_contract() -> None:
    timeline_path = settings.temp_dir / "timeline_visual_contract_smoke.json"
    timeline_path.parent.mkdir(parents=True, exist_ok=True)

    editor = EditorAgent()
    state = StoryState(topic="Demo topic", hook="Demo hook", platform="tiktok")
    state.style_profile.cut_speed = "ultra_rapido"
    state.style_profile.subtitle_style = "bold_animated"
    state.style_profile.transitions = ["whip", "cut"]
    state.style_profile.music_volume = 0.22
    state.color_palette = "#112233, #FFAA00"

    payload = editor.build_timeline_json(
        state=state,
        media_paths=[],
        decisions=[],
        audio_duration=6.0,
        timeline_path=timeline_path,
        composition_id="UniversalCommercial",
        style_playbook="finanzas",
    )

    assert payload.get("playbook") == "finanzas"
    assert payload.get("theme") == "minimalist-diagram"
    assert payload.get("style", {}).get("transitionPreset") == "swipe"
    assert payload.get("style", {}).get("kineticLevel") == "intense"
    assert payload.get("meta", {}).get("style_profile", {}).get("cut_speed") == "ultra_rapido"
    assert payload.get("captions", None) is None or payload.get("captions", {}).get("fontSize", 0) >= 50

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


def test_edit_decisions_artifact_generation() -> None:
    feature_image = str((settings.workspace / "temp" / "feature_edit_decision.png").resolve())

    timeline_payload = {
        "composition_id": "UniversalCommercial",
        "projectInfo": {"name": "Demo Brand"},
        "script": {
            "hook": "Hook",
            "solution": "Solution",
            "cta": "CTA",
            "features": [
                {
                    "title": "Feature A",
                    "subtitle": "Benefit",
                    "imagePath": feature_image,
                }
            ],
        },
        "audio": {
            "volume": 0.5,
        },
    }

    edit_path, edit_payload = build_edit_decisions_artifact(
        timeline_payload=timeline_payload,
        metadata={
            "timestamp": 1775940000001,
            "job_id": "test_job",
            "composition_id": "UniversalCommercial",
        },
        artifacts_dir=settings.temp_dir,
    )

    assert edit_path.exists(), "edit_decisions file missing"
    assert edit_payload.get("version") == "1.0"
    assert edit_payload.get("renderer_family") == "animation-first"
    assert len(edit_payload.get("cuts", [])) >= 1, "Expected at least one cut in edit_decisions"

    persisted = json.loads(edit_path.read_text(encoding="utf-8"))
    assert persisted.get("version") == "1.0"
    assert persisted.get("cuts", [{}])[0].get("source", "").startswith("workspace/")

    edit_path.unlink(missing_ok=True)


def test_edit_decisions_artifact_incremental_seed_fallback() -> None:
    fallback_media = str((settings.workspace / "temp" / "seed_clip.mp4").resolve())
    incremental_seed = {
        "version": "1.0",
        "mapper": "editor_incremental_v1",
        "cuts": [
            {
                "id": "seed_1",
                "source": fallback_media,
                "in_seconds": 0.0,
                "out_seconds": 2.4,
                "transition_out": "cut",
                "reason": "Seed fallback cut",
            }
        ],
        "metadata": {"media_count": 1},
    }

    timeline_payload = {
        "composition_id": "UniversalCommercial",
        "projectInfo": {"name": "Demo Brand"},
        "script": {
            "hook": "Hook",
            "solution": "Solution",
            "cta": "CTA",
            "features": [],
        },
    }

    edit_path, edit_payload = build_edit_decisions_artifact(
        timeline_payload=timeline_payload,
        metadata={
            "timestamp": 1775940000002,
            "job_id": "test_job",
            "composition_id": "UniversalCommercial",
        },
        artifacts_dir=settings.temp_dir,
        incremental_eml_seed=incremental_seed,
    )

    assert edit_path.exists(), "edit_decisions fallback file missing"
    assert len(edit_payload.get("cuts", [])) == 1, "Expected incremental seed fallback cut"
    assert edit_payload.get("cuts", [{}])[0].get("id") == "seed_1"

    meta = edit_payload.get("metadata", {})
    assert meta.get("cut_mapper") == "editor_incremental"
    assert meta.get("cut_mapper_fallback") is True
    assert int(meta.get("incremental_seed_cut_count", 0)) == 1

    edit_path.unlink(missing_ok=True)


def main() -> int:
    print("\n=== UNIVERSAL COMMERCIAL NORMALIZATION SMOKE ===")

    test_resolve_composition_id_prefers_timeline()
    print("PASS: composition id resolution")

    test_resolve_composition_id_defaults_to_universal()
    print("PASS: default composition fallback")

    test_universal_commercial_normalization()
    print("PASS: universal payload normalization")

    test_universal_commercial_extended_theme_and_config()
    print("PASS: universal extended theme + config")

    test_editor_timeline_embeds_composition_id()
    print("PASS: editor timeline composition wiring")

    test_editor_timeline_visual_contract()
    print("PASS: editor timeline visual contract")

    test_director_artifacts_generation()
    print("PASS: director artifacts generation")

    test_edit_decisions_artifact_generation()
    print("PASS: edit_decisions artifact generation")

    test_edit_decisions_artifact_incremental_seed_fallback()
    print("PASS: incremental EML fallback mapping")

    print("\nAll UniversalCommercial smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
