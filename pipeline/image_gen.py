"""Image Generator — Stock-first (Pexels/Pixabay) + AI fallback.

Replaces n8n nodes: stock image APIs + 🖼️ Pollinations + 🎨 Leonardo fallback.
Generates 4 strategic images per video (intro, cliffhanger, peak, payoff).
"""
from __future__ import annotations

import hashlib
import re
import shutil
import time
import urllib.parse
import random
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import download_file, request_with_retry

try:
    from state_manager import get_asset_history, AssetHistory
except Exception:  # pragma: no cover
    get_asset_history = None
    AssetHistory = None

try:
    from styles.playbook_loader import load_playbook as _load_style_playbook
except Exception:  # pragma: no cover
    _load_style_playbook = None


# Position-specific prompts (identical to MASTER V13 ✅ Sinc Imagen)
POSITION_EXTRAS = {
    1: "opening frame, premium intro, bold focal framing",
    2: "post cliffhanger frame, tension rise, dramatic atmosphere",
    3: "peak tension frame, cinematic contrast, emotional intensity",
    4: "final payoff frame, aspirational finish, call to action visual",
}
_pexels_image_rotation_counter: list[int] = [0]

# Accepted image URLs per generation run — used to register anti-repeat hashes.
_accepted_image_urls: list[tuple[str, str, str]] = []  # (provider, url, prompt)


def _hash_url(url: str, output: Optional[Path] = None) -> str:
    """SHA-256 short hash for an image URL (+ optional local bytes)."""
    if AssetHistory is not None:
        try:
            return AssetHistory.compute_hash(url=url or "", file_path=output)
        except Exception:
            pass
    return hashlib.sha256((url or "").strip().lower().split("?")[0].encode()).hexdigest()[:32]


def _recent_image_hashes() -> set[str]:
    if get_asset_history is None:
        return set()
    try:
        return get_asset_history().get_recent_hashes(kind="image", n_jobs=50)
    except Exception:
        return set()


def _style_modifiers(style_playbook: Optional[str]) -> str:
    """Return visual-style modifiers (palette, motion, typography tone) from a style playbook."""
    if not style_playbook or _load_style_playbook is None:
        return ""
    try:
        data = _load_style_playbook(str(style_playbook))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    parts: list[str] = []
    visual = data.get("visual_language") or {}
    palette = (visual.get("color_palette") if isinstance(visual, dict) else None) or []
    if isinstance(palette, list) and palette:
        colors = [str(c.get("hex") or c.get("name") or c) for c in palette[:3] if c]
        colors = [c for c in colors if c]
        if colors:
            parts.append("palette: " + ", ".join(colors))
    mood = (visual.get("mood") if isinstance(visual, dict) else None)
    if mood:
        parts.append(f"mood: {mood}")
    motion = data.get("motion") or {}
    style_note = (motion.get("style") if isinstance(motion, dict) else None)
    if style_note:
        parts.append(f"motion: {style_note}")
    identity = data.get("identity") or {}
    style_name = (identity.get("name") if isinstance(identity, dict) else None) or style_playbook
    parts.append(f"style: {style_name}")
    return ", ".join(parts)


def generate_images(
    prompt_base: str,
    visual_nicho: str,
    ab_variant: str,
    timestamp: int,
    temp_dir: Path,
    count: int = 4,
    provider_order: Optional[list[str]] = None,
    prefer_stock_images: Optional[bool] = None,
    cache_ttl_days: Optional[int] = None,
    enable_cache: Optional[bool] = None,
    job_id: Optional[str] = None,
    style_playbook: Optional[str] = None,
    scene_texts: Optional[list[str]] = None,
) -> list[Path]:
    """Generate images for the video (stock-first when enabled).

    Returns list of successfully generated image paths.
    """
    results, _stats = generate_images_with_stats(
        prompt_base,
        visual_nicho,
        ab_variant,
        timestamp,
        temp_dir,
        count=count,
        provider_order=provider_order,
        prefer_stock_images=prefer_stock_images,
        cache_ttl_days=cache_ttl_days,
        enable_cache=enable_cache,
        job_id=job_id,
        style_playbook=style_playbook,
        scene_texts=scene_texts,
    )
    return results


def generate_images_with_stats(
    prompt_base: str,
    visual_nicho: str,
    ab_variant: str,
    timestamp: int,
    temp_dir: Path,
    count: int = 4,
    provider_order: Optional[list[str]] = None,
    prefer_stock_images: Optional[bool] = None,
    cache_ttl_days: Optional[int] = None,
    enable_cache: Optional[bool] = None,
    job_id: Optional[str] = None,
    style_playbook: Optional[str] = None,
    scene_texts: Optional[list[str]] = None,
) -> tuple[list[Path], dict[str, dict[str, int]]]:
    """Generate images and return per-provider stats.

    Stats shape: {provider: {"ok": int, "fail": int}}
    """
    # Reset accepted-URL buffer for this run (used for anti-repeat registration).
    _accepted_image_urls.clear()
    # Pre-load recent hashes once per run.
    recent_hashes = _recent_image_hashes()

    base_style = (
        "cinematic vertical key art 9:16, high contrast lighting, "
        "clean focal subject, dramatic depth, subtle film grain, "
        "no text, no watermark, no logo"
    )
    if settings.gemini_everywhere_mode:
        base_style = (
            f"{base_style}, energetic visual storytelling, vibrant color separation, "
            "joyful cinematic mood, premium editorial finish"
        )

    style_ab = (
        "dynamic dutch angle, kinetic motion accents"
        if ab_variant == "B"
        else "centered composition, bold foreground separation"
    )

    # V16.1: style playbook modifiers (palette, mood, motion) for niche consistency.
    style_extra = _style_modifiers(style_playbook) if style_playbook else ""

    stock_first = settings.prefer_stock_images if prefer_stock_images is None else bool(prefer_stock_images)
    cache_enabled = settings.enable_image_cache if enable_cache is None else bool(enable_cache)
    try:
        ttl_days = int(settings.media_cache_ttl_days if cache_ttl_days is None else cache_ttl_days)
    except (TypeError, ValueError):
        ttl_days = int(settings.media_cache_ttl_days)
    ttl_days = max(0, ttl_days)

    if provider_order is None:
        provider_order = ["pexels", "pixabay", "leonardo", "pollinations"] if stock_first else ["leonardo", "pollinations", "pexels", "pixabay"]

    settings.ensure_dirs()
    ttl_seconds = ttl_days * 86400
    stats = {
        "pexels": {"ok": 0, "fail": 0},
        "pixabay": {"ok": 0, "fail": 0},
        "leonardo": {"ok": 0, "fail": 0},
        "pollinations": {"ok": 0, "fail": 0},
    }
    results: list[Path] = []
    failed_indices: list[int] = []

    for idx in range(1, count + 1):
        extra = POSITION_EXTRAS.get(idx, "")
        # Per-scene phrase from the real script (if available).
        scene_phrase = ""
        if scene_texts:
            try:
                scene_phrase = (scene_texts[idx - 1] or "").strip()[:220]
            except IndexError:
                scene_phrase = ""
        full_prompt = ", ".join(filter(None, [
            prompt_base, visual_nicho, scene_phrase, style_extra,
            base_style, style_ab, extra,
        ]))

        output = temp_dir / f"imagen_{idx}_{timestamp}.jpg"
        generated = False
        cache_key = hashlib.sha1(full_prompt.encode("utf-8", errors="ignore")).hexdigest()[:20]
        cache_file = settings.image_cache_dir / f"img_{cache_key}.jpg"

        # Reuse cached image when still fresh.
        if cache_enabled and _is_fresh_file(cache_file, ttl_seconds):
            try:
                shutil.copy2(cache_file, output)
                results.append(output)
                logger.info(f"Image {idx}/{count} CACHE HIT")
                continue
            except Exception as e:
                logger.debug(f"Image cache copy failed, generating fresh: {e}")

        for provider in provider_order:
            normalized_provider = str(provider or "").strip().lower()

            if normalized_provider in {"pexels", "pexels_image"}:
                if not settings.pexels_keys:
                    continue
                if not settings.provider_allowed("pexels", usage="media"):
                    logger.debug("Pexels image skipped by provider policy")
                    continue

                if _download_pexels_image(full_prompt, output, recent_hashes):
                    results.append(output)
                    if cache_enabled:
                        _save_image_cache(output, cache_file)
                    stats["pexels"]["ok"] += 1
                    logger.info(f"Image {idx}/{count} OK (Pexels Stock)")
                    generated = True
                    break

                stats["pexels"]["fail"] += 1
                continue

            if normalized_provider in {"pixabay", "pixabay_image"}:
                if not settings.pixabay_api_key:
                    continue
                if not settings.provider_allowed("pixabay", usage="media"):
                    logger.debug("Pixabay image skipped by provider policy")
                    continue

                if _download_pixabay_image(full_prompt, output, recent_hashes):
                    results.append(output)
                    if cache_enabled:
                        _save_image_cache(output, cache_file)
                    stats["pixabay"]["ok"] += 1
                    logger.info(f"Image {idx}/{count} OK (Pixabay Stock)")
                    generated = True
                    break

                stats["pixabay"]["fail"] += 1
                continue

            if normalized_provider == "leonardo":
                if not settings.leonardo_api_key:
                    continue
                if not settings.provider_allowed("leonardo", usage="media"):
                    logger.debug("Leonardo skipped by provider policy")
                    continue

                if _download_leonardo(full_prompt, output):
                    results.append(output)
                    if cache_enabled:
                        _save_image_cache(output, cache_file)
                    stats["leonardo"]["ok"] += 1
                    logger.info(f"Image {idx}/{count} OK (Leonardo)")
                    generated = True
                    break

                stats["leonardo"]["fail"] += 1
                continue

            if normalized_provider == "pollinations":
                if _download_pollinations(full_prompt, output):
                    results.append(output)
                    if cache_enabled:
                        _save_image_cache(output, cache_file)
                    stats["pollinations"]["ok"] += 1
                    logger.info(f"Image {idx}/{count} OK (Pollinations Fallback)")
                    generated = True
                    break

                stats["pollinations"]["fail"] += 1

        if not generated:
            logger.warning(f"Image {idx}/{count} FAILED")
            failed_indices.append(idx)

    # Keep scene/image coverage stable even when some providers fail.
    # Avoid filling all failed slots with the exact same image (causes repeated visuals).
    if failed_indices and results:
        seed_pool = list(results)
        # Limit number of direct duplicates per source to avoid slideshow of the same image.
        max_dup_per_source = 2
        dup_counts: dict[str, int] = {}

        for offset, failed_idx in enumerate(failed_indices):
            source = seed_pool[offset % len(seed_pool)]
            source_key = str(source)
            dup_counts.setdefault(source_key, 0)

            target = temp_dir / f"imagen_{failed_idx}_{timestamp}.jpg"

            # If we've already duplicated this source enough times, try to produce
            # a light variation (if Pillow is available). If that fails, skip adding
            # further duplicates to avoid repeating the exact same frame many times.
            if dup_counts[source_key] >= max_dup_per_source:
                try:
                    from PIL import Image, ImageFilter

                    img = Image.open(source)
                    # Apply a tiny blur to create a perceptible variation without changing
                    # semantic content (keeps continuity but avoids exact duplicates).
                    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
                    img.save(target, quality=85)
                    results.append(target)
                    dup_counts[source_key] += 1
                    logger.info(f"Image {failed_idx}/{count} FILLED with lightweight variation")
                    continue
                except Exception:
                    logger.debug("Pillow not available or variation failed; skipping extra duplicate")
                    # Do not create more duplicates if variation isn't possible
                    break

            try:
                shutil.copy2(source, target)
                results.append(target)
                dup_counts[source_key] += 1
                logger.info(f"Image {failed_idx}/{count} FILLED from successful fallback")
            except Exception as e:
                logger.debug(f"Image fallback fill skipped ({failed_idx}): {e}")

    # Copy image 1 as legacy filename (safe — fixes WinError 2)
    if results:
        legacy = temp_dir / f"imagen_{timestamp}.jpg"
        if not legacy.exists() and results[0].exists():
            try:
                shutil.copy2(results[0], legacy)
            except (FileNotFoundError, OSError) as e:
                logger.debug(f"Legacy image copy skipped: {e}")

    # V16.1: register accepted images in global asset_history for anti-repeat.
    if job_id and _accepted_image_urls and get_asset_history is not None:
        try:
            history = get_asset_history()
            for provider, url, prm in _accepted_image_urls:
                h = _hash_url(url)
                history.add_asset(kind="image", asset_hash=h, job_id=job_id,
                                  url=url, prompt=(prm or "")[:200])
        except Exception as exc:
            logger.debug(f"[image_gen] asset_history registration failed: {exc}")

    logger.info(
        f"Images generated: {len(results)}/{count} "
        f"(anti-repeat + niche-style applied)"
    )
    return results, stats


def _is_fresh_file(path: Path, ttl_seconds: int) -> bool:
    if not path.exists() or path.stat().st_size <= 1000:
        return False
    if ttl_seconds <= 0:
        return True
    age = time.time() - path.stat().st_mtime
    return age <= ttl_seconds


def _save_image_cache(source: Path, cache_file: Path) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, cache_file)
    except Exception as e:
        logger.debug(f"Image cache save skipped: {e}")


def _stock_query(prompt: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9áéíóúñÁÉÍÓÚÑ\s]", " ", str(prompt or ""))
    words = [w for w in text.split() if len(w) >= 3]
    if not words:
        return "cinematic portrait"
    return " ".join(words[:6])


def _rotated_pexels_image_keys(keys: list[str]) -> list[str]:
    if not keys:
        return []
    start = _pexels_image_rotation_counter[0] % len(keys)
    _pexels_image_rotation_counter[0] += 1
    return keys[start:] + keys[:start]


def _download_pexels_image(
    prompt: str,
    output: Path,
    recent_hashes: Optional[set[str]] = None,
) -> bool:
    query = urllib.parse.quote(_stock_query(prompt))
    url = f"https://api.pexels.com/v1/search?query={query}&orientation=portrait&per_page=10"
    blocklist = recent_hashes or set()

    for key in _rotated_pexels_image_keys(settings.pexels_keys):
        try:
            response = request_with_retry(
                "GET",
                url,
                headers={"Authorization": key},
                max_retries=1,
                timeout=20,
            )

            if response.status_code == 429:
                continue
            if response.status_code >= 400:
                continue

            photos = response.json().get("photos", [])
            import time
            import random
            random.seed(time.time())
            random.shuffle(photos)
            for photo in photos:
                src = photo.get("src", {}) if isinstance(photo, dict) else {}
                image_url = src.get("large2x") or src.get("large") or src.get("original")
                if not image_url:
                    continue
                # Anti-repeat: skip if already used in last 50 jobs.
                if _hash_url(image_url) in blocklist:
                    continue
                if download_file(image_url, output, timeout=45):
                    _accepted_image_urls.append(("pexels", image_url, prompt))
                    return True
        except Exception:
            continue

    return False


def _download_pixabay_image(
    prompt: str,
    output: Path,
    recent_hashes: Optional[set[str]] = None,
) -> bool:
    if not settings.pixabay_api_key:
        return False

    query = urllib.parse.quote(_stock_query(prompt))
    url = (
        f"https://pixabay.com/api/?key={settings.pixabay_api_key}"
        f"&q={query}&orientation=vertical&image_type=photo&per_page=10&safesearch=true"
    )
    blocklist = recent_hashes or set()

    try:
        response = request_with_retry("GET", url, max_retries=1, timeout=20)
        if response.status_code >= 400:
            return False

        hits = response.json().get("hits", [])
        import time
        import random
        random.seed(time.time())
        random.shuffle(hits)
        for item in hits:
            image_url = item.get("largeImageURL") or item.get("webformatURL")
            if not image_url:
                continue
            if _hash_url(image_url) in blocklist:
                continue
            if download_file(image_url, output, timeout=45):
                _accepted_image_urls.append(("pixabay", image_url, prompt))
                return True
    except Exception:
        return False

    return False


def _download_pollinations(prompt: str, output: Path) -> bool:
    """Download image from Pollinations API."""
    try:
        encoded = urllib.parse.quote(prompt)
        import time
        import random
        random.seed(time.time())
        url = (
            f"{settings.pollinations_base}/prompt/{encoded}"
            f"?width=1080&height=1920&model=flux&nologo=true&seed={random.randint(1, 999999)}"
        )
        ok = download_file(url, output, timeout=45)
        if ok:
            _accepted_image_urls.append(("pollinations", url, prompt))
        return ok
    except Exception as e:
        logger.debug(f"Pollinations error: {e}")
        return False


def _download_leonardo(prompt: str, output: Path) -> bool:
    """Generate image via Leonardo.ai API."""
    try:
        url = "https://cloud.leonardo.ai/api/rest/v1/generations"
        headers = {
            "Authorization": f"Bearer {settings.leonardo_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": prompt[:1000],
            "modelId": "b24e16ff-06e3-43eb-8d33-4416c2d75876",
            "width": 1080,
            "height": 1920,
            "num_images": 1,
            "public": False,
        }

        response = request_with_retry(
            "POST", url,
            json_data=payload,
            headers=headers,
            max_retries=2,
            timeout=30,
        )

        if response.status_code >= 400:
            return False

        data = response.json()
        gen_id = data.get("sdGenerationJob", {}).get("generationId")
        if not gen_id:
            return False

        # Poll for result (simplified — Leonardo is async)
        import time
        for _ in range(20):
            time.sleep(3)
            check_url = f"https://cloud.leonardo.ai/api/rest/v1/generations/{gen_id}"
            check = request_with_retry("GET", check_url, headers=headers, max_retries=1)
            if check.status_code == 200:
                gen_data = check.json()
                images = gen_data.get("generations_by_pk", {}).get("generated_images", [])
                if images:
                    img_url = images[0].get("url", "")
                    if img_url:
                        return download_file(img_url, output)
            time.sleep(2)

        return False

    except Exception as e:
        logger.debug(f"Leonardo error: {e}")
        return False
