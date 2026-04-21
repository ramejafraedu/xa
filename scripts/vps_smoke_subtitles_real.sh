#!/usr/bin/env bash
# Smoke test with real Spanish TTS to exercise faster-whisper word-level timings.
set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

OUT=$(mktemp -d)
AUDIO="$OUT/speech.wav"
ASS="$OUT/speech.ass"

python - <<PY
import asyncio, edge_tts
text = ("Este es un ejemplo corto en español para probar la alineación "
        "de subtítulos palabra por palabra con faster whisper.")
async def run():
    c = edge_tts.Communicate(text, "es-ES-AlvaroNeural")
    await c.save("$AUDIO")
asyncio.run(run())
print("tts_ok")
PY

python - <<PY
from pathlib import Path
from pipeline.subtitles_whisperx import generate_subtitles_with_fallback

text = ("Este es un ejemplo corto en español para probar la alineación "
        "de subtítulos palabra por palabra con faster whisper.")
events = generate_subtitles_with_fallback(
    Path("$AUDIO"),
    text,
    audio_duration=8.0,
    ass_path=Path("$ASS"),
    language="es",
)
print(f"events={events}")
body = Path("$ASS").read_text(encoding="utf-8")
import re
dialogues = [l for l in body.splitlines() if l.startswith("Dialogue:")]
print(f"dialogues={len(dialogues)}")
for d in dialogues[:3]:
    print(d[:160])
PY

rm -rf "$OUT"
echo "[smoke-real] OK"
