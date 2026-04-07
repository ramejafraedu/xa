"""WhisperX Subtitles — Word-level precise timing from audio.

Uses WhisperX (local, free) for precise word-level alignment,
with the existing character-estimation fallback.

MODULE CONTRACT:
  Input:  audio file (MP3/WAV) + output ASS path
  Output: ASS file with precise word timing

Provider hierarchy:
  1. WhisperX (local) → precise word-level timing
  2. generate_timed_ass_from_text() → character-estimation fallback
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings


def generate_ass_whisperx(
    audio_path: Path,
    ass_path: Path,
    language: str = "es",
) -> int:
    """Generate ASS subtitles using WhisperX word-level alignment.

    Args:
        audio_path: Path to audio file (MP3, WAV).
        ass_path: Where to save the ASS file.
        language: Audio language code.

    Returns:
        Number of dialogue events, or 0 if failed.
    """
    if not settings.use_whisperx:
        logger.debug("WhisperX disabled in config")
        return 0

    # Idempotency check
    if ass_path.exists() and ass_path.stat().st_size > 100:
        logger.debug(f"ASS already exists: {ass_path.name}")
        return 1  # Non-zero means "already done"

    if not audio_path.exists():
        logger.warning(f"Audio file not found: {audio_path}")
        return 0

    try:
        import whisperx
        import torch
    except ImportError:
        logger.warning(
            "WhisperX not installed. Run: pip install whisperx torch\n"
            "Falling back to character-estimation subtitles."
        )
        return 0

    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"

        logger.info(f"📝 WhisperX: Transcribing {audio_path.name} on {device}...")

        # Step 1: Transcribe
        model = whisperx.load_model(
            "base",
            device=device,
            compute_type=compute_type,
            language=language,
        )
        audio = whisperx.load_audio(str(audio_path))
        result = model.transcribe(audio, batch_size=8, language=language)

        # Step 2: Word-level alignment
        model_a, metadata = whisperx.load_align_model(
            language_code=language,
            device=device,
        )
        result = whisperx.align(
            result["segments"],
            model_a,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )

        # Step 3: Convert to ASS
        events = _whisperx_to_ass(result, ass_path)
        logger.info(f"✅ WhisperX ASS: {events} events with word-level timing")
        return events

    except Exception as e:
        logger.warning(f"WhisperX failed: {e}")
        return 0


def _whisperx_to_ass(result: dict, ass_path: Path) -> int:
    """Convert WhisperX alignment result to ASS format."""
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
        "Style: Default,Arial Rounded MT Bold,90,"
        "&H00FFFFFF,&H0000D7FF,&H00000000,&H99000000,"
        "-1,0,0,0,100,100,2,0,1,4,2,2,80,80,220,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    segments = result.get("segments", [])
    cursor = 0.0

    for seg in segments:
        timed_words, cursor = _build_timed_words(seg, cursor)
        if not timed_words:
            continue

        # Group timed words into chunks of 4
        for i in range(0, len(timed_words), 4):
            chunk = timed_words[i:i + 4]
            if not chunk:
                continue

            start_time = chunk[0][1]
            end_time = chunk[-1][2]

            # Build ASS line with karaoke timing
            base = r"{\an2\bord4\blur3\1c&H00FFFFFF&\3c&H000000&\fs90\fad(120,120)}"
            line = base
            for word_text, w_start, w_end in chunk:
                word_dur = max(0.08, w_end - w_start)
                wdur_cs = max(6, int(round(word_dur * 100)))
                line += r"{\k" + str(wdur_cs) + "}" + word_text + " "

            events.append(
                f"Dialogue: 1,{_to_ass_time(start_time)},{_to_ass_time(end_time)},"
                f"Default,,0,0,0,,{line.strip()}"
            )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


def generate_subtitles_with_fallback(
    audio_path: Path,
    text: str,
    audio_duration: float,
    ass_path: Path,
    language: str = "es",
) -> int:
    """Generate subtitles: WhisperX first, character-estimation fallback.

    This is the main entry point for subtitle generation.
    """
    # Try WhisperX first
    events = generate_ass_whisperx(audio_path, ass_path, language)
    if events > 0:
        return events

    # Fallback: character-estimation
    logger.info("Using character-estimation subtitles (WhisperX unavailable)")
    from pipeline.subtitles import generate_timed_ass_from_text
    return generate_timed_ass_from_text(text, audio_duration, ass_path)


def _to_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _build_timed_words(seg: dict, fallback_start: float) -> tuple[list[tuple[str, float, float]], float]:
    """Build contiguous word timings, falling back to segment distribution when needed."""
    raw_words = seg.get("words", []) or []
    words = []
    for w in raw_words:
        text = str(w.get("word", "")).strip().upper()
        if text:
            words.append((text, _as_float(w.get("start")), _as_float(w.get("end"))))

    if not words:
        return [], fallback_start

    seg_start = _as_float(seg.get("start"))
    if seg_start is None:
        seg_start = fallback_start
    seg_start = max(0.0, seg_start)

    seg_end = _as_float(seg.get("end"))
    if seg_end is None or seg_end <= seg_start:
        seg_end = seg_start + max(0.8, 0.28 * len(words))

    # Prefer WhisperX word timing only when complete and monotonic.
    if _has_valid_word_timings(words):
        normalized: list[tuple[str, float, float]] = []
        cursor = seg_start
        for text, w_start, w_end in words:
            assert w_start is not None and w_end is not None
            start = max(cursor, seg_start, w_start)
            end = min(seg_end, max(start + 0.08, w_end))
            if end <= start:
                end = start + 0.08
            normalized.append((text, start, end))
            cursor = end
        return normalized, max(seg_end, normalized[-1][2])

    # Fallback: distribute words uniformly within segment to avoid subtitle gaps.
    seg_dur = max(0.8, seg_end - seg_start)
    step = seg_dur / len(words)
    normalized = []
    for idx, (text, _, __) in enumerate(words):
        start = seg_start + idx * step
        end = seg_start + (idx + 1) * step
        normalized.append((text, start, end))
    return normalized, seg_end


def _has_valid_word_timings(words: list[tuple[str, Optional[float], Optional[float]]]) -> bool:
    prev_end = -1.0
    for _, start, end in words:
        if start is None or end is None:
            return False
        if end <= start:
            return False
        if start < prev_end - 0.12:
            return False
        prev_end = end
    return True


def _as_float(value: object) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
