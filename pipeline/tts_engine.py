"""TTS Engine — Multi-provider TTS with scored fallback.

Replaces n8n nodes: 🗣️ Gemini-TTS + 🗣️ Edge-TTS Fallback.
Audio processing with identical FFmpeg filters as MASTER V13.
"""
from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import time
import wave
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import request_with_retry
from services.provider_cascade import ProviderCascade


_EDGE_DISABLED_UNTIL = 0.0
_EDGE_DISABLE_SECONDS = 15 * 60


def _edge_is_temporarily_disabled() -> bool:
    return time.time() < _EDGE_DISABLED_UNTIL


def _mark_edge_temporarily_disabled(reason: str) -> None:
    global _EDGE_DISABLED_UNTIL
    _EDGE_DISABLED_UNTIL = max(_EDGE_DISABLED_UNTIL, time.time() + _EDGE_DISABLE_SECONDS)
    logger.warning(
        "Edge-TTS temporarily disabled for "
        f"{_EDGE_DISABLE_SECONDS}s after auth/rate error: {reason[:120]}"
    )


_tts_cascade = None

def _get_tts_cascade() -> ProviderCascade:
    global _tts_cascade
    if _tts_cascade is None:
        _tts_cascade = ProviderCascade(
            name="tts",
            state_dir=settings.temp_dir,
            cooldown_seconds=settings.provider_cascade_cooldown_seconds,
            max_consecutive_failures=settings.provider_cascade_max_failures,
        )
    return _tts_cascade


def generate_tts(
    text: str,
    output_mp3: Path,
    voz_gemini: str = "Kore",
    voz_edge: str = "es-MX-JorgeNeural",
    rate_tts: str = "+0%",
    pitch_tts: str = "+0Hz",
    subs_vtt_path: Optional[Path] = None,
    enforce_provider_policy: bool = True,
    sync_subtitles: bool = True,  # NEW: Enable subtitle synchronization
    provider_order: Optional[list[str]] = None,
) -> tuple[bool, str]:
    """Generate TTS audio. Returns (success, engine_used).

    Uses ProviderCascade for scored fallback
    (Google Cloud TTS first when configured, else Gemini / Edge / Piper / ElevenLabs by scores).
    If sync_subtitles is True and TTS doesn't provide timing,
    uses AudioSubtitleSynchronizer to force-align text with audio.
    """
    if output_mp3.exists() and output_mp3.stat().st_size > 1000:
        logger.info("TTS audio already exists, skipping")
        # Even if audio exists, ensure subtitles are synced if needed
        if subs_vtt_path and sync_subtitles and not subs_vtt_path.exists():
            _sync_subtitles_if_needed(output_mp3, text, subs_vtt_path)
        return True, "cached"

    strict_free = enforce_provider_policy and (
        settings.v15_strict_free_media_tools
        or (settings.free_mode and not settings.allow_freemium_in_free_mode)
    )

    if not settings.enable_provider_cascade:
        return _generate_tts_legacy(
            text, output_mp3, voz_gemini, voz_edge, rate_tts, pitch_tts,
            subs_vtt_path, enforce_provider_policy, sync_subtitles
        )

    cascade = _get_tts_cascade()
    provider_scores = _build_tts_provider_scores(provider_order, strict_free)

    # Define simple callable wrappers for each provider
    def wrap_piper():
        return _piper_tts(text, output_mp3)
    
    def wrap_elevenlabs():
        return _elevenlabs_tts(text, output_mp3)

    def wrap_google_tts():
        return _google_cloud_tts(text, output_mp3)
        
    def wrap_gemini():
        return _gemini_tts(text, output_mp3, voz_gemini)
        
    def wrap_edge():
        return _edge_tts(text, output_mp3, voz_edge, rate_tts, pitch_tts, subs_vtt_path)

    # Register Piper
    cascade.register(
        "piper",
        wrap_piper,
        tier="free",
        base_score=provider_scores["piper"],
        enabled=settings.use_piper_tts
    )

    # Register ElevenLabs
    eleven_allowed = (
        settings.provider_allowed("elevenlabs", usage="media")
        if enforce_provider_policy else True
    )
    cascade.register(
        "elevenlabs",
        wrap_elevenlabs,
        tier="premium",
        base_score=provider_scores["elevenlabs"],
        enabled=(
            bool(settings.elevenlabs_api_key)
            and bool(settings.enable_elevenlabs_tts)
            and eleven_allowed
        ),
    )

    # Register Google Cloud TTS
    google_tts_allowed = (
        settings.provider_allowed("google_tts", usage="media")
        if enforce_provider_policy else True
    )
    cascade.register(
        "google_tts",
        wrap_google_tts,
        tier="premium",
        base_score=provider_scores["google_tts"],
        enabled=settings.use_google_tts and google_tts_allowed,
    )

    # Register Gemini
    gemini_allowed = (
        settings.provider_allowed("gemini", usage="media")
        if enforce_provider_policy else True
    )
    cascade.register(
        "gemini",
        wrap_gemini,
        tier="freemium",
        base_score=provider_scores["gemini"],
        enabled=bool(settings.get_gemini_keys()) and gemini_allowed
    )

    # Register Edge-TTS
    cascade.register(
        "edge-tts",
        wrap_edge,
        tier="free",
        base_score=provider_scores["edge-tts"],
        enabled=not _edge_is_temporarily_disabled()
    )

    result = cascade.execute(provider_order=provider_order)

    if result.success:
        # Subtitles synchronization step
        if subs_vtt_path:
            if sync_subtitles:
                _sync_subtitles_if_needed(output_mp3, text, subs_vtt_path)
            else:
                if not subs_vtt_path.exists():
                    subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
        return True, result.provider_name
    
    logger.warning(f"All TTS providers failed: {result.error}")
    return False, "none"


def _normalize_tts_provider(name: object) -> str:
    value = str(name or "").strip().lower()
    aliases = {
        "edge_tts": "edge-tts",
        "edge": "edge-tts",
        "piper_tts": "piper",
    }
    return aliases.get(value, value)


def _build_tts_provider_scores(provider_order: Optional[list[str]], strict_free: bool) -> dict[str, float]:
    default_order = ["piper", "edge-tts", "gemini", "google_tts", "elevenlabs"]
    if not strict_free:
        default_order = ["elevenlabs", "google_tts", "gemini", "edge-tts", "piper"]
    if settings.gemini_everywhere_mode and not strict_free:
        default_order = ["gemini", "edge-tts", "piper", "google_tts", "elevenlabs"]
    # When Google Cloud TTS is configured, prefer it over ElevenLabs (and over Gemini in everywhere mode).
    if settings.google_tts_effective_enabled() and not strict_free:
        if settings.gemini_everywhere_mode:
            default_order = ["google_tts", "gemini", "edge-tts", "piper", "elevenlabs"]
        else:
            default_order = ["google_tts", "elevenlabs", "gemini", "edge-tts", "piper"]

    merged: list[str] = []
    seen: set[str] = set()
    for provider in (provider_order or []) + default_order:
        normalized = _normalize_tts_provider(provider)
        if normalized not in {"piper", "edge-tts", "gemini", "google_tts", "elevenlabs"}:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)

    # Highest-priority provider gets highest base score.
    total = len(merged)
    scores: dict[str, float] = {}
    for idx, provider in enumerate(merged):
        scores[provider] = float((total - idx) * 10)

    for provider in ["piper", "edge-tts", "gemini", "google_tts", "elevenlabs"]:
        scores.setdefault(provider, 10.0)

    return scores


def _generate_tts_legacy(
    text: str,
    output_mp3: Path,
    voz_gemini: str = "Kore",
    voz_edge: str = "es-MX-JorgeNeural",
    rate_tts: str = "+0%",
    pitch_tts: str = "+0Hz",
    subs_vtt_path: Optional[Path] = None,
    enforce_provider_policy: bool = True,
    sync_subtitles: bool = True,
) -> tuple[bool, str]:
    """Original fallback sequence for backward compatibility."""
    strict_free = enforce_provider_policy and (
        settings.v15_strict_free_media_tools
        or (settings.free_mode and not settings.allow_freemium_in_free_mode)
    )

    # In strict free mode, prefer fully offline TTS first when available.
    if strict_free and settings.use_piper_tts:
        if _piper_tts(text, output_mp3):
            if subs_vtt_path:
                if sync_subtitles:
                    _sync_subtitles_if_needed(output_mp3, text, subs_vtt_path)
                else:
                    subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return True, "piper"

    eleven_allowed = (
        settings.provider_allowed("elevenlabs", usage="media")
        if enforce_provider_policy
        else True
    )
    google_tts_allowed = (
        settings.provider_allowed("google_tts", usage="media")
        if enforce_provider_policy
        else True
    )
    if settings.google_tts_effective_enabled() and google_tts_allowed:
        success = _google_cloud_tts(text, output_mp3)
        if success:
            if subs_vtt_path:
                if sync_subtitles:
                    _sync_subtitles_if_needed(output_mp3, text, subs_vtt_path)
                else:
                    subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return True, "google_tts"

    if (
        settings.enable_elevenlabs_tts
        and settings.elevenlabs_api_key
        and eleven_allowed
    ):
        success = _elevenlabs_tts(text, output_mp3)
        if success:
            if subs_vtt_path:
                if sync_subtitles:
                    _sync_subtitles_if_needed(output_mp3, text, subs_vtt_path)
                else:
                    subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return True, "elevenlabs"

    # Try Gemini TTS
    gemini_allowed = (
        settings.provider_allowed("gemini", usage="media")
        if enforce_provider_policy
        else True
    )
    if settings.get_gemini_keys() and gemini_allowed:
        success = _gemini_tts(text, output_mp3, voz_gemini)
        if success:
            if subs_vtt_path:
                if sync_subtitles:
                    _sync_subtitles_if_needed(output_mp3, text, subs_vtt_path)
                else:
                    subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
            return True, "gemini"

    # Fallback: Edge-TTS
    if not _edge_is_temporarily_disabled():
        success = _edge_tts(text, output_mp3, voz_edge, rate_tts, pitch_tts, subs_vtt_path)
        if success:
            return True, "edge-tts"

    # Final fallback: Piper
    if settings.use_piper_tts and _piper_tts(text, output_mp3):
        if subs_vtt_path:
            subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
        return True, "piper"

    return False, "none"


def _sync_subtitles_if_needed(
    audio_path: Path,
    script_text: str,
    subs_vtt_path: Path,
    language: str = "es"
) -> bool:
    """Synchronize subtitles with audio using forced alignment.
    
    This function runs after TTS generation to create accurate word-level
    timestamps for subtitle burning, even when TTS doesn't provide them.
    
    Args:
        audio_path: Path to generated audio file
        script_text: Original script text
        subs_vtt_path: Where to save synchronized VTT
        language: Language code
        
    Returns:
        True if synchronization successful
    """
    try:
        from pipeline.audio_sync import AudioSubtitleSynchronizer
        
        logger.info(f"🔍 Synchronizing subtitles for: {audio_path.name}")
        
        synchronizer = AudioSubtitleSynchronizer(model_size="base")
        success = synchronizer.align_script_with_audio(
            audio_path=audio_path,
            script_text=script_text,
            output_vtt=subs_vtt_path,
            language=language
        )
        
        if success:
            logger.info(f"✅ Subtitles synchronized: {subs_vtt_path.name}")
        else:
            logger.warning("⚠️ Subtitle sync failed, using empty VTT")
            subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
        
        return success
        
    except Exception as e:
        logger.warning(f"Subtitle sync error: {e}")
        # Fallback: create empty VTT
        try:
            subs_vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
        except Exception:
            pass
        return False


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


def _elevenlabs_tts(text: str, output_mp3: Path) -> bool:
    """Generate TTS via ElevenLabs API and normalize audio with ffmpeg."""
    api_key = (settings.elevenlabs_api_key or "").strip()
    if not api_key:
        return False

    voice_id = (settings.elevenlabs_voice_id or "").strip()
    if not voice_id:
        logger.warning("ElevenLabs TTS skipped: ELEVENLABS_VOICE_ID is empty")
        return False

    base_url = (settings.elevenlabs_api_url or "https://api.elevenlabs.io/v1/text-to-speech").rstrip("/")
    url = f"{base_url}/{voice_id}"
    raw_mp3 = output_mp3.with_name(f"eleven_raw_{output_mp3.stem}.mp3")

    try:
        payload = {
            "text": text,
            "model_id": settings.elevenlabs_model_id or "eleven_multilingual_v2",
            "voice_settings": {
                "stability": max(0.0, min(1.0, float(settings.elevenlabs_stability))),
                "similarity_boost": max(0.0, min(1.0, float(settings.elevenlabs_similarity_boost))),
            },
        }

        response = request_with_retry(
            "POST",
            url,
            headers={
                "xi-api-key": api_key,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            json_data=payload,
            max_retries=2,
            timeout=90,
        )

        if response.status_code != 200:
            logger.warning(
                f"ElevenLabs TTS HTTP {response.status_code}: {(response.text or '')[:180]}"
            )
            return False

        raw_mp3.write_bytes(response.content)
        if not raw_mp3.exists() or raw_mp3.stat().st_size < 1000:
            logger.warning("ElevenLabs TTS: empty audio")
            raw_mp3.unlink(missing_ok=True)
            return False

        success = _apply_audio_filters(raw_mp3, output_mp3)
        raw_mp3.unlink(missing_ok=True)
        if success:
            logger.info("✅ ElevenLabs TTS generated")
        return success
    except Exception as exc:
        logger.warning(f"ElevenLabs TTS error: {exc}")
        raw_mp3.unlink(missing_ok=True)
        return False


def _google_cloud_tts(text: str, output_mp3: Path) -> bool:
    """Generate TTS via Google Cloud Text-to-Speech API."""
    if not settings.use_google_tts:
        return False

    try:
        from google.api_core.client_options import ClientOptions
        from google.cloud import texttospeech
        from google.oauth2 import service_account
    except ImportError:
        logger.debug("google-cloud-texttospeech not installed; skipping Google TTS")
        return False

    clean_text = (text or "").strip()
    if not clean_text:
        return False

    raw_mp3 = output_mp3.with_name(f"google_raw_{output_mp3.stem}.mp3")

    try:
        client_kwargs = {}

        api_key = (settings.google_tts_api_key or "").strip()
        if api_key:
            client_kwargs["client_options"] = ClientOptions(api_key=api_key)

        credentials_cfg = (settings.google_tts_service_account_json or "").strip()
        if credentials_cfg:
            credentials_path = settings.resolved_google_tts_service_account_path()
            if not credentials_path.exists():
                logger.warning(f"Google TTS service account file not found: {credentials_path}")
                return False
            client_kwargs["credentials"] = service_account.Credentials.from_service_account_file(
                str(credentials_path)
            )

        client = texttospeech.TextToSpeechClient(**client_kwargs)

        voice_name = (settings.google_tts_voice_name or "").strip()
        language_code = (settings.google_tts_language_code or "es-US").strip()

        if voice_name:
            voice = texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name,
            )
        else:
            voice = texttospeech.VoiceSelectionParams(language_code=language_code)

        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=max(0.25, min(4.0, float(settings.google_tts_speaking_rate))),
            pitch=max(-20.0, min(20.0, float(settings.google_tts_pitch))),
        )

        response = client.synthesize_speech(
            request={
                "input": texttospeech.SynthesisInput(text=clean_text),
                "voice": voice,
                "audio_config": audio_config,
            },
            timeout=max(5, int(settings.google_tts_timeout_seconds or 45)),
        )

        raw_mp3.write_bytes(response.audio_content or b"")
        if not raw_mp3.exists() or raw_mp3.stat().st_size < 1000:
            logger.warning("Google TTS produced empty audio")
            raw_mp3.unlink(missing_ok=True)
            return False

        success = _apply_audio_filters(raw_mp3, output_mp3)
        raw_mp3.unlink(missing_ok=True)
        if success:
            logger.info("✅ Google Cloud TTS generated")
        return success
    except Exception as exc:
        msg = str(exc)
        logger.warning(f"Google Cloud TTS error: {msg[:180]}")
        raw_mp3.unlink(missing_ok=True)
        return False


def _gemini_tts(text: str, output_mp3: Path, voice: str) -> bool:
    """Generate TTS via Gemini API con rotación de las 4 keys."""
    all_keys = settings.get_gemini_keys()
    if not all_keys:
        logger.warning("Gemini TTS: no keys configured")
        return False

    model_name = (settings.gemini_tts_model or "gemini-2.5-flash-preview-tts").strip()

    for key_idx, api_key in enumerate(all_keys):
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/"
                f"models/{model_name}:generateContent"
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
                    # WhisperX is the primary subtitle path in this pipeline.
                    # Skip a second websocket request to Edge when WhisperX is enabled.
                    if settings.use_whisperx:
                        vtt_path.write_text("WEBVTT\n\n", encoding="utf-8")
                        return tmp_wav

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
        msg = str(e)
        if "403" in msg or "Invalid response status" in msg:
            _mark_edge_temporarily_disabled(msg)
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


def clean_tts_text(text: str) -> str:
    """Clean text for TTS input — strip HTML, special chars, normalize whitespace."""
    import re
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r'[{}\\[\\]|\\\\^~*_#@"]', " ", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace(", ", ", ").replace(". ", ". ")
    return text.strip()


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds using mutagen for exact precision."""
    try:
        from mutagen import File
        audio = File(audio_path)
        if audio and hasattr(audio.info, "length"):
            return float(audio.info.length)
        else:
            logger.warning(f"Mutagen could not read length of {audio_path}, falling back to ffprobe")
    except ImportError:
        logger.warning("mutagen not installed, falling back to ffprobe")
    except Exception as e:
        logger.warning(f"mutagen failed for {audio_path}: {e}")
        
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
