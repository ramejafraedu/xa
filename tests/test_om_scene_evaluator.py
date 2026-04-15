import pytest
from pipeline.om_scene_evaluator import evaluate_composition_plan

def test_empty_plan():
    res = evaluate_composition_plan([])
    assert res["is_slideshow_risk"] is True
    assert res["score"] == 5.0
    assert res["verdict"] == "fail"

def test_slideshow_risk_static_overload():
    # Creamos 5 escenas, 4 son static
    specs = [
        {"shot_type": "medium", "motion": "static", "emotion": "neutral", "clip_description": "A man looking at a chart"},
        {"shot_type": "medium", "motion": "static", "emotion": "neutral", "clip_description": "A woman typing"},
        {"shot_type": "wide", "motion": "static", "emotion": "neutral", "clip_description": "An office building"},
        {"shot_type": "close-up", "motion": "static", "emotion": "neutral", "clip_description": "A coffee cup"},
        {"shot_type": "medium", "motion": "pan", "emotion": "neutral", "clip_description": "Street level"},
    ]
    res = evaluate_composition_plan(specs)
    assert res["is_slideshow_risk"] is True
    # Debería tener violación de estáticos
    assert any("4/5 escenas son estáticas" in v for v in res["violations"])

def test_repetitive_shots():
    specs = [
        {"shot_type": "close-up", "motion": "dynamic", "emotion": "happy", "clip_description": "a specific detail"},
        {"shot_type": "close-up", "motion": "dynamic", "emotion": "happy", "clip_description": "another detail"},
        {"shot_type": "close-up", "motion": "dynamic", "emotion": "happy", "clip_description": "third detail"},
        {"shot_type": "close-up", "motion": "dynamic", "emotion": "happy", "clip_description": "fourth detail"},
    ]
    res = evaluate_composition_plan(specs)
    assert any("transiciones entre planos del mismo encuadre" in v for v in res["violations"])
    assert res["score"] > 0

def test_strong_plan():
    specs = [
        {"shot_type": "wide", "motion": "pan", "emotion": "happy", "clip_description": "A bustling futuristic city"},
        {"shot_type": "medium", "motion": "slow", "emotion": "curious", "clip_description": "A scientist examining a glowing artifact"},
        {"shot_type": "close-up", "motion": "dynamic", "emotion": "intense", "clip_description": "The artifact pulsing with light"},
        {"shot_type": "aerial", "motion": "timelapse", "emotion": "awe", "clip_description": "Clouds rushing over the laboratory"},
    ]
    res = evaluate_composition_plan(specs)
    assert res["is_slideshow_risk"] is False
    assert res["verdict"] in ("strong", "acceptable")
    assert res["score"] < 3.0
