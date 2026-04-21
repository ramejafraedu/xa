#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate
python - <<'PY'
import traceback
import whisperx, faster_whisper
print("whisperx:", whisperx.__version__ if hasattr(whisperx, "__version__") else "?")
print("faster_whisper:", faster_whisper.__version__)
try:
    m = whisperx.load_model("base", device="cpu", compute_type="int8", language="es")
    print("loaded ok:", type(m))
except Exception as e:
    print("LOAD FAIL:")
    traceback.print_exc()
PY
