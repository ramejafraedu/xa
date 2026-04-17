#!/usr/bin/env bash
# Launch a smoke run of video_factory in background and report PID.
cd /home/xavierfranmen/video_factory
. .venv/bin/activate
mkdir -p workspace/logs workspace/output workspace/temp
: > workspace/logs/smoke_run.log
nohup python video_factory.py curiosidades --duration-mins 0.6 \
    >workspace/logs/smoke_run.log 2>&1 &
PID=$!
echo "SMOKE_PID=$PID"
sleep 2
if kill -0 "$PID" 2>/dev/null; then
    echo "SMOKE_RUNNING=yes"
else
    echo "SMOKE_RUNNING=no"
    echo "--- log tail ---"
    tail -n 60 workspace/logs/smoke_run.log
fi
