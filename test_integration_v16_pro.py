#!/usr/bin/env python3
"""
Test de Integración V16 PRO
Verifica que todos los módulos de los 5 repositorios estén integrados
"""

import sys
from pathlib import Path

def test_imports():
    """Test 1: Verificar imports de todos los módulos"""
    print("\n🧪 TEST 1: Verificando imports...")
    
    tests = [
        ("CostTracker", "cost_tracker", "CostTracker"),
        ("QualityGate", "lib.scoring", "QualityGate"),
        ("PipelineLoader", "lib.pipeline_loader", "load_pipeline"),
        ("ABAssetManager", "pipeline.asset_manager_v2", "ABAssetManager"),
        ("CharacterTracker", "core.consistency.character_tracker", "CharacterTracker"),
        ("RenderBackend", "core.render_backend", "RenderBackend"),
        ("ContentTranslationEngine", "skills.translation.content_translation_engine", "ContentTranslationEngine"),
        ("AudioPostProcessor", "tools.audio.post_process", "AudioPostProcessor"),
    ]
    
    passed = 0
    failed = 0
    
    for name, module, symbol in tests:
        try:
            exec(f"from {module} import {symbol}")
            print(f"  ✅ {name}: OK")
            passed += 1
        except ImportError as e:
            print(f"  ❌ {name}: {e}")
            failed += 1
    
    return passed, failed

def test_file_structure():
    """Test 2: Verificar estructura de archivos"""
    print("\n🧪 TEST 2: Verificando estructura de archivos...")
    
    required_files = [
        "cost_tracker.py",
        "config_v16_pro.yaml",
        "requirements_v16_pro.txt",
        "INTEGRATION_STATUS_V16_PRO.md",
        "lib/pipeline_loader.py",
        "lib/scoring.py",
        "skills/translation/content_translation_engine.py",
        "pipeline/asset_manager_v2.py",
        "core/consistency/character_tracker.py",
        "core/render_backend.py",
        "tools/audio/post_process.py",
        "remotion-composer/src/parsers/eml.ts",
        "remotion-composer/src/templates/UniversalCommercial.tsx",
    ]
    
    passed = 0
    failed = 0
    
    for file_path in required_files:
        full_path = Path(file_path)
        if full_path.exists():
            print(f"  ✅ {file_path}: Existe")
            passed += 1
        else:
            print(f"  ❌ {file_path}: No encontrado")
            failed += 1
    
    return passed, failed

def test_configuration():
    """Test 3: Verificar configuración YAML"""
    print("\n🧪 TEST 3: Verificando configuración...")
    
    try:
        import yaml
        
        with open("config_v16_pro.yaml", "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        
        checks = [
            ("version", config.get("version")),
            ("features.openmontage_tools", config.get("features", {}).get("openmontage_tools")),
            ("features.shortgpt_eml", config.get("features", {}).get("shortgpt_eml")),
            ("features.saard00_ab_testing", config.get("features", {}).get("saard00_ab_testing")),
            ("features.vimax_consistency", config.get("features", {}).get("vimax_consistency")),
            ("features.autocm_themes", config.get("features", {}).get("autocm_themes")),
            ("themes.available", config.get("themes", {}).get("available")),
            ("ab_testing.enabled", config.get("ab_testing", {}).get("enabled")),
            ("translation.enabled", config.get("translation", {}).get("enabled")),
        ]
        
        passed = 0
        failed = 0
        
        for check_name, value in checks:
            if value is not None and value != []:
                print(f"  ✅ {check_name}: {value}")
                passed += 1
            else:
                print(f"  ❌ {check_name}: No configurado")
                failed += 1
        
        return passed, failed
        
    except Exception as e:
        print(f"  ❌ Error leyendo configuración: {e}")
        return 0, 9

def test_state_manager_integration():
    """Test 4: Verificar StateManager tiene integraciones"""
    print("\n🧪 TEST 4: Verificando StateManager...")
    
    try:
        with open("state_manager.py", "r", encoding="utf-8") as f:
            content = f.read()
        
        checks = [
            ("CostTracker import", "from cost_tracker import CostTracker" in content or "from tools.cost_tracker import CostTracker" in content),
            ("QualityGate import", "from lib.scoring import QualityGate" in content),
            ("quality_gate attribute", "self.quality_gate" in content),
            ("initialize_cost_tracker", "def initialize_cost_tracker" in content),
        ]
        
        passed = 0
        failed = 0
        
        for check_name, exists in checks:
            if exists:
                print(f"  ✅ {check_name}: Integrado")
                passed += 1
            else:
                print(f"  ❌ {check_name}: No encontrado")
                failed += 1
        
        return passed, failed
        
    except Exception as e:
        print(f"  ❌ Error verificando StateManager: {e}")
        return 0, 4

def test_video_factory_integration():
    """Test 5: Verificar video_factory.py tiene cost tracker"""
    print("\n🧪 TEST 5: Verificando video_factory.py...")
    
    try:
        with open("video_factory.py", "r", encoding="utf-8") as f:
            content = f.read()
        
        checks = [
            ("StateManager import", "from state_manager import StateManager" in content),
            ("initialize_cost_tracker", "initialize_cost_tracker" in content),
            ("Cost tracking comment", "V16 PRO" in content or "cost tracking" in content.lower()),
        ]
        
        passed = 0
        failed = 0
        
        for check_name, exists in checks:
            if exists:
                print(f"  ✅ {check_name}: Integrado")
                passed += 1
            else:
                print(f"  ❌ {check_name}: No encontrado")
                failed += 1
        
        return passed, failed
        
    except Exception as e:
        print(f"  ❌ Error verificando video_factory.py: {e}")
        return 0, 3

def main():
    print("=" * 60)
    print("🚀 VIDEO FACTORY V16 PRO - TEST DE INTEGRACIÓN")
    print("=" * 60)
    
    total_passed = 0
    total_failed = 0
    
    # Ejecutar tests
    tests = [
        test_imports,
        test_file_structure,
        test_configuration,
        test_state_manager_integration,
        test_video_factory_integration,
    ]
    
    for test_func in tests:
        try:
            p, f = test_func()
            total_passed += p
            total_failed += f
        except Exception as e:
            print(f"\n❌ Error en {test_func.__name__}: {e}")
            total_failed += 1
    
    # Resumen
    print("\n" + "=" * 60)
    print("📊 RESUMEN DE TESTS")
    print("=" * 60)
    print(f"✅ Pasados: {total_passed}")
    print(f"❌ Fallidos: {total_failed}")
    print(f"📈 Porcentaje: {(total_passed / (total_passed + total_failed) * 100):.1f}%")
    
    if total_failed == 0:
        print("\n🎉 ¡INTEGRACIÓN V16 PRO COMPLETADA CON ÉXITO!")
        return 0
    else:
        print(f"\n⚠️  {total_failed} tests fallaron. Revisar integración.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
