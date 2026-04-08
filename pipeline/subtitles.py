"""Subtitles — VTT to ASS word-by-word conversion.

Replaces n8n node: 🎨 ASS Word-by-Word.
Generates karaoke-style word-by-word subtitles.
"""
from __future__ import annotations

import re
from pathlib import Path

from loguru import logger


def vtt_to_ass(vtt_path: Path, ass_path: Path) -> int:
    """Convert VTT subtitles to ASS word-by-word format.

    Returns number of dialogue events created.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial Black,90,"
        "&H00FFFFFF,&H0000D7FF,&H00000000,&H99000000,"
        "-1,0,0,0,100,100,2,0,1,4,2,2,80,80,220,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    try:
        content = vtt_path.read_text(encoding="utf-8")
    except Exception:
        ass_path.write_text(header, encoding="utf-8")
        logger.info("No VTT content, created empty ASS")
        return 0

    if not content.strip() or content.strip() == "WEBVTT":
        ass_path.write_text(header, encoding="utf-8")
        return 0

    blocks = re.split(r"\n\n+", content.strip())
    events = []

    for block in blocks:
        lines = [x for x in block.split("\n") if x.strip()]
        tl = next((x for x in lines if "-->" in x), None)
        if not tl:
            continue

        parts = re.split(r"\s*-->\s*", tl)
        if len(parts) < 2:
            continue

        start_s = _parse_vtt_time(parts[0])
        end_s = _parse_vtt_time(parts[1].split()[0])
        if end_s <= start_s:
            continue

        text_lines = [
            x for x in lines
            if "-->" not in x and not x.startswith("WEBVTT") and not re.match(r"^\d+$", x.strip())
        ]
        text = " ".join(text_lines).strip().upper()
        if not text:
            continue

        words = text.split()
        if not words:
            continue

        chunk_size = 4
        dur = end_s - start_s
        wdur_cs = max(6, int(round((dur / len(words)) * 100)))

        for i in range(0, len(words), chunk_size):
            chunk = words[i : i + chunk_size]
            c_start = start_s + (i * (dur / len(words)))
            c_end = c_start + (len(chunk) * (dur / len(words)))

            base = _subtitle_base_tag()
            line = base
            for w in chunk:
                line += r"{\k" + str(wdur_cs) + "}" + w + " "
            line = _clean_subtitle_artifacts(line)

            events.append(
                f"Dialogue: 1,{_to_ass_time(c_start)},{_to_ass_time(c_end)},Default,,0,0,0,,{line.strip()}"
            )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info(f"ASS created: {len(events)} events")
    return len(events)


def generate_timed_ass_from_text(
    text: str,
    audio_duration: float,
    ass_path: Path,
) -> int:
    """Generate ASS subtitles from plain text when no VTT is available.

    Splits text into chunks and distributes evenly across audio duration.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial Black,90,"
        "&H00FFFFFF,&H0000D7FF,&H00000000,&H99000000,"
        "-1,0,0,0,100,100,2,0,1,4,2,2,80,80,220,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    words = text.upper().split()
    if not words:
        ass_path.write_text(header, encoding="utf-8")
        return 0

    chunk_size = 4
    events = []
    time_per_word = audio_duration / len(words)
    wdur_cs = max(6, int(round(time_per_word * 100)))

    for i in range(0, len(words), chunk_size):
        chunk = words[i : i + chunk_size]
        c_start = i * time_per_word
        c_end = min((i + len(chunk)) * time_per_word, audio_duration)

        base = _subtitle_base_tag()
        line = base
        for w in chunk:
            line += r"{\k" + str(wdur_cs) + "}" + w + " "
        line = _clean_subtitle_artifacts(line)

        events.append(
            f"Dialogue: 1,{_to_ass_time(c_start)},{_to_ass_time(c_end)},Default,,0,0,0,,{line.strip()}"
        )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info(f"ASS from text: {len(events)} events for {audio_duration:.1f}s")
    return len(events)


def _parse_vtt_time(t: str) -> float:
    t = t.strip().replace(",", ".")
    p = t.split(":")
    if len(p) == 3:
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    if len(p) == 2:
        return int(p[0]) * 60 + float(p[1])
    return 0.0


def _to_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _subtitle_base_tag() -> str:
    """Animated subtitle style tag used across generators."""
    return (
        r"{\an2\bord4\blur3\1c&H00FFFFFF&\3c&H000000&\fs90"
        r"\fad(100,120)\t(0,180,\fscx108\fscy108)\t(180,420,\fscx100\fscy100)}"
    )


def _clean_subtitle_artifacts(line: str) -> str:
    """Remove synthetic suffix artifacts from generated subtitle lines."""
    cleaned = str(line or "").strip()
    cleaned = re.sub(r"\s*\((?:!|:\$|\^_\^|\*)\)\s*$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()
