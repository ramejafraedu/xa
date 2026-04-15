import pytest
from pipeline.om_scoring import score_clip_candidate, select_best_clip_candidate

def test_score_clip_candidate_pexels():
    clip = {"clip_id": "123", "provider": "pexels"}
    score_obj = score_clip_candidate(clip, "cyberpunk city")
    
    # Pexels defaults: relevance=0.90, freshness=1.0, res=1.0, reliability=0.95
    # Weighted = (0.90 * 0.40) + (1.0 * 0.35) + (1.0 * 0.15) + (0.95 * 0.10)
    # Weighted = 0.36 + 0.35 + 0.15 + 0.095 = 0.955
    assert score_obj.provider == "pexels"
    assert "weighted_score" in score_obj.to_dict()
    assert score_obj.weighted_score > 0.90

def test_select_best_clip_candidate():
    candidates = [
        {"clip_id": "pib_456", "provider": "pixabay"},
        {"clip_id": "pex_123", "provider": "pexels"},
    ]
    
    # Pexels tiene mayor fiabilidad y relevancia base por ende debería ganar 
    best = select_best_clip_candidate(candidates, "hacker typing")
    assert best is not None
    assert best["provider"] == "pexels"
