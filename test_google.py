import os
from pathlib import Path

from google import genai
from dotenv import load_dotenv

_THIS_DIR = Path(__file__).resolve().parent
load_dotenv(_THIS_DIR / ".env", override=False)

# Configuración de Vertex/ADC
PROJECT_ID = os.getenv("VERTEX_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT") or ""
LOCATION = os.getenv("VERTEX_LOCATION", "global")
USE_VERTEX_AI = (os.getenv("USE_VERTEX_AI", "false") or "").strip().lower() in {"1", "true", "yes", "on"}
ADC_PATH = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
ADC_AVAILABLE = bool(ADC_PATH and Path(ADC_PATH).exists())
FORCE_VERTEX = USE_VERTEX_AI or ADC_AVAILABLE or bool(PROJECT_ID)


def _model_candidates() -> list[str]:
    configured = [
        m.strip() for m in (os.getenv("GEMINI_CHAT_MODELS") or "").split(",") if m.strip()
    ]
    preferred = (
        os.getenv("GEMINI_TEXT_MODEL")
        or os.getenv("PRIMARY_LLM")
        or "gemini-3.1-pro-preview"
    ).strip()

    models = [preferred] + configured
    deduped: list[str] = []
    seen: set[str] = set()
    for model in models:
        lowered = model.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(model)

    return deduped or [
        "gemini-3.1-pro-preview",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash-001",
    ]


MODEL_CANDIDATES = _model_candidates()


def build_client() -> tuple[genai.Client, str]:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key), "api_key"
    if PROJECT_ID:
        return genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION), "vertex_adc"
    return genai.Client(vertexai=True, location=LOCATION), "vertex_adc"


def build_fallback_clients() -> list[tuple[genai.Client, str]]:
    clients: list[tuple[genai.Client, str]] = []
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    # Prefer Vertex ADC when enabled/configured in env.
    if FORCE_VERTEX:
        if PROJECT_ID:
            clients.append((genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION), "vertex_adc"))
        else:
            clients.append((genai.Client(vertexai=True, location=LOCATION), "vertex_adc"))

    # API key remains as optional fallback path.
    if api_key:
        clients.append((genai.Client(api_key=api_key), "api_key"))

    # If Vertex is not explicitly enabled, still include it as a last-resort fallback.
    if not FORCE_VERTEX:
        if PROJECT_ID:
            clients.append((genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION), "vertex_adc"))
        else:
            clients.append((genai.Client(vertexai=True, location=LOCATION), "vertex_adc"))

    return clients


def is_quota_error(error: Exception) -> bool:
    msg = str(error).lower()
    return "resource_exhausted" in msg or "quota" in msg or "429" in msg


def is_model_unavailable_error(error: Exception) -> bool:
    msg = str(error).lower()
    return (
        "not_found" in msg
        or "404" in msg
        or "model is not found" in msg
        or "publisher model" in msg
        or "invalid model" in msg
    )


prompt = "Dame un hook explosivo de 3 segundos para un TikTok sobre finanzas personales."

try:
    response_text = ""
    last_error = None
    attempted_modes: list[str] = []

    for client, auth_mode in build_fallback_clients():
        attempted_modes.append(auth_mode)
        print(f"--- 🧠 Probando Gemini ({auth_mode}) ---")

        mode_had_quota_error = False
        for model_name in MODEL_CANDIDATES:
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                response_text = (response.text or "").strip()
                if response_text:
                    print(f"✅ ACCESO CONCEDIDO ({model_name}): {response_text}")
                    break
            except Exception as model_error:
                last_error = model_error
                if is_model_unavailable_error(model_error):
                    print(f"⚠️ Modelo no disponible ({model_name}). Probando fallback...")
                    continue
                if auth_mode == "api_key" and is_quota_error(model_error):
                    mode_had_quota_error = True
                    continue
                # For non-quota failures, break model loop and try next auth mode.
                break

        if response_text:
            break
        if auth_mode == "api_key" and mode_had_quota_error:
            print("⚠️ API key sin cuota. Intentando Vertex ADC...")

    if not response_text:
        if last_error:
            raise last_error
        raise RuntimeError(
            f"No se obtuvo texto de respuesta en modos: {', '.join(attempted_modes)}"
        )

    print("\n--- 🚀 Todo listo para el render final ---")

except Exception as e:
    msg = str(e)
    if "default credentials" in msg.lower() or "application default credentials" in msg.lower():
        print("❌ ERROR TÉCNICO: faltan credenciales ADC para Vertex AI")
        print("👉 Solución rápida:")
        print("   1) exporta GOOGLE_APPLICATION_CREDENTIALS con el JSON de cuenta de servicio")
        print("   2) o usa: gcloud auth application-default login")
        print("   3) verifica VERTEX_PROJECT_ID y vuelve a ejecutar")
    elif "resource_exhausted" in msg.lower() or "quota" in msg.lower() or "429" in msg.lower():
        print("❌ ERROR TÉCNICO: tu API key de Gemini no tiene cuota disponible")
        print("👉 Opciones:")
        print("   1) habilitar billing/cuota en Gemini API")
        print("   2) usar Vertex ADC (gcloud auth application-default login)")
        print("   3) probar más tarde cuando se resetee cuota")
    else:
        print(f"❌ ERROR TÉCNICO: {e}")