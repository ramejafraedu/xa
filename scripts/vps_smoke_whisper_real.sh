#!/usr/bin/env bash
# Extract speech track from an existing rendered video and run the
# faster-whisper subtitle generator on it. Verifies the real end-to-end path
# (no synthetic tone).
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

VIDEO="$(ls -1t ./workspace/output/*.mp4 2>/dev/null | head -n1)"
if [ -z "${VIDEO:-}" ]; then
    VIDEO="$(ls -1t ./historia_*.mp4 2>/dev/null | head -n1)"
fi
if [ -z "${VIDEO:-}" ]; then
    echo "no candidate video found"; exit 1
fi
echo "[smoke] using video: $VIDEO"

OUT=$(mktemp -d)
AUDIO="$OUT/speech.wav"
ASS="$OUT/speech.ass"

ffmpeg -hide_banner -loglevel error -i "$VIDEO" -vn -ac 1 -ar 16000 "$AUDIO"
ls -lh "$AUDIO"

python - <<PY
from pathlib import Path
from pipeline.subtitles_whisperx import generate_subtitles_with_fallback

events = generate_subtitles_with_fallback(
    Path("$AUDIO"),
    "",
    audio_duration=float(__import__('subprocess').check_output(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1","$AUDIO"]
    ).decode().strip()),
    ass_path=Path("$ASS"),
    language="es",
)
print(f"events={events}")
body = Path("$ASS").read_text(encoding="utf-8")
dialogues = [l for l in body.splitlines() if l.startswith("Dialogue:")]
print(f"dialogues={len(dialogues)}")
for d in dialogues[:5]:
    print(d[:200])
PY

rm -rf "$OUT"
echo "[smoke-real] OK"
