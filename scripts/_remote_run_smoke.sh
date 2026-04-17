#!/usr/bin/env bash
# Launch a smoke run of video_factory in background and report PID.
# Uses default niche curiosidades; supports override: bash _remote_run_smoke.sh historia 1.3
cd /home/xavierfranmen/video_factory
. .venv/bin/activate
mkdir -p workspace/logs workspace/output workspace/temp

NICHE="${1:-curiosidades}"
DURATION="${2:-1.3}"

LOG="workspace/logs/smoke_run_${NICHE}_$(date +%s).log"
: > "$LOG"
nohup python video_factory.py "$NICHE" --duration-mins "$DURATION" \
    >"$LOG" 2>&1 &
PID=$!
echo "SMOKE_PID=$PID"
echo "SMOKE_LOG=$LOG"
sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "SMOKE_RUNNING=yes"
else
    echo "SMOKE_RUNNING=no"
    echo "--- log tail ---"
    tail -n 60 "$LOG"
fi
