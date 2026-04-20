"""Test de integración completa - OpenMontage + Overlays + Remotion"""

from video_factory import run_pipeline
import sys

def test_full():
    print("=== TEST INTEGRACIÓN COMPLETA ===")
    
    try:
        # Prueba con nicho de curiosidades (buen test de overlays automáticos)
        result = run_pipeline(
            nicho_slug="curiosidades",
            
            dry_run=False
        )
        
        print(f"✅ Pipeline completado")
        print(f"Video generado: {result.get('output_path')}")
        print(f"Schema con overlays: {result.get('schema_path')}")
        print(f"OpenMontage scoring usado: {result.get('used_openmontage', False)}")
        print(f"Overlays automáticos: {result.get('overlays_count', 0)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    success = test_full()
    sys.exit(0 if success else 1)
