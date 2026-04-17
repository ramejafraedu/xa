#!/usr/bin/env bash
# Quick tail/status for the most recent smoke run log.
cd /home/xavierfranmen/video_factory
LOG=$(ls -t workspace/logs/smoke_run_*.log 2>/dev/null | head -1)
echo "LOG=$LOG"
echo "---procs---"
ps -o pid,etime,cmd -C python 2>/dev/null | grep -E "video_factory|smoke" || echo "no python running"
echo "---stats---"
grep -aE 'ASS from text|Expanding|Script too short|Script too long|_script_word_count|Narration|audio_dur|Video rendered|Pipeline Result|✅ .*rendered|COMPLETED_PUBLISH|ERROR' "$LOG" | tail -40
echo "---tail log---"
tail -n 25 "$LOG"
