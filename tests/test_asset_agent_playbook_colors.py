#!/usr/bin/env python
"""
Smoke tests for AssetAgent playbook primary color extraction.

Run with:
  python tests/test_asset_agent_playbook_colors.py
"""

import sys
from pathlib import Path


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agents.asset_agent import AssetAgent


class _FakePlaybook:
    def __init__(self, visual_language):
        self.visual_language = visual_language


def test_primary_colors_list_format():
    playbook = _FakePlaybook(
        {
            "primary_colors": [
                {"name": "Deep Navy", "hex": "#1a1a2e"},
                {"name": "Vibrant Gold", "hex": "#ffd700"},
                {"name": "Slate Gray", "hex": "#2d3436"},
            ]
        }
    )

    colors = AssetAgent._extract_playbook_primary_colors(playbook)
    assert colors[:2] == ["deep navy #1a1a2e", "vibrant gold #ffd700"]
    return True


def test_primary_colors_dict_format():
    playbook = _FakePlaybook(
        {
            "primary_colors": {
                "a": {"name": "Deep Navy", "hex": "#1a1a2e"},
                "b": {"name": "Vibrant Gold", "hex": "#ffd700"},
            }
        }
    )

    colors = AssetAgent._extract_playbook_primary_colors(playbook)
    assert "deep navy #1a1a2e" in colors
    assert "vibrant gold #ffd700" in colors
    return True


def test_primary_colors_invalid_format():
    playbook = _FakePlaybook({"primary_colors": "#ffffff"})
    colors = AssetAgent._extract_playbook_primary_colors(playbook)
    assert colors == []
    return True


def main():
    print("\n" + "=" * 60)
    print("ASSET AGENT PLAYBOOK COLORS SMOKE TEST")
    print("=" * 60)

    results = []

    try:
        results.append(("List format", test_primary_colors_list_format()))
        print("✅ List format")
    except Exception as exc:
        print(f"❌ List format failed: {exc}")
        results.append(("List format", False))

    try:
        results.append(("Dict format", test_primary_colors_dict_format()))
        print("✅ Dict format")
    except Exception as exc:
        print(f"❌ Dict format failed: {exc}")
        results.append(("Dict format", False))

    try:
        results.append(("Invalid format", test_primary_colors_invalid_format()))
        print("✅ Invalid format")
    except Exception as exc:
        print(f"❌ Invalid format failed: {exc}")
        results.append(("Invalid format", False))

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
