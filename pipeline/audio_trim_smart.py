"""Post-TTS audio processing helpers.

Adds optional silence trimming and loudness normalization with safe fallbacks.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from config import settings


def _run_ffmpeg_filter(input_path: Path, output_path: Path, af_filter: str) -> bool:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        logger.debug("ffmpeg not found in PATH; skipping post-TTS audio processing")
        return False

    cmd = [
        ffmpeg_bin,
        "-y",
        "-nostats",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-af",
        af_filter,
        str(output_path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            logger.debug(f"FFmpeg audio filter failed: {proc.stderr[:180]}")
            return False
        return output_path.exists() and output_path.stat().st_size > 1000
    except Exception as exc:
        logger.debug(f"FFmpeg audio filter error: {exc}")
        return False


def apply_post_tts_audio_processing(audio_path: Path, timestamp: int, temp_dir: Path) -> tuple[Path, list[str]]:
    """Apply optional silence trim and loudnorm normalization after TTS.

    Returns final audio path and list of applied steps.
    """
    steps: list[str] = []
    current = audio_path

    if not current.exists() or current.stat().st_size <= 1000:
        return audio_path, steps

    # Integración con @OpenMontage para procesamiento de audio avanzado
    if settings.enable_openmontage_free_tools:
        from core.openmontage_free import apply_audio_enhance, apply_silence_cutter

        # 1. Enhancement (EQ, Gate, Loudnorm avanzado)
        if settings.enable_post_tts_loudnorm:
            enhance_out = temp_dir / f"audio_enhanced_om_{timestamp}.mp3"
            result_path = apply_audio_enhance(current, enhance_out, preset="clean_speech")
            if result_path and result_path.exists():
                current = result_path
                steps.append("om_audio_enhance")

        # 2. Silence Cutter (Jump cuts dinámicos en vez de solo trim en los bordes)
        if settings.enable_smart_silence_trim:
            noise_db = float(settings.audio_trim_silence_db)
            min_silence = float(settings.audio_trim_min_silence_seconds)
            cutter_out = temp_dir / f"audio_silence_cut_om_{timestamp}.mp3"
            result_path = apply_silence_cutter(
                current, 
                cutter_out, 
                mode="remove", 
                silence_threshold_db=noise_db, 
                min_silence_duration=min_silence
            )
            if result_path and result_path.exists():
                current = result_path
                steps.append("om_silence_cutter")
        
        return current, steps

    # Trim short/leading/trailing silence segments. (Fallback Legacy)
    if settings.enable_smart_silence_trim:
        noise_db = float(settings.audio_trim_silence_db)
        min_silence = float(settings.audio_trim_min_silence_seconds)
        trim_out = temp_dir / f"audio_trimmed_{timestamp}.mp3"
        trim_filter = (
            f"silenceremove=start_periods=1:start_threshold={noise_db}dB:"
            f"start_silence={min_silence}:"
            f"stop_periods=1:stop_threshold={noise_db}dB:"
            f"stop_silence={min_silence}"
        )
        if _run_ffmpeg_filter(current, trim_out, trim_filter):
            current = trim_out
            steps.append("silence_trim")

    # Standardize perceived loudness for better platform consistency.
    if settings.enable_post_tts_loudnorm:
        loudnorm_out = temp_dir / f"audio_loudnorm_{timestamp}.mp3"
        target_i = float(settings.post_tts_loudnorm_i)
        target_lra = float(settings.post_tts_loudnorm_lra)
        target_tp = float(settings.post_tts_loudnorm_tp)
        loudnorm_filter = f"loudnorm=I={target_i}:LRA={target_lra}:TP={target_tp}"
        if _run_ffmpeg_filter(current, loudnorm_out, loudnorm_filter):
            current = loudnorm_out
            steps.append("loudnorm")

    return current, steps
