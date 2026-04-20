"""Local open-source TTS fallback (Phase 3 scaffold).

Wire XTTS, Bark, or Piper here and call ``try_local_tts`` from ``pipeline.tts_engine``
when cloud providers return 429 or exhaust the cascade.

This module stays optional: no heavy ML deps in base ``requirements.txt``.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings


def try_local_tts(
    text: str,
    output_path: Path,
    *,
    backend: Optional[str] = None,
) -> tuple[bool, str]:
    """Synthesize speech locally. Returns (success, engine_or_error).

    When ``local_tts_enabled`` is false or backend is ``none``, returns immediately.
    Implementations below are stubs until models are installed and configured.
    """
    if not getattr(settings, "local_tts_enabled", False):
        return False, "local_tts_disabled"

    b = (backend or getattr(settings, "local_tts_backend", "none") or "none").strip().lower()
    if b in {"", "none"}:
        return False, "local_tts_backend_none"

    text_clean = (text or "").strip()
    if len(text_clean) < 2:
        return False, "empty_text"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if b == "piper":
        return _try_piper(text_clean, output_path)
    if b == "xtts":
        return _stub_xtts(text_clean, output_path)
    if b == "bark":
        return _stub_bark(text_clean, output_path)

    return False, f"unknown_local_tts_backend:{b}"


def _try_piper(text: str, output_path: Path) -> tuple[bool, str]:
    """Piper CLI if ``piper`` is on PATH and ``local_tts_model_path`` points to .onnx."""
    onnx = getattr(settings, "local_tts_model_path", "") or ""
    if not onnx or not Path(onnx).exists():
        logger.debug("Piper: set local_tts_model_path to a .onnx voice model")
        return False, "piper_model_missing"

    piper_bin = shutil.which("piper") or shutil.which("piper.exe")
    if not piper_bin:
        return False, "piper_cli_not_in_path"

    wav = output_path.with_suffix(".wav")
    try:
        proc = subprocess.run(
            [piper_bin, "--model", onnx, "--output_file", str(wav)],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        if proc.returncode != 0 or not wav.exists():
            return False, (proc.stderr or b"").decode("utf-8", errors="replace")[:500]

        if output_path.suffix.lower() in {".mp3", ".m4a"}:
            ff = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
            if not ff:
                return True, "piper_wav_only"
            r2 = subprocess.run(
                [ff, "-y", "-i", str(wav), str(output_path)],
                capture_output=True,
                timeout=60,
            )
            if r2.returncode == 0 and output_path.exists():
                wav.unlink(missing_ok=True)
                return True, "piper+ffmpeg"
            return True, f"piper_ok_wav={wav}"

        return True, "piper"
    except Exception as exc:
        return False, str(exc)[:500]


def _stub_xtts(text: str, output_path: Path) -> tuple[bool, str]:
    """Placeholder for Coqui XTTS / similar — install deps and implement."""
    logger.info("local_tts: XTTS backend not wired; add TTS package + model load here")
    return False, "xtts_not_implemented"


def _stub_bark(text: str, output_path: Path) -> tuple[bool, str]:
    """Placeholder for Suno Bark — GPU-friendly; add transformers + model here."""
    logger.info("local_tts: Bark backend not wired; add bark import + generate_audio here")
    return False, "bark_not_implemented"
