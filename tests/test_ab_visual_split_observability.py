#!/usr/bin/env python
"""
Smoke tests for A/B visual split observability in dashboard APIs.

Run with:
  python tests/test_ab_visual_split_observability.py
"""

import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import dashboard


def _sample_manifest(job_id: str) -> dict:
    return {
        "job_id": job_id,
        "nicho_slug": "finanzas",
        "status": "completed",
        "timestamp": 1775842980202,
        "titulo": "Smoke AB",
        "timings": {"assets": 12.4, "render": 21.0},
        "ab_visual_split": {
            "enabled": True,
            "multiplier": 2,
            "runtime_override_enabled": True,
            "runtime_override_multiplier": True,
            "requested_images_count": 4,
            "selected_variant": "A",
            "selection_decision": "promote",
            "selection_mode": "saar_ab_scoring",
            "selection_score": 8.12,
            "selection_reason": "quality=8.10, viral=8.20, qa=pass",
            "saar_enabled": True,
            "saar_use_winner": False,
            "saar_winner_applied": False,
            "saar_candidate_count": 2,
            "saar_selected_variant": "A",
            "saar_selection_mode": "size_bytes_desc",
            "saar_selection_reason": "winner=max_file_size_bytes",
            "qa_gate_passed": True,
            "qa_skipped": False,
            "qa_penalty": 0.0,
        },
        "decision_trail": [
            {
                "stage": "media",
                "label": "Media selected",
                "detail": "A/B split applied",
                "severity": "info",
                "timestamp": 1775843113499,
                "metadata": {
                    "ab_visual_split": {
                        "enabled": True,
                        "multiplier": 2,
                        "target_clips": 8,
                        "fetched_stock_clips": 5,
                        "generated_images": 4,
                    }
                },
            }
        ],
    }


def test_extract_ab_visual_split():
    """Helper should normalize direct + decision trail fields."""
    manifest = _sample_manifest("ab_helper_1")
    split = dashboard._extract_ab_visual_split(manifest)

    assert split["enabled"] is True
    assert split["multiplier"] == 2
    assert split["target_clips"] == 8
    assert split["fetched_stock_clips"] == 5
    assert split["generated_images"] == 4
    assert split["runtime_override_enabled"] is True
    assert split["runtime_override_multiplier"] is True
    assert split["requested_images_count"] == 4
    assert split["selected_variant"] == "A"
    assert split["selection_decision"] == "promote"
    assert split["selection_mode"] == "saar_ab_scoring"
    assert abs(split["selection_score"] - 8.12) < 0.01
    assert split["saar_enabled"] is True
    assert split["saar_use_winner"] is False
    assert split["saar_candidate_count"] == 2
    assert split["saar_selected_variant"] == "A"
    assert split["saar_selection_mode"] == "size_bytes_desc"
    assert split["qa_gate_passed"] is True
    assert split["qa_skipped"] is False
    assert abs(split["qa_penalty"] - 0.0) < 0.01
    assert len(split["decision_events"]) == 1

    return True


def test_dashboard_ab_routes_and_operations():
    """API endpoints should expose AB diagnostics and runtime config controls."""
    original_workspace_dir = dashboard.settings.workspace_dir
    original_enable_ab = dashboard.settings.enable_ab_visual_split
    original_ab_multiplier = dashboard.settings.ab_visual_split_multiplier
    original_saar_enabled = dashboard.settings.enable_saar_composer
    original_saar_use_winner = dashboard.settings.saar_composer_use_winner
    original_gemini_everywhere = dashboard.settings.gemini_everywhere_mode
    original_silence_trim = dashboard.settings.enable_smart_silence_trim
    original_post_tts = dashboard.settings.enable_post_tts_loudnorm

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        temp_dir = root / "temp"
        output_dir = root / "output"
        review_dir = output_dir / "review_manual"
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)

        job_id = "ab_api_1"
        manifest_path = output_dir / f"job_manifest_{job_id}.json"
        manifest_path.write_text(
            json.dumps(_sample_manifest(job_id), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        dashboard.settings.workspace_dir = str(root)

        try:
            client = TestClient(dashboard.app)

            detail = client.get(f"/api/jobs/{job_id}")
            assert detail.status_code == 200
            detail_ab = detail.json().get("ab_visual_split", {})
            assert detail_ab.get("enabled") is True
            assert detail_ab.get("multiplier") == 2

            analysis = client.get(f"/api/jobs/{job_id}/analysis")
            assert analysis.status_code == 200
            analysis_ab = analysis.json().get("ab_visual_split", {})
            assert analysis_ab.get("enabled") is True
            assert analysis_ab.get("multiplier") == 2

            split_route = client.get(f"/api/jobs/{job_id}/ab-visual-split")
            assert split_route.status_code == 200
            split_payload = split_route.json().get("ab_visual_split", {})
            assert split_payload.get("enabled") is True
            assert split_payload.get("multiplier") == 2
            assert split_payload.get("target_clips") == 8
            assert split_payload.get("selected_variant") == "A"
            assert split_payload.get("selection_decision") == "promote"
            assert split_payload.get("selection_mode") == "saar_ab_scoring"
            assert abs(float(split_payload.get("selection_score", 0.0)) - 8.12) < 0.01
            assert split_payload.get("saar_enabled") is True
            assert split_payload.get("saar_candidate_count") == 2

            jobs = client.get("/api/jobs?limit=20")
            assert jobs.status_code == 200
            rows = jobs.json()
            row = next((item for item in rows if item.get("job_id") == job_id), None)
            assert row is not None
            row_ab = row.get("ab_visual_split", {})
            assert row_ab.get("enabled") is True
            assert row_ab.get("multiplier") == 2

            cfg = client.get("/api/config/operations")
            assert cfg.status_code == 200
            cfg_body = cfg.json()
            assert "enable_ab_visual_split" in cfg_body
            assert "ab_visual_split_multiplier" in cfg_body
            assert "enable_saar_composer" in cfg_body
            assert "saar_composer_use_winner" in cfg_body
            assert "gemini_everywhere_mode" in cfg_body
            assert "enable_smart_silence_trim" in cfg_body
            assert "enable_post_tts_loudnorm" in cfg_body

            upd = client.post(
                "/api/config/operations",
                json={
                    "enable_ab_visual_split": True,
                    "ab_visual_split_multiplier": 3,
                    "enable_saar_composer": True,
                    "saar_composer_use_winner": False,
                    "gemini_everywhere_mode": True,
                    "enable_smart_silence_trim": True,
                    "enable_post_tts_loudnorm": True,
                    "persist": False,
                },
            )
            assert upd.status_code == 200
            updated = upd.json().get("updated", {})
            assert updated.get("enable_ab_visual_split") is True
            assert updated.get("ab_visual_split_multiplier") == 3
            assert updated.get("enable_saar_composer") is True
            assert updated.get("saar_composer_use_winner") is False
            assert updated.get("gemini_everywhere_mode") is True
            assert updated.get("enable_smart_silence_trim") is True
            assert updated.get("enable_post_tts_loudnorm") is True
        finally:
            dashboard.settings.workspace_dir = original_workspace_dir
            dashboard.settings.enable_ab_visual_split = original_enable_ab
            dashboard.settings.ab_visual_split_multiplier = original_ab_multiplier
            dashboard.settings.enable_saar_composer = original_saar_enabled
            dashboard.settings.saar_composer_use_winner = original_saar_use_winner
            dashboard.settings.gemini_everywhere_mode = original_gemini_everywhere
            dashboard.settings.enable_smart_silence_trim = original_silence_trim
            dashboard.settings.enable_post_tts_loudnorm = original_post_tts

    return True


def main():
    """Run all A/B observability smoke tests."""
    print("\n" + "=" * 60)
    print("A/B OBSERVABILITY SMOKE TEST")
    print("=" * 60)

    results = []

    try:
        results.append(("AB Extract Helper", test_extract_ab_visual_split()))
        print("✅ AB Extract Helper")
    except Exception as exc:
        print(f"❌ AB Extract Helper failed: {exc}")
        results.append(("AB Extract Helper", False))

    try:
        results.append(("Dashboard AB Routes + Operations", test_dashboard_ab_routes_and_operations()))
        print("✅ Dashboard AB Routes + Operations")
    except Exception as exc:
        print(f"❌ Dashboard AB Routes + Operations failed: {exc}")
        results.append(("Dashboard AB Routes + Operations", False))

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
