"""AssemblyAI subtitles fallback for V15.

Generates ASS word-by-word subtitles from AssemblyAI transcript words.
This is used when WhisperX fails or is unavailable.
"""
from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

from services.http_client import request_with_retry


ASSEMBLY_UPLOAD_URL = "https://api.assemblyai.com/v2/upload"
ASSEMBLY_TRANSCRIPT_URL = "https://api.assemblyai.com/v2/transcript"


def generate_ass_assemblyai(
    audio_path: Path,
    ass_path: Path,
    api_key: str,
    language_code: str = "es",
    poll_seconds: int = 2,
    max_polls: int = 90,
) -> int:
    """Transcribe audio with AssemblyAI and write word-level ASS subtitles.

    Returns number of ASS dialogue events generated.
    """
    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise RuntimeError("audio file missing or too small for AssemblyAI")

    token = (api_key or "").strip()
    if not token:
        raise RuntimeError("AssemblyAI API key is empty")

    headers = {"authorization": token}

    upload_resp = request_with_retry(
        "POST",
        ASSEMBLY_UPLOAD_URL,
        headers={**headers, "content-type": "application/octet-stream"},
        data=audio_path.read_bytes(),
        max_retries=2,
        timeout=180,
    )
    if upload_resp.status_code != 200:
        raise RuntimeError(f"AssemblyAI upload failed: HTTP {upload_resp.status_code}")

    upload_url = (upload_resp.json() or {}).get("upload_url")
    if not upload_url:
        raise RuntimeError("AssemblyAI upload missing upload_url")

    create_resp = request_with_retry(
        "POST",
        ASSEMBLY_TRANSCRIPT_URL,
        headers={**headers, "content-type": "application/json"},
        json_data={
            "audio_url": upload_url,
            "language_code": language_code or "es",
            "punctuate": True,
            "format_text": True,
        },
        max_retries=2,
        timeout=90,
    )
    if create_resp.status_code not in (200, 201):
        raise RuntimeError(f"AssemblyAI transcript create failed: HTTP {create_resp.status_code}")

    transcript_id = (create_resp.json() or {}).get("id")
    if not transcript_id:
        raise RuntimeError("AssemblyAI transcript id missing")

    transcript_data: dict = {}
    for _ in range(max_polls):
        poll_resp = request_with_retry(
            "GET",
            f"{ASSEMBLY_TRANSCRIPT_URL}/{transcript_id}",
            headers=headers,
            max_retries=1,
            timeout=45,
        )
        if poll_resp.status_code != 200:
            time.sleep(poll_seconds)
            continue

        transcript_data = poll_resp.json() or {}
        status = str(transcript_data.get("status", ""))
        if status == "completed":
            break
        if status == "error":
            msg = str(transcript_data.get("error", "unknown error"))
            raise RuntimeError(f"AssemblyAI transcript error: {msg}")
        time.sleep(poll_seconds)

    words = transcript_data.get("words") if isinstance(transcript_data, dict) else None
    if not isinstance(words, list) or not words:
        raise RuntimeError("AssemblyAI transcript returned no words")

    events = _words_to_ass(words, ass_path)
    logger.info(f"📝 AssemblyAI ASS: {events} events with word-level timing")
    return events


def _words_to_ass(words: list[dict], ass_path: Path) -> int:
    """Convert AssemblyAI words[] to ASS Dialogue events."""
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

    valid: list[tuple[str, int, int]] = []
    for w in words:
        if not isinstance(w, dict):
            continue
        text = str(w.get("text", "")).strip().upper()
        if not text:
            continue
        try:
            start_ms = int(w.get("start", 0) or 0)
            end_ms = int(w.get("end", start_ms + 120) or (start_ms + 120))
        except Exception:
            continue
        if end_ms <= start_ms:
            end_ms = start_ms + 120
        valid.append((text, start_ms, end_ms))

    if not valid:
        ass_path.write_text(header, encoding="utf-8")
        return 0

    chunk_size = 4
    events: list[str] = []
    for idx in range(0, len(valid), chunk_size):
        chunk = valid[idx : idx + chunk_size]
        start_s = chunk[0][1] / 1000.0
        end_s = chunk[-1][2] / 1000.0

        line = _subtitle_base_tag()
        chunk_words: list[str] = []
        for text, start_ms, end_ms in chunk:
            dur_cs = max(4, int(round((end_ms - start_ms) / 10.0)))
            line += r"{\k" + str(dur_cs) + "}" + text + " "
            chunk_words.append(text)

        line += _line_emoticon(chunk_words)
        events.append(
            f"Dialogue: 1,{_to_ass_time(start_s)},{_to_ass_time(end_s)},Default,,0,0,0,,{line.strip()}"
        )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return len(events)


def _to_ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    cs = int(round((s - int(s)) * 100))
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def _subtitle_base_tag() -> str:
    return (
        r"{\an2\bord4\blur3\1c&H00FFFFFF&\3c&H000000&\fs90"
        r"\fad(100,120)\t(0,180,\fscx108\fscy108)\t(180,420,\fscx100\fscy100)}"
    )


def _line_emoticon(words: list[str]) -> str:
    joined = " ".join(words).upper()
    if any(k in joined for k in ["DINERO", "NEGOCIO", "RICO", "CRIPTO", "VENTA"]):
        return " (:$)"
    if any(k in joined for k in ["SALUD", "ENERGIA", "CUERPO", "MENTE", "CEREBRO"]):
        return " (^_^)"
    if any(k in joined for k in ["TIP", "HACK", "TRUCO", "SECRETO", "APRENDE"]):
        return " (*)"
    if any(k in joined for k in ["ALERTA", "ERROR", "CUIDADO", "PELIGRO"]):
        return " (!)"
    return ""
