#!/usr/bin/env python
"""
Smoke test for Playbook System + Provider Selector Integration (Phase 1).

Tests:
1. Playbook loader loads all 5 playbooks
2. Provider selectors choose providers correctly
3. Cost optimization works

Run with:
  python tests/test_phase1_integration.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.playbook_loader import get_playbook_loader, get_playbook
from core.provider_decision_maker import get_decision_maker
from config import settings


def test_playbook_loading():
    """Test that all playbooks load correctly."""
    print("\n=== TEST 1: Playbook Loading ===")
    
    loader = get_playbook_loader()
    available = loader.list_available_playbooks()
    
    print(f"✅ Found {len(available)} playbooks:")
    for niche, description in available.items():
        playbook = get_playbook(niche)
        if playbook:
            print(f"   - {niche}: {playbook.name}")
            # Verify key fields exist
            assert playbook.identity, f"Missing identity in {niche}"
            assert playbook.visual_language, f"Missing visual_language in {niche}"
            assert playbook.motion, f"Missing motion in {niche}"
        else:
            print(f"   ❌ Failed to load {niche}")
            return False
    
    return True


def test_provider_selection():
    """Test provider selection logic."""
    print("\n=== TEST 2: Provider Selection ===")
    
    maker = get_decision_maker({
        'prefer_free_providers': True,
        'quality_tier': 'balanced'
    })
    
    # Test TTS selection
    script = "Este es un test de selección de proveedores. " * 20
    tts_decision = maker.select_tts(script)
    print(f"✅ TTS: {tts_decision.provider_name} (${tts_decision.cost_usd:.4f})")
    assert tts_decision.provider_name, "No TTS provider selected"
    
    # Test Image selection
    img_decision = maker.select_images(6)
    print(f"✅ Images: {img_decision.provider_name} (${img_decision.cost_usd:.4f})")
    assert img_decision.provider_name, "No image provider selected"
    
    # Test Music selection
    music_decision = maker.select_music(1)
    print(f"✅ Music: {music_decision.provider_name} (${music_decision.cost_usd:.4f})")
    assert music_decision.provider_name, "No music provider selected"
    
    # Cost optimization verification
    total_cost = tts_decision.cost_usd + img_decision.cost_usd + music_decision.cost_usd
    print(f"✅ Cost per video: ${total_cost:.4f} (target: < $0.25)")
    print(f"✅ Cost for 30 videos: ${total_cost * 30:.2f}")
    
    return total_cost < 0.25  # Should be very cheap with free providers


def test_config_fields():
    """Test that new config fields exist."""
    print("\n=== TEST 3: Config Fields ===")
    
    # Playbook fields
    assert hasattr(settings, 'playbook_validation_enabled'), "Missing playbook_validation_enabled"
    print(f"✅ playbook_validation_enabled = {settings.playbook_validation_enabled}")
    
    # Provider selector fields
    assert hasattr(settings, 'provider_selector_enabled'), "Missing provider_selector_enabled"
    print(f"✅ provider_selector_enabled = {settings.provider_selector_enabled}")
    
    # Future feature flags (should be False for now)
    assert hasattr(settings, 'avatar_pipeline_enabled'), "Missing avatar_pipeline_enabled"
    print(f"✅ avatar_pipeline_enabled = {settings.avatar_pipeline_enabled}")
    
    assert hasattr(settings, 'clipfactory_enabled'), "Missing clipfactory_enabled"
    print(f"✅ clipfactory_enabled = {settings.clipfactory_enabled}")
    
    return True


def main():
    """Run all smoke tests."""
    print("\n" + "="*60)
    print("PHASE 1 SMOKE TEST: Playbook System + Provider Selector")
    print("="*60)
    
    results = []
    
    # Test 1: Playbook Loading
    try:
        results.append(("Playbook Loading", test_playbook_loading()))
    except Exception as e:
        print(f"❌ Playbook test failed: {e}")
        results.append(("Playbook Loading", False))
    
    # Test 2: Provider Selection
    try:
        results.append(("Provider Selection", test_provider_selection()))
    except Exception as e:
        print(f"❌ Provider selection test failed: {e}")
        results.append(("Provider Selection", False))
    
    # Test 3: Config Fields
    try:
        results.append(("Config Fields", test_config_fields()))
    except Exception as e:
        print(f"❌ Config test failed: {e}")
        results.append(("Config Fields", False))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 Phase 1 Smoke Test PASSED!")
        return 0
    else:
        print("\n⚠️  Some tests failed. Check output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
