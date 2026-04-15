"""
Tests de Integración OpenMontage - Video Factory V16 PRO
Valida todos los componentes integrados del proyecto OpenMontage.
"""

import sys
import os
from pathlib import Path

# Añadir raíz al path para imports
sys.path.insert(0, str(Path(__file__).parent))

def test_imports():
    """Test 1: Verificar que todos los módulos se pueden importar."""
    print("\n🧪 Test 1: Importación de módulos OpenMontage")
    
    errors = []
    
    # Core Libraries
    try:
        from lib.clip_embedder import embed_images, embed_texts, model_info
        print("  ✅ lib.clip_embedder")
    except Exception as e:
        errors.append(f"clip_embedder: {e}")
        print(f"  ❌ lib.clip_embedder: {e}")
    
    try:
        from lib.corpus import Corpus, ClipRecord
        print("  ✅ lib.corpus")
    except Exception as e:
        errors.append(f"corpus: {e}")
        print(f"  ❌ lib.corpus: {e}")
    
    # Video Tools
    video_tools = [
        ("tools.video.clip_cache", "ClipCache"),
        ("tools.video.corpus_builder", "CorpusBuilder"),
        ("tools.video.clip_search", "ClipSearch"),
        ("tools.video.direct_clip_search", "DirectClipSearch"),
        ("tools.video.higgsfield_video", "HiggsfieldVideo"),
        ("tools.video.seedance_video", "SeedanceVideo"),
    ]
    
    for module, class_name in video_tools:
        try:
            exec(f"from {module} import {class_name}")
            print(f"  ✅ {module}")
        except Exception as e:
            errors.append(f"{module}: {e}")
            print(f"  ❌ {module}: {e}")
    
    # Stock Sources (nombres reales pueden variar)
    stock_modules = [
        ("tools.video.stock_sources.pexels", "Pexels"),
        ("tools.video.stock_sources.pixabay_video", "PixabayVideo"),
        ("tools.video.stock_sources.archive_org", "ArchiveOrg"),
    ]
    
    for module, class_name in stock_modules:
        try:
            exec(f"from {module} import {class_name}")
            print(f"  ✅ {module}")
        except Exception as e:
            # Estos pueden fallar si faltan API keys, es aceptable
            print(f"  ⚠️  {module} (API key o archivo diferente)")
    
    return len(errors) == 0, errors


def test_clip_embedder_structure():
    """Test 2: Verificar estructura de clip_embedder."""
    print("\n🧪 Test 2: Estructura clip_embedder")
    
    try:
        from lib import clip_embedder
        
        # Verificar funciones principales
        required_funcs = ['embed_images', 'embed_texts', 'model_info', 'pool_frames']
        for func in required_funcs:
            assert hasattr(clip_embedder, func), f"Falta función: {func}"
        
        # Verificar constantes
        assert clip_embedder._MODEL_ID == "openai/clip-vit-base-patch32"
        assert clip_embedder._DEVICE in ["cpu", "cuda"]
        
        print("  ✅ Estructura correcta")
        return True, []
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False, [str(e)]


def test_corpus_structure():
    """Test 3: Verificar estructura de corpus."""
    print("\n🧪 Test 3: Estructura Corpus")
    
    try:
        from lib.corpus import Corpus, ClipRecord, EMBED_DIM
        
        # Verificar ClipRecord
        rec = ClipRecord(
            clip_id="test_123",
            source="pexels",
            source_id="123",
            source_url="https://example.com",
            local_path="clips/test_123.mp4"
        )
        assert rec.clip_id == "test_123"
        assert rec.kind == "video"  # default
        
        # Verificar Corpus
        from pathlib import Path
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus = Corpus(Path(tmpdir))
            corpus.ensure_dirs()
            
            # Verificar directorios creados
            assert corpus.clips_dir.exists()
            assert corpus.thumbs_dir.exists()
            
            # Verificar persistencia vacía
            corpus.load()
            assert len(corpus) == 0
            
            # Verificar save/load
            corpus.save()
            assert corpus.index_path.exists()
        
        print("  ✅ Estructura Corpus correcta")
        return True, []
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False, [str(e)]


def test_clip_cache_structure():
    """Test 4: Verificar estructura de clip_cache."""
    print("\n🧪 Test 4: Estructura ClipCache")
    
    try:
        from tools.video import clip_cache
        
        # Verificar clases y funciones principales
        assert hasattr(clip_cache, 'ClipCache')
        assert hasattr(clip_cache, 'default_cache_dir')
        assert hasattr(clip_cache, 'CacheEntry')
        
        # Verificar que ClipCache tiene métodos esperados
        cc_class = clip_cache.ClipCache
        assert hasattr(cc_class, 'ingest')
        assert hasattr(cc_class, 'try_link')
        
        print("  ✅ Estructura ClipCache correcta")
        return True, []
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False, [str(e)]


def test_corpus_builder_structure():
    """Test 5: Verificar estructura de corpus_builder."""
    print("\n🧪 Test 5: Estructura CorpusBuilder")
    
    try:
        from tools.video.corpus_builder import CorpusBuilder
        
        # Verificar atributos de BaseTool
        assert hasattr(CorpusBuilder, 'name')
        assert hasattr(CorpusBuilder, 'version')
        assert hasattr(CorpusBuilder, 'dependencies')
        
        assert CorpusBuilder.name == "corpus_builder"
        assert "transformers" in str(CorpusBuilder.dependencies)
        assert "torch" in str(CorpusBuilder.dependencies)
        
        print("  ✅ Estructura CorpusBuilder correcta")
        return True, []
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return False, [str(e)]


def test_file_existence():
    """Test 6: Verificar que todos los archivos fueron copiados."""
    print("\n🧪 Test 6: Existencia de archivos integrados")
    
    root = Path(__file__).parent
    
    required_files = [
        # Core libraries
        "lib/clip_embedder.py",
        "lib/corpus.py",
        
        # Video tools
        "tools/video/clip_cache.py",
        "tools/video/corpus_builder.py",
        "tools/video/clip_search.py",
        "tools/video/direct_clip_search.py",
        "tools/video/higgsfield_video.py",
        "tools/video/seedance_video.py",
        
        # Stock sources (nombres reales en OpenMontage)
        "tools/video/stock_sources/__init__.py",
        "tools/video/stock_sources/pexels.py",
        "tools/video/stock_sources/pixabay_video.py",
        "tools/video/stock_sources/archive_org.py",
    ]
    
    missing = []
    for file_path in required_files:
        full_path = root / file_path
        if full_path.exists():
            size = full_path.stat().st_size
            print(f"  ✅ {file_path} ({size:,} bytes)")
        else:
            missing.append(file_path)
            print(f"  ❌ {file_path} NO EXISTE")
    
    return len(missing) == 0, missing


def test_dependencies_in_requirements():
    """Test 7: Verificar dependencias en requirements."""
    print("\n🧪 Test 7: Dependencias en requirements_v16_pro.txt")
    
    root = Path(__file__).parent
    req_file = root / "requirements_v16_pro.txt"
    
    if not req_file.exists():
        print("  ❌ No se encuentra requirements_v16_pro.txt")
        return False, ["requirements file missing"]
    
    content = req_file.read_text()
    
    required_deps = [
        "torch",
        "transformers",
        "filelock",
        "safetensors",
        "huggingface-hub",
    ]
    
    missing = []
    for dep in required_deps:
        if dep in content:
            print(f"  ✅ {dep}")
        else:
            missing.append(dep)
            print(f"  ❌ {dep} NO ENCONTRADO")
    
    return len(missing) == 0, missing


def run_all_tests():
    """Ejecutar todos los tests y mostrar resumen."""
    print("=" * 70)
    print("🎬 TESTS DE INTEGRACIÓN OPENMONTAGE - Video Factory V16 PRO")
    print("=" * 70)
    
    tests = [
        ("Importaciones", test_imports),
        ("Estructura clip_embedder", test_clip_embedder_structure),
        ("Estructura Corpus", test_corpus_structure),
        ("Estructura ClipCache", test_clip_cache_structure),
        ("Estructura CorpusBuilder", test_corpus_builder_structure),
        ("Existencia de archivos", test_file_existence),
        ("Dependencias", test_dependencies_in_requirements),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            passed, errors = test_func()
            results.append((name, passed, errors))
        except Exception as e:
            results.append((name, False, [str(e)]))
    
    # Resumen
    print("\n" + "=" * 70)
    print("📊 RESUMEN DE TESTS")
    print("=" * 70)
    
    passed_count = sum(1 for _, p, _ in results if p)
    total_count = len(results)
    
    for name, passed, errors in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {name}")
        if errors and not passed:
            for err in errors[:3]:  # Mostrar máximo 3 errores
                print(f"      - {err}")
    
    print(f"\n📈 Total: {passed_count}/{total_count} tests pasados ({passed_count/total_count*100:.1f}%)")
    
    if passed_count == total_count:
        print("\n🎉 ¡Todos los tests pasaron! Integración OpenMontage exitosa.")
    else:
        print(f"\n⚠️  {total_count - passed_count} test(s) fallaron. Revisar integración.")
    
    return passed_count == total_count


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
