"""Proportional ASS timing from plain text (no WhisperX)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.subtitles import generate_timed_ass_from_text


def test_proportional_ass_fills_audio_window(tmp_path: Path):
    ass = tmp_path / "t.ass"
    text = "UNO DOS TREINTA"  # TREINTA longer → more time budget
    n = generate_timed_ass_from_text(text, 10.0, ass)
    assert n >= 1
    body = ass.read_text(encoding="utf-8")
    assert "Dialogue:" in body
    assert "TREINTA" in body
