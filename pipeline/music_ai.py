"""Music AI — Generate music tracks via Gemini Lyria 3.

Generates custom instrumental tracks matching the video mood,
with Pixabay/Jamendo as fallback.

MODULE CONTRACT:
  Input:  mood/genre string + duration + config
  Output: Path to MP3 file

Provider hierarchy:
  1. Lyria 3 (Gemini API) → custom AI music
  2. Pixabay (existing) → royalty-free fallback
  3. Jamendo (existing) → secondary fallback
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import settings
from services.http_client import download_file, request_with_retry


_lyria_cooldown_until = 0.0
_suno_cooldown_until = 0.0


def _rotated_gemini_keys() -> list[tuple[int, str]]:
    """Return Gemini keys rotated from current round-robin pointer."""
    keys = settings.get_gemini_keys()
    if not keys:
        return []

    indexed_keys = list(enumerate(keys, start=1))
    first_key = settings.next_gemini_key()
    if not first_key:
        return indexed_keys

    start_idx = 0
    for idx, (_slot, key) in enumerate(indexed_keys):
        if key == first_key:
            start_idx = idx
            break

    return indexed_keys[start_idx:] + indexed_keys[:start_idx]


def generate_music_ai(
    mood: str,
    output_path: Path,
    duration_seconds: float = 60,
    nicho: str = "",
) -> bool:
    """Generate music using Lyria 3 via Gemini API.

    Args:
        mood: Music mood/genre description.
        output_path: Where to save the generated audio.
        duration_seconds: Target duration.
        nicho: Niche for style context.

    Returns:
        True if successful.
    """
    global _lyria_cooldown_until

    gemini_keys = _rotated_gemini_keys()
    if not gemini_keys:
        logger.debug("No GEMINI_API_KEY — skipping Lyria")
        return False

    if not settings.use_lyria_music:
        logger.debug("Lyria music disabled in config")
        return False

    if not settings.provider_allowed("lyria"):
        logger.info("Lyria music skipped by provider policy")
        return False

    if time.time() < _lyria_cooldown_until:
        return False

    # Idempotency check
    if output_path.exists() and output_path.stat().st_size > 10000:
        logger.debug(f"Music already exists: {output_path.name}")
        return True

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-genai not installed. Run: pip install google-genai")
        return False

    prompt = _build_music_prompt(mood, duration_seconds, nicho)
    logger.info(f"🎵 Lyria 3: Generating music — {mood}")

    last_error = ""
    for key_slot, api_key in gemini_keys:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="lyria-3-generate-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                ),
            )

            if response and response.candidates:
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "inline_data") and part.inline_data:
                        audio_data = part.inline_data.data
                        if audio_data:
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            output_path.write_bytes(audio_data)

                            if output_path.stat().st_size > 5000:
                                logger.info(
                                    "✅ Lyria music saved: "
                                    f"{output_path.name} ({output_path.stat().st_size // 1024}KB) "
                                    f"key#{key_slot}"
                                )
                                return True

            last_error = f"Lyria key#{key_slot}: no audio data in response"
            logger.debug(last_error)
        except Exception as exc:
            err_text = str(exc)
            err_lower = err_text.lower()
            last_error = err_text

            if any(token in err_lower for token in ("not_found", "404", "is not found", "unsupported")):
                _lyria_cooldown_until = time.time() + (6 * 3600)
                logger.warning("Lyria model unavailable (404). Cooling down for 6h.")
                return False

            if any(token in err_lower for token in ("resource_exhausted", "quota", "429", "rate")):
                logger.debug(f"Lyria key#{key_slot} quota/rate hit, rotating")
                continue

            logger.warning(f"Lyria music generation failed on key#{key_slot}: {err_text}")

    if last_error:
        logger.warning(f"Lyria music generation failed for all keys: {last_error[:220]}")
    return False


def generate_music_suno(
    mood: str,
    output_path: Path,
    duration_seconds: float = 60,
    nicho: str = "",
    poll_seconds: int = 2,
    max_polls: int = 40,
) -> bool:
    """Generate (or reuse cached) music using Suno API."""
    global _suno_cooldown_until

    if not settings.suno_api_key:
        logger.debug("No SUNO_API_KEY — skipping Suno")
        return False

    if not settings.use_suno_music:
        logger.debug("Suno music disabled in config")
        return False

    if not settings.provider_allowed("suno"):
        logger.info("Suno music skipped by provider policy")
        return False

    if time.time() < _suno_cooldown_until:
        return False

    settings.ensure_dirs()
    cache_path = _suno_cache_path(mood, duration_seconds, nicho)
    if _copy_cached_music(cache_path, output_path):
        logger.info(f"♻️ Suno cache reused: {cache_path.name}")
        return True

    if output_path.exists() and output_path.stat().st_size > 10000:
        _store_cached_music(output_path, cache_path)
        return True

    prompt = _build_music_prompt(mood, duration_seconds, nicho)
    payload = {
        "prompt": prompt,
        "tags": mood,
        "title": f"{(nicho or 'video').strip()} instrumental",
        "instrumental": True,
        "make_instrumental": True,
        "duration": int(max(8, min(180, round(duration_seconds)))),
    }
    headers = {
        "authorization": f"Bearer {settings.suno_api_key}",
        "x-api-key": settings.suno_api_key,
        "content-type": "application/json",
    }

    try:
        logger.info(f"🎵 Suno: generating music — {mood}")
        response = request_with_retry(
            "POST",
            settings.suno_api_url,
            headers=headers,
            json_data=payload,
            max_retries=2,
            timeout=120,
        )

        if response.status_code >= 400:
            logger.warning(f"Suno generation failed: HTTP {response.status_code}")
            if response.status_code in (401, 403):
                _suno_cooldown_until = time.time() + (30 * 60)
            return False

        data: dict[str, Any]
        try:
            data = response.json() or {}
        except Exception:
            logger.warning("Suno generation returned invalid JSON")
            return False

        audio_url = _extract_audio_url(data)

        if not audio_url:
            task_id = _extract_task_id(data)
            if task_id:
                audio_url = _poll_suno_audio_url(
                    task_id=task_id,
                    headers=headers,
                    poll_seconds=poll_seconds,
                    max_polls=max_polls,
                )

        if not audio_url:
            logger.warning("Suno response did not include an audio URL")
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not download_file(audio_url, output_path, timeout=120):
            logger.warning("Suno audio download failed")
            return False

        _store_cached_music(output_path, cache_path)
        logger.info(f"✅ Suno music saved: {output_path.name} ({output_path.stat().st_size // 1024}KB)")
        return True
    except Exception as exc:
        logger.warning(f"Suno music generation failed: {exc}")
        return False


def _build_music_prompt(mood: str, duration: float, nicho: str) -> str:
    """Build a descriptive prompt for music generation."""
    nicho_styles = {
        "finanzas": "modern electronic, corporate, upbeat, confident",
        "historia": "epic orchestral, cinematic, dramatic, historical",
        "curiosidades": "quirky electronic, playful, wonder, upbeat tempo",
        "historias_reddit": "dark cinematic tension, suspense pulses, emotional builds, subtle impacts",
        "ia_herramientas": "modern tech groove, clean electronic, optimistic momentum, startup energy",
    }

    style = nicho_styles.get(nicho.lower(), mood)

    return (
        f"Generate a {int(duration)}-second instrumental music track. "
        f"Style: {style}. Mood: {mood}. "
        f"No vocals, no lyrics. "
        f"Suitable as background music for a short-form video. "
        f"Clean mix, moderate tempo, professional quality."
    )


def fetch_music_with_fallback(
    mood: str,
    output_path: Path,
    duration_seconds: float = 60,
    nicho: str = "",
) -> bool:
    """Try Lyria 3 first, then fall back to Pixabay/Jamendo.

    This is the main entry point — replaces direct calls to fetch_music().
    """
    success, _source = fetch_music_with_fallback_source(
        mood,
        output_path,
        duration_seconds=duration_seconds,
        nicho=nicho,
    )
    return success


from services.provider_cascade import ProviderCascade

_music_cascade = None

def _get_music_cascade() -> ProviderCascade:
    global _music_cascade
    if _music_cascade is None:
        _music_cascade = ProviderCascade(
            name="music",
            state_dir=settings.temp_dir,
            cooldown_seconds=settings.provider_cascade_cooldown_seconds,
            max_consecutive_failures=settings.provider_cascade_max_failures,
        )
    return _music_cascade


def fetch_music_with_fallback_source(
    mood: str,
    output_path: Path,
    duration_seconds: float = 60,
    nicho: str = "",
    provider_order: list[str] | None = None,
) -> tuple[bool, str]:
    """Try providers via Cascade and return (success, source)."""
    if not settings.enable_provider_cascade:
        return _fetch_music_with_fallback_source_legacy(
            mood, output_path, duration_seconds, nicho, provider_order
        )
        
    cascade = _get_music_cascade()
    
    def wrap_suno():
        return generate_music_suno(mood, output_path, duration_seconds, nicho)
        
    def wrap_lyria():
        return generate_music_ai(mood, output_path, duration_seconds, nicho)
        
    def wrap_pixabay():
        from pipeline.music import fetch_music_by_order
        ok, _ = fetch_music_by_order(mood, output_path, provider_order=["pixabay"])
        return ok
        
    def wrap_jamendo():
        from pipeline.music import fetch_music_by_order
        ok, _ = fetch_music_by_order(mood, output_path, provider_order=["jamendo"])
        return ok

    # Register providers
    suno_allowed = bool(settings.suno_api_key) and settings.use_suno_music and settings.provider_allowed("suno")
    cascade.register("suno", wrap_suno, tier="premium", base_score=90.0, enabled=suno_allowed)
    
    lyria_allowed = bool(settings.get_gemini_keys()) and settings.use_lyria_music and settings.provider_allowed("lyria")
    cascade.register("lyria", wrap_lyria, tier="freemium", base_score=80.0, enabled=lyria_allowed)
    
    cascade.register("pixabay", wrap_pixabay, tier="free", base_score=60.0, enabled=True)
    cascade.register("jamendo", wrap_jamendo, tier="free", base_score=50.0, enabled=True)
    
    res = cascade.execute()
    if res.success:
        return True, res.provider_name
        
    logger.warning(f"Music generation failed for all providers: {res.error}")
    return False, "none"


def _fetch_music_with_fallback_source_legacy(
    mood: str,
    output_path: Path,
    duration_seconds: float = 60,
    nicho: str = "",
    provider_order: list[str] | None = None,
) -> tuple[bool, str]:
    """Original legacy sequence."""
    if provider_order is None:
        provider_order = ["suno", "lyria", "pixabay", "jamendo"] if settings.suno_api_key else ["lyria", "pixabay", "jamendo"]

    for provider in provider_order:
        if provider == "suno":
            if generate_music_suno(mood, output_path, duration_seconds, nicho):
                return True, "suno"
            continue

        if provider == "lyria":
            if generate_music_ai(mood, output_path, duration_seconds, nicho):
                return True, "lyria"
            continue

        if provider in ("pixabay", "jamendo"):
            try:
                from pipeline.music import fetch_music_by_order

                ok, src = fetch_music_by_order(mood, output_path, provider_order=[provider])
                if ok:
                    return True, src
            except Exception as e:
                logger.debug(f"Music provider {provider} failed: {e}")

    logger.warning("Music generation failed for all providers")
    return False, "none"


def _suno_cache_path(mood: str, duration_seconds: float, nicho: str) -> Path:
    import random
    # Maintain a pool of up to 3 variations per mood+niche+duration
    # This guarantees the music isn't exactly the same every single time.
    pool_index = random.randint(1, 3)
    payload = {
        "provider": "suno",
        "mood": str(mood or "").strip().lower(),
        "nicho": str(nicho or "").strip().lower(),
        "duration": int(max(8, min(180, round(duration_seconds)))),
        "pool_index": pool_index,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:18]
    return settings.music_cache_dir / f"suno_{digest}.mp3"


def _copy_cached_music(cache_path: Path, output_path: Path) -> bool:
    if not cache_path.exists() or cache_path.stat().st_size < 10000:
        return False
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_path, output_path)
        return output_path.exists() and output_path.stat().st_size >= 10000
    except Exception:
        return False


def _store_cached_music(output_path: Path, cache_path: Path) -> None:
    if not output_path.exists() or output_path.stat().st_size < 10000:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, cache_path)
    except Exception as exc:
        logger.debug(f"Could not store Suno cache: {exc}")


def _poll_suno_audio_url(
    task_id: str,
    headers: dict[str, str],
    poll_seconds: int,
    max_polls: int,
) -> Optional[str]:
    status_api = (settings.suno_status_api_url or "").strip()
    if not status_api:
        return None

    if "{task_id}" in status_api:
        status_url = status_api.format(task_id=task_id)
    else:
        status_url = f"{status_api.rstrip('/')}/{task_id}"

    for _ in range(max_polls):
        try:
            resp = request_with_retry(
                "GET",
                status_url,
                headers=headers,
                max_retries=1,
                timeout=45,
            )
            if resp.status_code >= 400:
                time.sleep(poll_seconds)
                continue

            data = resp.json() or {}
            audio_url = _extract_audio_url(data)
            if audio_url:
                return audio_url

            status = str((data or {}).get("status", "")).lower()
            if status in {"error", "failed", "cancelled"}:
                return None
        except Exception:
            pass
        time.sleep(poll_seconds)

    return None


def _extract_audio_url(payload: Any) -> Optional[str]:
    urls: list[tuple[int, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if isinstance(value, str) and value.startswith("http"):
                    priority = 0 if any(k in lowered for k in ("audio", "stream", "download", "url")) else 1
                    urls.append((priority, value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not urls:
        return None

    for _, url in sorted(urls, key=lambda x: x[0]):
        lowered = url.lower()
        if any(ext in lowered for ext in (".mp3", ".wav", ".m4a", ".ogg", "audio")):
            return url

    return sorted(urls, key=lambda x: x[0])[0][1]


def _extract_task_id(payload: Any) -> Optional[str]:
    keys = {"task_id", "taskid", "job_id", "jobid", "id", "generation_id"}
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                lowered = str(key).lower()
                if lowered in keys and value:
                    return str(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return None
