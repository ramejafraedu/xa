#!/usr/bin/env bash
# Remote bootstrap: verify deps + Vertex AI + Google TTS + fast smoke test.
set -u

cd /home/xavierfranmen/video_factory
if [ ! -d .venv ]; then
    echo "[BOOTSTRAP] .venv missing; creating..."
    python3 -m venv .venv
fi
. .venv/bin/activate

echo "[BOOTSTRAP] Python: $(python --version)"

pip install --disable-pip-version-check --quiet --upgrade pip >/dev/null 2>&1 || true

REQ_PKGS=(
    "psutil"
    "fastapi"
    "uvicorn[standard]"
    "python-multipart"
    "sse-starlette"
    "loguru"
    "python-dotenv"
    "pydantic-settings"
    "pyyaml"
    "httpx"
    "requests"
    "google-genai"
    "google-cloud-aiplatform"
    "google-cloud-texttospeech"
)
echo "[BOOTSTRAP] Ensuring core Python deps are installed..."
pip install --disable-pip-version-check --quiet "${REQ_PKGS[@]}" 2>&1 | tail -5

echo "[BOOTSTRAP] Dep import check:"
python - <<'PY'
import importlib.util
mods = [
    "psutil",
    "fastapi",
    "uvicorn",
    "multipart",
    "sse_starlette",
    "loguru",
    "dotenv",
    "pydantic_settings",
    "yaml",
    "httpx",
    "requests",
    "google.genai",
    "google.cloud.aiplatform",
    "google.cloud.texttospeech",
]
for m in mods:
    ok = importlib.util.find_spec(m) is not None
    print(f"  {m}: {'OK' if ok else 'MISSING'}")
PY

echo ""
echo "[BOOTSTRAP] Settings summary:"
python - <<'PY'
from config import settings
print("  use_vertex_ai       =", settings.use_vertex_ai)
print("  vertex_project_id   =", settings.vertex_project_id)
print("  vertex_location     =", settings.vertex_location)
print("  use_google_tts      =", settings.use_google_tts)
print("  gemini_keys_count   =", len(settings.get_gemini_keys()))
print("  google_tts_enabled  =", settings.google_tts_effective_enabled())
import os
print("  GOOGLE_APPLICATION_CREDENTIALS =", os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
PY

echo ""
echo "[BOOTSTRAP] Vertex AI smoke (list models call):"
python - <<'PY'
import os, sys
try:
    from google import genai
    from google.genai import types
    client = genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "global"),
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=["Say 'vertex-ok' only."],
    )
    txt = (getattr(resp, "text", "") or "").strip()
    print("  Vertex response:", txt[:60])
except Exception as e:
    print("  Vertex smoke FAILED:", repr(e)[:300])
    sys.exit(2)
PY

echo ""
echo "[BOOTSTRAP] Google TTS smoke (1 sentence):"
python - <<'PY'
import os, sys
from pathlib import Path
out = Path("/tmp/_vf_tts_smoke.mp3")
try:
    from google.cloud import texttospeech
    client = texttospeech.TextToSpeechClient()
    response = client.synthesize_speech(
        request={
            "input": texttospeech.SynthesisInput(text="Prueba Google TTS servidor."),
            "voice": texttospeech.VoiceSelectionParams(language_code="es-US", name="es-US-Neural2-A"),
            "audio_config": texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3),
        },
        timeout=30,
    )
    out.write_bytes(response.audio_content or b"")
    print(f"  TTS ok bytes={out.stat().st_size}")
except Exception as e:
    print("  Google TTS smoke FAILED:", repr(e)[:300])
    sys.exit(3)
PY
echo ""
echo "[BOOTSTRAP] DONE"
