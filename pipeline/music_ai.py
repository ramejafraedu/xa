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

from pathlib import Path

from loguru import logger

from config import settings


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
    if not settings.gemini_api_key:
        logger.debug("No GEMINI_API_KEY — skipping Lyria")
        return False

    if not settings.use_lyria_music:
        logger.debug("Lyria music disabled in config")
        return False

    if not settings.provider_allowed("lyria"):
        logger.info("Lyria music skipped by provider policy")
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

    try:
        # Rotate keys to avoid rate limits
        api_key = settings.next_gemini_key()
        client = genai.Client(api_key=api_key)

        # Build a descriptive prompt
        prompt = _build_music_prompt(mood, duration_seconds, nicho)
        logger.info(f"🎵 Lyria 3: Generating music — {mood}")

        response = client.models.generate_content(
            model="lyria-3-generate-preview",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
            ),
        )

        # Extract audio data
        if response and response.candidates:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    audio_data = part.inline_data.data
                    if audio_data:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_bytes(audio_data)

                        if output_path.stat().st_size > 5000:
                            logger.info(f"✅ Lyria music saved: {output_path.name} ({output_path.stat().st_size // 1024}KB)")
                            return True

        logger.warning("Lyria: no audio data in response")
        return False

    except Exception as e:
        logger.warning(f"Lyria music generation failed: {e}")
        return False


def _build_music_prompt(mood: str, duration: float, nicho: str) -> str:
    """Build a descriptive prompt for music generation."""
    nicho_styles = {
        "finanzas": "modern electronic, corporate, upbeat, confident",
        "historia": "epic orchestral, cinematic, dramatic, historical",
        "curiosidades": "quirky electronic, playful, wonder, upbeat tempo",
        "salud": "calm ambient, wellness, peaceful, nature sounds",
        "recetas": "light acoustic, kitchen vibes, happy, warm",
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


def fetch_music_with_fallback_source(
    mood: str,
    output_path: Path,
    duration_seconds: float = 60,
    nicho: str = "",
    provider_order: list[str] | None = None,
) -> tuple[bool, str]:
    """Try providers in order and return (success, source)."""
    provider_order = provider_order or ["lyria", "pixabay", "jamendo"]

    for provider in provider_order:
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
