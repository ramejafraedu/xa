"""Image Generator — Pollinations AI + Leonardo fallback.

Replaces n8n nodes: 🖼️ Pollinations Imagen + 🎨 Leonardo.ai Fallback + ✅ Sinc Imagen.
Generates 4 strategic images per video (intro, cliffhanger, peak, payoff).
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from services.http_client import download_file, request_with_retry


# Position-specific prompts (identical to MASTER V13 ✅ Sinc Imagen)
POSITION_EXTRAS = {
    1: "opening frame, premium intro, bold focal framing",
    2: "post cliffhanger frame, tension rise, dramatic atmosphere",
    3: "peak tension frame, cinematic contrast, emotional intensity",
    4: "final payoff frame, aspirational finish, call to action visual",
}


def generate_images(
    prompt_base: str,
    visual_nicho: str,
    ab_variant: str,
    timestamp: int,
    temp_dir: Path,
    count: int = 4,
) -> list[Path]:
    """Generate AI images for the video.

    Returns list of successfully generated image paths.
    """
    base_style = (
        "cinematic vertical key art 9:16, high contrast lighting, "
        "clean focal subject, dramatic depth, subtle film grain, "
        "no text, no watermark, no logo"
    )

    style_ab = (
        "dynamic dutch angle, kinetic motion accents"
        if ab_variant == "B"
        else "centered composition, bold foreground separation"
    )

    results = []

    for idx in range(1, count + 1):
        extra = POSITION_EXTRAS.get(idx, "")
        full_prompt = ", ".join(filter(None, [
            prompt_base, visual_nicho, base_style, style_ab, extra
        ]))

        output = temp_dir / f"imagen_{idx}_{timestamp}.jpg"

        # Try Pollinations first
        if _download_pollinations(full_prompt, output):
            results.append(output)
            logger.info(f"Image {idx}/4 OK (Pollinations)")
            continue

        # Fallback: Leonardo.ai
        if settings.leonardo_api_key and _download_leonardo(full_prompt, output):
            results.append(output)
            logger.info(f"Image {idx}/4 OK (Leonardo)")
            continue

        logger.warning(f"Image {idx}/4 FAILED")

    # Copy image 1 as legacy filename
    if results:
        legacy = temp_dir / f"imagen_{timestamp}.jpg"
        if not legacy.exists():
            import shutil
            shutil.copy2(results[0], legacy)

    logger.info(f"Images generated: {len(results)}/{count}")
    return results


def _download_pollinations(prompt: str, output: Path) -> bool:
    """Download image from Pollinations API."""
    try:
        encoded = urllib.parse.quote(prompt)
        url = (
            f"{settings.pollinations_base}/prompt/{encoded}"
            f"?width=1080&height=1920&model=flux&nologo=true"
        )
        return download_file(url, output, timeout=45)
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
            "width": 576,
            "height": 1024,
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
