"""TTS Engine — Gemini TTS primary, Edge-TTS fallback.

Replaces n8n nodes: 🗣️ Gemini-TTS + 🗣️ Edge-TTS Fallback.
Audio processing with identical FFmpeg filters as MASTER V13.
"""
from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import wave
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import request_with_retry


def generate_tts(
    text: str,
    output_mp3: Path,
    voz_gemini: str = "Kore",
    voz_edge: str = "es-MX-JorgeNeural",
    rate_tts: str = "+0%",
    pitch_tts: str = "+0Hz",
    subs_vtt_path: Optional[Path] = None,
) -> tuple[bool, str]:
    """Generate TTS audio. Returns (success, engine_used).

    Tries Gemini TTS first, falls back to Edge-TTS.
    """
    if output_mp3.exists() and output_mp3.stat().st_size > 1000:
        logger.info("TTS audio already exists, skipping")
        return True, "cached"

    strict_free = settings.free_mode and not settings.allow_freemium_in_free_mode

    # In strict free mode, prefer fully offline TTS first when available.
    if strict_free and settings.use_piper_tts:
        if _piper_tts(text, output_mp3):
            if subs_vtt_path:
                subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return True, "piper"

    # Try Gemini TTS (unless blocked by provider policy)
    if settings.gemini_api_key and settings.provider_allowed("gemini"):
        success = _gemini_tts(text, output_mp3, voz_gemini)
        if success:
            # Create empty VTT for ASS generation (Gemini doesn't provide timing)
            if subs_vtt_path:
                subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return True, "gemini"
        logger.warning("Gemini TTS failed, trying Edge-TTS")
    elif settings.gemini_api_key and not settings.provider_allowed("gemini"):
        logger.info("Gemini TTS skipped by provider policy")

    # Fallback: Edge-TTS
    success = _edge_tts(text, output_mp3, voz_edge, rate_tts, pitch_tts, subs_vtt_path)
    if success:
        return True, "edge-tts"

    # Final fallback: Piper offline TTS (if configured)
    if settings.use_piper_tts and _piper_tts(text, output_mp3):
        if subs_vtt_path:
            subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
        return True, "piper"

    return False, "none"


def _piper_tts(text: str, output_mp3: Path) -> bool:
    """Generate local/offline TTS via Piper CLI."""
    model_cfg = (settings.piper_model_path or "").strip()
    if not model_cfg:
        logger.debug("Piper TTS disabled: PIPER_MODEL_PATH not configured")
        return False

    model_path = Path(model_cfg)
    if not model_path.is_absolute():
        model_path = settings.base_dir / model_path

    if not model_path.exists():
        logger.warning(f"Piper model not found: {model_path}")
        return False

    piper_bin = shutil.which("piper")
    if not piper_bin:
        logger.warning("Piper CLI not found in PATH")
        return False

    raw_wav = output_mp3.with_name(f"piper_raw_{output_mp3.stem}.wav")
    cmd = [
        piper_bin,
        "-m", str(model_path),
        "-f", str(raw_wav),
    ]

    try:
        result = subprocess.run(
            cmd,
            input=text,
            text=True,
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning(f"Piper TTS failed: {(result.stderr or '')[-200:]}")
            raw_wav.unlink(missing_ok=True)
            return False

        if not raw_wav.exists() or raw_wav.stat().st_size < 1000:
            logger.warning("Piper TTS produced empty audio")
            raw_wav.unlink(missing_ok=True)
            return False

        success = _apply_audio_filters(raw_wav, output_mp3)
        raw_wav.unlink(missing_ok=True)
        if success:
            logger.info("✅ Piper TTS generado (offline)")
        return success
    except Exception as exc:
        logger.warning(f"Piper TTS error: {exc}")
        raw_wav.unlink(missing_ok=True)
        return False


def _gemini_tts(text: str, output_mp3: Path, voice: str) -> bool:
    """Generate TTS via Gemini API con rotación de las 4 keys."""
    all_keys = settings.get_gemini_keys()
    if not all_keys:
        logger.warning("Gemini TTS: no keys configured")
        return False

    for key_idx, api_key in enumerate(all_keys):
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/"
                f"models/gemini-2.5-flash-preview-tts:generateContent"
                f"?key={api_key}"
            )

            payload = {
                "contents": [{"parts": [{"text": text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": voice}
                        }
                    },
                },
            }

            response = request_with_retry(
                "POST", url,
                json_data=payload,
                headers={"Content-Type": "application/json"},
                max_retries=1,
                timeout=60,
            )

            if response.status_code == 429:
                logger.debug(f"Gemini TTS key#{key_idx+1} quota agotada, rotando...")
                continue

            if response.status_code != 200:
                logger.warning(f"Gemini TTS key#{key_idx+1} HTTP {response.status_code}")
                continue

            data = response.json()
            parts = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )

            audio_b64 = None
            mime_type = "audio/pcm;rate=24000"
            for p in parts:
                inline = p.get("inlineData", {})
                if inline.get("data"):
                    audio_b64 = inline["data"]
                    mime_type = inline.get("mimeType", mime_type)
                    break

            if not audio_b64:
                logger.warning(f"Gemini TTS key#{key_idx+1}: no audio data")
                continue

            pcm_data = base64.b64decode(audio_b64)

            # Extract sample rate
            sample_rate = 24000
            if "rate=" in mime_type:
                try:
                    sample_rate = int(mime_type.split("rate=")[1].split(";")[0].strip())
                except (ValueError, IndexError):
                    pass

            # Write WAV
            raw_wav = output_mp3.with_suffix(".wav")
            with wave.open(str(raw_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm_data)

            if not raw_wav.exists() or raw_wav.stat().st_size < 100:
                logger.warning(f"Gemini TTS key#{key_idx+1}: WAV vacío")
                raw_wav.unlink(missing_ok=True)
                continue

            # Convert to MP3 with audio filters
            success = _apply_audio_filters(raw_wav, output_mp3)
            raw_wav.unlink(missing_ok=True)
            if success:
                logger.info(f"✅ Gemini TTS generado con key#{key_idx+1}")
                return True

        except Exception as e:
            logger.warning(f"Gemini TTS key#{key_idx+1} error: {e}")
            continue

    return False


def _edge_tts(
    text: str,
    output_mp3: Path,
    voice: str,
    rate: str,
    pitch: str,
    subs_vtt_path: Optional[Path] = None,
) -> bool:
    """Generate TTS via edge-tts Python library."""
    try:
        import asyncio
        import edge_tts

        async def _run():
            communicate = edge_tts.Communicate(
                text, voice, rate=rate, pitch=pitch
            )
            tmp_wav = output_mp3.with_name(f"edge_raw_{output_mp3.stem}.wav")
            vtt_path = subs_vtt_path or output_mp3.with_suffix(".vtt")

            await communicate.save(str(tmp_wav))

            if vtt_path:
                try:
                    submaker = edge_tts.SubMaker()
                    async for chunk in edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).stream():
                        if chunk["type"] == "WordBoundary":
                            # Compatibilidad edge-tts <7 y >=7
                            if hasattr(submaker, 'feed'):
                                submaker.feed(chunk)
                            elif hasattr(submaker, 'create_sub'):
                                submaker.create_sub(
                                    chunk.get("offset", 0),
                                    chunk.get("duration", 0),
                                    chunk.get("text", ""),
                                )
                    # Compatibilidad get_subs() vs srt
                    if hasattr(submaker, 'get_subs'):
                        subs_content = submaker.get_subs()
                    elif hasattr(submaker, 'srt'):
                        subs_content = submaker.srt
                    else:
                        subs_content = "WEBVTT\n\n"
                    vtt_path.write_text(subs_content, encoding="utf-8")
                except Exception as e:
                    logger.debug(f"Edge-TTS subtitle generation failed: {e}")
                    vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")

            return tmp_wav

        # Handle Windows event loop policy
        import sys
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    tmp_wav = pool.submit(asyncio.run, _run()).result()
            else:
                tmp_wav = loop.run_until_complete(_run())
        except RuntimeError:
            tmp_wav = asyncio.run(_run())

        if not tmp_wav.exists() or tmp_wav.stat().st_size < 100:
            logger.warning("Edge-TTS: empty audio")
            return False

        success = _apply_audio_filters(tmp_wav, output_mp3)
        tmp_wav.unlink(missing_ok=True)
        return success

    except ImportError:
        logger.error("edge-tts not installed. Run: pip install edge-tts")
        return False
    except Exception as e:
        logger.error(f"Edge-TTS error: {e}")
        return False


def _apply_audio_filters(input_wav: Path, output_mp3: Path) -> bool:
    """Apply audio enhancement filters (identical to MASTER V13)."""
    af = (
        "aresample=resampler=swr,"
        "highpass=f=80,"
        "lowpass=f=15000,"
        "equalizer=f=180:width_type=o:width=2:g=2,"
        "equalizer=f=3500:width_type=o:width=2:g=1.5,"
        "acompressor=threshold=-18dB:ratio=3:attack=5:release=80:makeup=2,"
        "loudnorm=I=-14:TP=-1.5:LRA=7"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_wav.as_posix(),
        "-af", af,
        "-ar", "48000",
        "-b:a", "320k",
        str(output_mp3),
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and output_mp3.exists() and output_mp3.stat().st_size > 1000:
            logger.info(f"Audio processed: {output_mp3.name}")
            return True
        else:
            logger.warning(f"Audio filter failed: {result.stderr[-200:]}")
            return False
    except Exception as e:
        logger.error(f"Audio processing error: {e}")
        return False


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        duration = float(result.stdout.strip())
        if duration > 0:
            return duration
    except Exception as e:
        logger.warning(f"ffprobe error: {e}")
    return 30.0  # fallback
