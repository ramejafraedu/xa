#!/usr/bin/env bash
# Print whether whisperx + torch import in the project venv.
set -e
cd "$HOME/video_factory"
source venv/bin/activate
python - <<'PY'
import importlib.util as u
print("whisperx_spec=", bool(u.find_spec("whisperx")))
print("torch_spec=", bool(u.find_spec("torch")))
try:
    import torch
    print("torch_version=", torch.__version__)
    print("torch_cuda=", torch.cuda.is_available())
except Exception as e:
    print("torch_import_error:", e)
try:
    import whisperx
    print("whisperx_ok=True")
except Exception as e:
    print("whisperx_import_error:", e)
PY
