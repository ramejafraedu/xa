"""Cross-job alert tracker — trips after N consecutive pipeline failures.

This module is intentionally tiny: it persists a counter (+ last error) in a
JSON file under `settings.temp_dir` and sends a Telegram alert through the
existing `request_with_retry` client when the threshold is crossed.

MODULE CONTRACT:
  record_pipeline_result(success, stage, error, job_id) → None
  peek_state() → dict (for dashboards / tests)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from config import settings
from services.http_client import request_with_retry


_STATE_FILENAME = "alert_state.json"
_DEFAULT_THRESHOLD = 3
_COOLDOWN_SECONDS = 60 * 30  # Only one alert every 30 minutes per tripped window


def _state_path() -> Path:
    return Path(settings.temp_dir) / _STATE_FILENAME


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_successes": 0,
            "last_error": "",
            "last_stage": "",
            "last_job_id": "",
            "last_alert_at": 0,
            "updated_at": 0,
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"alerts: could not read state file — resetting ({exc})")
        return {
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_successes": 0,
            "last_error": "",
            "last_stage": "",
            "last_job_id": "",
            "last_alert_at": 0,
            "updated_at": 0,
        }


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = int(time.time())
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _send_telegram_alert(state: dict[str, Any]) -> bool:
    """Send an alert to Telegram using the shared retry client."""
    token = (settings.telegram_bot_token or "").strip()
    chat_id = (settings.telegram_chat_id or "").strip()
    if not token or not chat_id:
        logger.debug("alerts: Telegram not configured; skipping alert")
        return False

    text = (
        "🚨 VIDEO FACTORY — fallos consecutivos detectados\n\n"
        f"Fallos seguidos: *{state.get('consecutive_failures', 0)}*\n"
        f"Última etapa: {state.get('last_stage') or 'N/A'}\n"
        f"Último job: {state.get('last_job_id') or 'N/A'}\n"
        f"Error: {str(state.get('last_error') or '')[:220]}"
    )

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        response = request_with_retry(
            "POST",
            url,
            json_data={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
            },
            timeout=15.0,
            max_retries=2,
        )
        return bool(response) and response.status_code < 400
    except Exception as exc:
        logger.warning(f"alerts: Telegram send failed: {exc}")
        return False


def record_pipeline_result(
    *,
    success: bool,
    stage: str = "",
    error: str = "",
    job_id: str = "",
    threshold: int = _DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Update the persisted counter and alert on threshold crossings."""
    state = _load_state()

    if success:
        state["consecutive_failures"] = 0
        state["total_successes"] = int(state.get("total_successes", 0)) + 1
        state["last_error"] = ""
        state["last_stage"] = stage or ""
        state["last_job_id"] = job_id or ""
        _save_state(state)
        return state

    state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    state["total_failures"] = int(state.get("total_failures", 0)) + 1
    state["last_error"] = str(error or "")[:500]
    state["last_stage"] = stage or ""
    state["last_job_id"] = job_id or ""

    now = int(time.time())
    should_alert = (
        state["consecutive_failures"] >= max(1, int(threshold))
        and (now - int(state.get("last_alert_at", 0) or 0)) >= _COOLDOWN_SECONDS
    )

    _save_state(state)

    if should_alert:
        logger.warning(
            f"alerts: {state['consecutive_failures']} consecutive failures — "
            "emitting Telegram alert"
        )
        if _send_telegram_alert(state):
            state["last_alert_at"] = now
            _save_state(state)

    return state


def peek_state() -> dict[str, Any]:
    """Return a snapshot of the current alert state (safe for dashboards)."""
    return _load_state()
