import os
from config import settings
from pipeline.composition_master import compose_video_clips

def test_integration():
    guion = (
        "El espacio exterior está en completo silencio. Nunca escucharás una explosión ahí. "
        "Las ondas sonoras necesitan un medio como el aire o el agua para viajar, "
        "y el espacio es un vacío enorme. A diferencia de las películas, donde las naves zumban "
        "y los láseres hacen un ruido enorme, la realidad es mucho más silenciosa."
    )
    tema = "El silencio del espacio exterior"
    nicho = "curiosidades"

    # Forzar el uso de llaves de la configuración existente o dar una falsa temporal (solo para testear hooks)
    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        os.environ["GEMINI_API_KEY"] = "fake-key-for-test"
    
    print("Iniciando Compose Video Clips...")
    try:
        clips_a, clips_b = compose_video_clips(
            guion=guion,
            tema=tema,
            nicho=nicho,
            num_clips=3, # Pocos para que sea rapido
            job_id="test_om_001"
        )
        print("====== RESULTADO DE LA COMPOSICIÓN ======")
        print(f"Total Clips A seleccionados: {len(clips_a)}")
        print(f"Total Clips B seleccionados: {len(clips_b)}")
        for idx, c in enumerate(clips_a):
            print(f" - Clip {idx+1} [A] {c.get('provider', 'N/A') if c else 'None'} -> {c.get('keyword_used') if c else 'None'}")
            
        print("\n¡Prueba terminada con éxito! No hubieron bloqueos de sintaxis ni importación.")
    except Exception as e:
        print(f"\nERROR durante la prueba: {e}")

if __name__ == "__main__":
    test_integration()
