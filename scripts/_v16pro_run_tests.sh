#!/usr/bin/env bash
# Runs 2 historia pipeline tests on the server for V16 PRO short-form strategy.
#   Version A: default short (target 40s, 35-45s window)
#   Version B: slightly longer (target ~54s, 50-55s window) via --duration-mins 0.9
set -u

cd "$(dirname "$0")/.." || exit 1
LOG_DIR="logs/v16pro_tests"
mkdir -p "$LOG_DIR"

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
    PYTHON_BIN=".venv/bin/python"
fi

STAMP=$(date +%Y%m%d_%H%M%S)
LOG_A="$LOG_DIR/historia_A_${STAMP}.log"
LOG_B="$LOG_DIR/historia_B_${STAMP}.log"

echo ">>> Cleaning workspace/temp"
rm -rf workspace/temp/* 2>/dev/null || true

echo ">>> [A] Historia short 35-45s (default target 40s)  -> $LOG_A"
nohup "$PYTHON_BIN" video_factory.py historia --v15 \
    --manual-ideas "Caso impactante y verificable elegido por el agente; debe cumplir la estrategia V16 PRO 30-45s con hook fuerte y micro-loop final" \
    > "$LOG_A" 2>&1 &
PID_A=$!
echo "PID_A=$PID_A"

# Space out the two runs a bit to reduce API rate-limit collisions.
sleep 60

echo ">>> [B] Historia mid 50-55s (duration_mins 0.9)  -> $LOG_B"
nohup "$PYTHON_BIN" video_factory.py historia --v15 --duration-mins 0.9 \
    --manual-ideas "Caso impactante DIFERENTE al anterior; un poco mas explicado en el desarrollo, mismo hook fuerte y micro-loop final V16 PRO" \
    > "$LOG_B" 2>&1 &
PID_B=$!
echo "PID_B=$PID_B"

echo "$PID_A"  > "$LOG_DIR/pids_${STAMP}.txt"
echo "$PID_B" >> "$LOG_DIR/pids_${STAMP}.txt"
echo "STAMP=$STAMP"
echo "LOG_A=$LOG_A"
echo "LOG_B=$LOG_B"
