#!/usr/bin/env bash
# One-shot: fresh venv + pip + Remotion npm on VPS (run from ~/video_factory or pass PROJECT_DIR).
set -euo pipefail
PROJECT_DIR="${1:-$HOME/video_factory}"
cd "$PROJECT_DIR"
mkdir -p remotion-composer/public/workspace data/logs temp output nichos

if command -v python3.11 >/dev/null 2>&1; then PY=python3.11; else PY=python3; fi
echo "[vps_reinstall] using $PY ($($PY --version))"

rm -rf venv
"$PY" -m venv venv
# shellcheck source=/dev/null
source venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -r requirements.txt

if [[ -d remotion-composer ]]; then
  cd remotion-composer
  if [[ -f package-lock.json ]]; then
    npm ci || npm install
  else
    npm install
  fi
  cd "$PROJECT_DIR"
fi

echo "[vps_reinstall] OK — Python venv + deps + remotion-composer npm"
