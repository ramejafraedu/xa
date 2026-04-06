"""Telegram notifications — success + error messages.

Replaces n8n nodes: 📲 Telegram Notificar + 🚨 Telegram Error Calidad.
"""
from __future__ import annotations

from loguru import logger

from config import settings
from models.content import PipelineResult
from services.http_client import request_with_retry


def notify_success(result: PipelineResult, drive_link: str = "N/A") -> bool:
    """Send success notification to Telegram."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False

    text = (
        f"✅ VIDEO FACTORY V14 — Video listo\n\n"
        f"📁 Nicho: {result.nicho_slug}\n"
        f"📝 Titulo: {result.titulo}\n"
        f"🎬 Archivo: {result.video_path.split('/')[-1] if '/' in result.video_path else result.video_path.split(chr(92))[-1]}\n\n"
        f"🔥 Viral: *{result.viral_score}* | Hook: *{result.hook_score}*\n"
        f"🅰️ AB: *{result.ab_variant}* | Calidad: *{result.quality_score}*\n"
        f"📊 D: *{result.block_scores.desarrollo}* | C: *{result.block_scores.cierre}*\n"
        f"⏱️ Duración: {result.duration_seconds:.1f}s\n"
        f"🔄 Healing attempts: {len(result.healing_attempts)}\n\n"
        f"📂 [Drive]({drive_link})"
    )

    return _send_message(text)


def notify_error(result: PipelineResult) -> bool:
    """Send error notification to Telegram."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False

    text = (
        f"🚨 VIDEO FACTORY V14 ERROR\n\n"
        f"📁 Nicho: {result.nicho_slug}\n"
        f"📝 Titulo: {result.titulo or 'N/A'}\n"
        f"⚠️ Etapa: {result.error_stage}\n"
        f"❌ Detalle: {result.error_message[:200]}\n\n"
        f"Hook: {result.block_scores.hook} | "
        f"Desarrollo: {result.block_scores.desarrollo} | "
        f"Cierre: {result.block_scores.cierre} | "
        f"Global: {result.quality_score}\n"
        f"🔄 Healing attempts: {len(result.healing_attempts)}\n"
        f"TS: {result.timestamp}"
    )

    return _send_message(text)


def notify_review(result: PipelineResult) -> bool:
    """Send review-needed notification (quality below threshold after healing)."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False

    text = (
        f"⚠️ VIDEO FACTORY V14 — Requiere revisión\n\n"
        f"📁 Nicho: {result.nicho_slug}\n"
        f"📝 Titulo: {result.titulo}\n"
        f"📊 Calidad: {result.quality_score} (threshold: 7.5)\n"
        f"🔄 Healing attempts: {len(result.healing_attempts)}\n"
        f"📂 Guardado en: review_manual/\n"
        f"TS: {result.timestamp}"
    )

    return _send_message(text)


def _send_message(text: str) -> bool:
    """Send a message via Telegram Bot API."""
    try:
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        response = request_with_retry(
            "POST", url,
            json_data={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
            max_retries=2,
            timeout=15,
        )
        if response.status_code < 400:
            logger.debug("Telegram notification sent")
            return True
        logger.warning(f"Telegram send failed: {response.status_code}")
        return False
    except Exception as e:
        logger.warning(f"Telegram error: {e}")
        return False
