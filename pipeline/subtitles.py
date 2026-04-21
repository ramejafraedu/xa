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


def _tokenize_subtitle_words(text: str) -> list[str]:
    """Non-empty tokens in order; uppercased for on-screen style."""
    raw = (text or "").strip()
    if not raw:
        return []
    return [m.group(0).upper() for m in re.finditer(r"\S+", raw)]


def _word_time_weights(words: list[str]) -> list[float]:
    """Longer tokens get more time (closer to natural speech pacing heuristic)."""
    weights: list[float] = []
    for w in words:
        core = re.sub(r"^[^\wÁÉÍÓÚÑÜáéíóúñü]+|[^\wÁÉÍÓÚÑÜáéíóúñü]+$", "", w)
        ln = max(1, len(core))
        weights.append(float(ln))
    return weights


def generate_timed_ass_from_text(
    text: str,
    audio_duration: float,
    ass_path: Path,
) -> int:
    """Generate ASS from plain text: proportional word timing + 4-word karaoke lines.

    Time budget is split by character-weighted tokens (not equal per word).
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
        "Style: Default,Arial Black,96,"
        "&H00FFFFFF,&H0000D7FF,&H00000000,&H99000000,"
        "-1,0,0,0,100,100,2,0,1,5,3,2,60,60,420,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    words = _tokenize_subtitle_words(text)
    if not words:
        ass_path.write_text(header, encoding="utf-8")
        return 0

    dur = max(0.5, float(audio_duration or 0.0))
    weights = _word_time_weights(words)
    total_w = sum(weights) or float(len(words))
    # Per-word start/end in seconds
    starts: list[float] = []
    ends: list[float] = []
    t = 0.0
    for i, w in enumerate(words):
        wlen = weights[i]
        span = dur * (wlen / total_w)
        span = max(span, 0.06)
        starts.append(t)
        ends.append(min(dur, t + span))
        t = ends[-1]
    if ends and ends[-1] < dur - 0.02:
        scale = dur / max(ends[-1], 0.01)
        starts = [min(dur, s * scale) for s in starts]
        ends = [min(dur, e * scale) for e in ends]
        for k in range(1, len(starts)):
            starts[k] = max(starts[k], ends[k - 1])

    chunk_size = 4
    events: list[str] = []
    for i in range(0, len(words), chunk_size):
        chunk_words = words[i : i + chunk_size]
        idx_slice = slice(i, i + len(chunk_words))
        c_start = starts[idx_slice.start]
        c_end = ends[idx_slice.stop - 1]

        base = _subtitle_base_tag()
        line = base
        for j, w in enumerate(chunk_words):
            wi = i + j
            word_dur = max(0.08, ends[wi] - starts[wi])
            wdur_cs = max(6, int(round(word_dur * 100)))
            line += r"{\k" + str(wdur_cs) + "}" + w + " "
        line = _clean_subtitle_artifacts(line)

        events.append(
            f"Dialogue: 1,{_to_ass_time(c_start)},{_to_ass_time(c_end)},Default,,0,0,0,,{line.strip()}"
        )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info(f"ASS from text (proportional): {len(events)} events for {dur:.1f}s")
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
    """Animated subtitle style tag used across generators (9:16 mobile-first)."""
    return (
        r"{\an2\bord5\blur2\shad3\1c&H00FFFFFF&\3c&H000000&\4c&H99000000&\fs96"
        r"\fad(80,100)\t(0,160,\fscx107\fscy107)\t(160,380,\fscx100\fscy100)}"
    )


def _clean_subtitle_artifacts(line: str) -> str:
    """Remove synthetic suffix artifacts from generated subtitle lines."""
    cleaned = str(line or "").strip()
    cleaned = re.sub(r"\s*\((?:!|:\$|\^_\^|\*)\)\s*$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()
