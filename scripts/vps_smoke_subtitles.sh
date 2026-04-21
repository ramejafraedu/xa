#!/usr/bin/env bash
# Smoke test: generate ASS subtitles from a short synthesized audio and
# verify word-level timings are produced (via WhisperX or the text fallback).

set -euo pipefail
cd "$HOME/video_factory"
source venv/bin/activate

OUT=$(mktemp -d)
AUDIO="$OUT/sample.wav"
ASS="$OUT/sample.ass"

# 3 seconds of a 440 Hz sine tone — enough for whisperx to load pipelines
# without actually needing a working transcription (it will fail silently
# and the text-proportional fallback should take over).
ffmpeg -hide_banner -loglevel error -f lavfi -i "sine=frequency=440:duration=3" \
    -ar 16000 -ac 1 "$AUDIO"

python - <<PY
from pathlib import Path
from pipeline.subtitles_whisperx import generate_subtitles_with_fallback

text = "Este es un ejemplo corto para probar la alineación de subtítulos."
events = generate_subtitles_with_fallback(
    Path("$AUDIO"),
    text,
    audio_duration=3.0,
    ass_path=Path("$ASS"),
    language="es",
)
print(f"events={events}")
assert events > 0, "expected at least one ASS event"
body = Path("$ASS").read_text(encoding="utf-8")
print("---- ASS header ----")
print("\n".join(body.splitlines()[:10]))
print("---- sample dialogue ----")
for line in body.splitlines():
    if line.startswith("Dialogue:"):
        print(line)
        break
PY

echo "[smoke] OK — ASS generated at $ASS"
rm -rf "$OUT"
