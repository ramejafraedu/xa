#!/usr/bin/env bash
# Install WhisperX + CPU torch into the project venv on the VPS.
# Usage:  bash scripts/vps_install_whisperx.sh
# Idempotent: re-running it is safe, packages already present are skipped.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/video_factory}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"

if [ ! -d "$VENV_DIR" ]; then
    echo "venv not found at $VENV_DIR — aborting."
    exit 1
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "[whisperx] python: $(python --version)"
echo "[whisperx] pip:    $(pip --version)"

pip install --upgrade pip wheel setuptools

if python -c "import torch" 2>/dev/null; then
    echo "[whisperx] torch already installed: $(python -c 'import torch; print(torch.__version__)')"
else
    echo "[whisperx] installing torch CPU wheels (this can take a few minutes)"
    pip install --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.3.1" "torchaudio==2.3.1"
fi

if python -c "import faster_whisper" 2>/dev/null; then
    echo "[whisperx] faster-whisper already installed: $(python -c 'import faster_whisper; print(faster_whisper.__version__)')"
else
    echo "[whisperx] installing faster-whisper + ctranslate2"
    # faster-whisper 1.0.3 gives word-level timestamps with a stable API and
    # does not require the broken whisperx VAD S3 bucket. We deliberately do
    # NOT install the full `whisperx` package to keep the dependency graph
    # small (no pyannote.audio, no torch-lightning, etc.).
    pip install "ctranslate2==4.4.0" "faster-whisper==1.0.3"
fi

# Pre-download the base faster-whisper weights so transcription works on
# hosts where the default huggingface redirect breaks for some IPs.
echo "[whisperx] pre-caching Systran/faster-whisper-base"
python - <<'PY'
import os
from huggingface_hub import snapshot_download
cache_dir = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
local = snapshot_download(
    repo_id="Systran/faster-whisper-base",
    cache_dir=cache_dir,
)
print("model cached at:", local)
PY

if command -v ffmpeg >/dev/null 2>&1; then
    echo "[whisperx] ffmpeg: $(ffmpeg -version | head -n1)"
else
    echo "[whisperx] WARNING: ffmpeg not found on PATH. WhisperX needs ffmpeg to decode audio."
fi

echo
echo "[whisperx] smoke test"
python - <<'PY'
import torch, faster_whisper
print("torch:", torch.__version__, "cuda?", torch.cuda.is_available())
print("faster_whisper:", faster_whisper.__version__)
PY

echo "[whisperx] done"
