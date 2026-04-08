"""Unified LLM router: Gemini primary with Azure GPT fallback.

This module centralizes text-generation calls so V14/V15 agents can
prioritize Gemini (with 4-key rotation) and automatically fail over
to Azure Inference GPT models when Gemini is unavailable.
"""
from __future__ import annotations

import time
from typing import Any

from loguru import logger

from config import settings
from services.http_client import request_with_retry

_GEMINI_CHAT_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-3-flash",
    "gemini-3.1-flash",
    "gemini-3.1-pro",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]


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
    """Try Gemini models with key rotation; return first valid response."""
    keys = settings.get_gemini_keys()
    if not keys:
        return "", "", "No GEMINI_API_KEY configured"

    genai, types, import_error = _import_genai()
    if genai is None or types is None:
        return "", "", f"google-genai unavailable: {import_error}"

    last_error = ""

    for model_name in _GEMINI_CHAT_MODELS:
        for key_idx, api_key in enumerate(keys):
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
                    return text, f"gemini:{model_name}", ""

                last_error = f"Empty response from {model_name}"
            except Exception as exc:
                err = str(exc)
                err_lower = err.lower()
                last_error = err

                if any(token in err_lower for token in ("resource_exhausted", "quota", "429", "rate")):
                    logger.debug(f"Gemini {model_name} key#{key_idx + 1} quota/rate hit, rotating")
                    continue

                if any(token in err_lower for token in ("not_found", "invalid", "404", "unsupported")):
                    logger.debug(f"Gemini model unavailable, skipping {model_name}")
                    break

                logger.warning(f"Gemini call failed for {model_name} key#{key_idx + 1}: {err}")

        time.sleep(0.25)

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
