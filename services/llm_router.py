"""Unified LLM router: Gemini primary with Azure GPT fallback.

This module centralizes text-generation calls so V14/V15 agents can
prioritize Gemini (with 4-key rotation) and automatically fail over
to Azure Inference GPT models when Gemini is unavailable.
"""
from __future__ import annotations

import json
import time
from typing import Any

from loguru import logger

from config import settings
from services.http_client import request_with_retry

_DEFAULT_GEMINI_CHAT_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3-flash",
]
_gemini_key_cooldowns: dict[int, float] = {}
_gemini_model_cooldowns: dict[str, float] = {}


def _now_ts() -> float:
    return time.time()


def _is_key_cooling_down(key_slot: int) -> bool:
    return _gemini_key_cooldowns.get(key_slot, 0.0) > _now_ts()


def _is_model_cooling_down(model_name: str) -> bool:
    return _gemini_model_cooldowns.get(model_name, 0.0) > _now_ts()


def _set_key_cooldown(key_slot: int) -> None:
    seconds = max(5, int(settings.gemini_key_cooldown_seconds or 25))
    _gemini_key_cooldowns[key_slot] = _now_ts() + seconds


def _set_model_cooldown(model_name: str) -> None:
    seconds = max(60, int(settings.gemini_model_cooldown_seconds or 600))
    _gemini_model_cooldowns[model_name] = _now_ts() + seconds


def _gemini_models_order() -> list[str]:
    configured = settings.get_gemini_chat_models()
    return configured or list(_DEFAULT_GEMINI_CHAT_MODELS)


def _classify_gemini_error(error_text: str) -> str:
    text = (error_text or "").lower()
    if any(token in text for token in ("resource_exhausted", "quota", "429", "rate", "too many requests")):
        return "quota"
    if any(token in text for token in ("not_found", "unsupported", "404", "model is not found", "invalid model")):
        return "model_unavailable"
    return "generic"


def _read_usage_stats() -> dict:
    path = settings.gemini_usage_stats_path
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_usage_stats(data: dict) -> None:
    try:
        settings.ensure_dirs()
        settings.gemini_usage_stats_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _record_gemini_usage(
    model_name: str,
    key_slot: int,
    success: bool,
    latency_ms: int,
    error: str = "",
) -> None:
    """Persist lightweight Gemini usage stats without storing secrets."""
    if not settings.gemini_enable_usage_stats:
        return

    data = _read_usage_stats()
    summary = data.setdefault("summary", {
        "attempts": 0,
        "success": 0,
        "failure": 0,
        "last_success_model": "",
        "last_error": "",
    })
    summary["attempts"] = int(summary.get("attempts", 0)) + 1
    if success:
        summary["success"] = int(summary.get("success", 0)) + 1
        summary["last_success_model"] = model_name
    else:
        summary["failure"] = int(summary.get("failure", 0)) + 1
        if error:
            summary["last_error"] = str(error)[:220]

    key_stats = data.setdefault("keys", {}).setdefault(str(key_slot), {
        "attempts": 0,
        "success": 0,
        "failure": 0,
        "last_error": "",
        "last_latency_ms": 0,
        "cooldown_until": 0,
    })
    key_stats["attempts"] = int(key_stats.get("attempts", 0)) + 1
    key_stats["last_latency_ms"] = int(max(0, latency_ms))
    key_stats["cooldown_until"] = int(_gemini_key_cooldowns.get(key_slot, 0))
    if success:
        key_stats["success"] = int(key_stats.get("success", 0)) + 1
    else:
        key_stats["failure"] = int(key_stats.get("failure", 0)) + 1
        if error:
            key_stats["last_error"] = str(error)[:220]

    model_stats = data.setdefault("models", {}).setdefault(model_name, {
        "attempts": 0,
        "success": 0,
        "failure": 0,
        "last_error": "",
        "cooldown_until": 0,
    })
    model_stats["attempts"] = int(model_stats.get("attempts", 0)) + 1
    model_stats["cooldown_until"] = int(_gemini_model_cooldowns.get(model_name, 0))
    if success:
        model_stats["success"] = int(model_stats.get("success", 0)) + 1
    else:
        model_stats["failure"] = int(model_stats.get("failure", 0)) + 1
        if error:
            model_stats["last_error"] = str(error)[:220]

    data["updated_at"] = int(_now_ts())
    _write_usage_stats(data)


def call_llm_primary_gemini(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.8,
    timeout: int = 45,
    max_retries: int = 1,
    purpose: str = "general",
) -> tuple[str, str]:
    """Call LLM with policy: Gemini first, Azure fallback, OpenRouter fallback.

    Returns:
        (text, model_used)
        - model_used examples: gemini:gemini-2.5-flash, azure:gpt-4o
    """
    text, model_used, gemini_error = _call_gemini_chat(system_prompt, user_prompt, temperature)
    if text:
        return text, model_used

    if gemini_error:
        logger.warning(f"LLM router ({purpose}): Gemini unavailable, fallback to Azure GPT. {gemini_error}")

    text, model_used, azure_error = _call_azure_chat(
        system_prompt,
        user_prompt,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )
    if text:
        return text, model_used

    if azure_error:
        logger.error(f"LLM router ({purpose}): Azure fallback failed. {azure_error}")

    text, model_used, openrouter_error = _call_openrouter_chat(
        system_prompt,
        user_prompt,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
    )
    if text:
        return text, model_used

    if openrouter_error:
        logger.error(f"LLM router ({purpose}): OpenRouter fallback failed. {openrouter_error}")

    return "", ""


def _import_genai() -> tuple[Any | None, Any | None, str]:
    """Import google genai SDK with compatibility fallbacks."""
    try:
        import google.genai as genai  # type: ignore
        from google.genai import types  # type: ignore

        return genai, types, ""
    except Exception:
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore

            return genai, types, ""
        except Exception as exc:
            return None, None, str(exc)


def _call_gemini_chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
) -> tuple[str, str, str]:
    """Try Gemini models with key rotation and lightweight cooldown guards."""
    keys = settings.get_gemini_keys()
    if not keys:
        return "", "", "No GEMINI_API_KEY configured"

    genai, types, import_error = _import_genai()
    if genai is None or types is None:
        return "", "", f"google-genai unavailable: {import_error}"

    last_error = ""
    attempted_any = False
    models = _gemini_models_order()

    for model_name in models:
        if _is_model_cooling_down(model_name):
            continue

        attempted_model = False
        for key_idx, api_key in enumerate(keys):
            key_slot = key_idx + 1
            if _is_key_cooling_down(key_slot):
                continue

            attempted_model = True
            attempted_any = True
            started = _now_ts()
            try:
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model_name,
                    contents=[user_prompt],
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                    ),
                )

                text = (getattr(response, "text", "") or "").strip()
                if text:
                    latency_ms = int(round((_now_ts() - started) * 1000))
                    _record_gemini_usage(model_name, key_slot, True, latency_ms)
                    return text, f"gemini:{model_name}", ""

                last_error = f"Empty response from {model_name}"
                latency_ms = int(round((_now_ts() - started) * 1000))
                _record_gemini_usage(model_name, key_slot, False, latency_ms, last_error)
            except Exception as exc:
                err = str(exc)
                last_error = err
                latency_ms = int(round((_now_ts() - started) * 1000))
                category = _classify_gemini_error(err)
                _record_gemini_usage(model_name, key_slot, False, latency_ms, err)

                if category == "quota":
                    _set_key_cooldown(key_slot)
                    logger.debug(f"Gemini {model_name} key#{key_slot} quota/rate hit, rotating")
                    continue

                if category == "model_unavailable":
                    _set_model_cooldown(model_name)
                    logger.debug(f"Gemini model unavailable, skipping {model_name}")
                    break

                logger.warning(f"Gemini call failed for {model_name} key#{key_slot}: {err}")

        if not attempted_model:
            last_error = f"All keys in cooldown for {model_name}"

        time.sleep(0.20)

    if not attempted_any:
        return "", "", "All Gemini keys/models are temporarily in cooldown"

    return "", "", last_error or "Gemini call failed"


def _call_azure_chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    max_retries: int,
) -> tuple[str, str, str]:
    """Fallback call to Azure Inference using configured GPT models."""
    if not settings.github_token:
        return "", "", "Missing GITHUB_TOKEN for Azure Inference"

    models: list[str] = []
    for model in [settings.inference_model, settings.inference_fallback_model]:
        m = (model or "").strip()
        if m and m not in models:
            models.append(m)

    if not models:
        return "", "", "No inference models configured"

    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Content-Type": "application/json",
    }

    last_error = ""

    for model in models:
        payload = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        try:
            response = request_with_retry(
                "POST",
                settings.inference_api_url,
                json_data=payload,
                headers=headers,
                max_retries=max_retries,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning(f"Azure model {model} call exception: {exc}")
            continue

        if response.status_code >= 400:
            last_error = f"HTTP {response.status_code}: {(response.text or '')[:200]}"
            logger.warning(f"Azure model {model} failed: {last_error}")
            continue

        try:
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            last_error = f"Invalid JSON response: {exc}"
            continue

        if content:
            return content, f"azure:{model}", ""

        last_error = f"Empty response from Azure model {model}"

    return "", "", last_error or "Azure fallback failed"


def _call_openrouter_chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    timeout: int,
    max_retries: int,
) -> tuple[str, str, str]:
    """Fallback call to OpenRouter chat completion API."""
    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        return "", "", "Missing OPENROUTER_API_KEY"

    models: list[str] = []
    for model in [settings.openrouter_model, settings.openrouter_fallback_model]:
        m = (model or "").strip()
        if m and m not in models:
            models.append(m)

    if not models:
        return "", "", "No OpenRouter models configured"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if settings.openrouter_site_url:
        headers["HTTP-Referer"] = settings.openrouter_site_url
    if settings.openrouter_app_name:
        headers["X-Title"] = settings.openrouter_app_name

    base_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error = ""

    for model in models:
        payload = {
            "model": model,
            "messages": base_messages,
            "temperature": temperature,
            "max_tokens": max(256, int(settings.openrouter_max_tokens or 4096)),
        }

        try:
            response = request_with_retry(
                "POST",
                settings.openrouter_api_url,
                json_data=payload,
                headers=headers,
                max_retries=max_retries,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning(f"OpenRouter model {model} call exception: {exc}")
            continue

        # Some models reject explicit temperature; retry once without it.
        if response.status_code == 400 and "temperature" in (response.text or "").lower():
            payload.pop("temperature", None)
            try:
                response = request_with_retry(
                    "POST",
                    settings.openrouter_api_url,
                    json_data=payload,
                    headers=headers,
                    max_retries=max_retries,
                    timeout=timeout,
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f"OpenRouter model {model} retry without temperature failed: {exc}")
                continue

        if response.status_code >= 400:
            last_error = f"HTTP {response.status_code}: {(response.text or '')[:200]}"
            logger.warning(f"OpenRouter model {model} failed: {last_error}")
            continue

        try:
            data = response.json()
            message = data.get("choices", [{}])[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, list):
                text_parts: list[str] = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if text:
                            text_parts.append(str(text))
                content = "\n".join(text_parts).strip()
        except Exception as exc:
            last_error = f"Invalid JSON response: {exc}"
            continue

        if content:
            return str(content), f"openrouter:{model}", ""

        last_error = f"Empty response from OpenRouter model {model}"

    return "", "", last_error or "OpenRouter fallback failed"
