"""Content Generator — GPT-4.1 via Azure Inference API.

Migrates the logic from n8n nodes: 📝 Preparar Prompt + 🤖 Copilot Generar + 🛡️ Fallback.
Uses the SAME model for both generation and self-healing (GPT-4.1).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Optional

from loguru import logger

from config import settings
from models.config_models import NichoConfig
from models.content import ABVariant
from services.http_client import request_with_retry


def _hash_string(s: str) -> int:
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _resolve_platform(valor: str) -> str:
    p = valor.lower()
    if "tiktok" in p:
        return "tiktok"
    if "reel" in p or "instagram" in p:
        return "reels"
    if "short" in p or "youtube" in p:
        return "shorts"
    if "facebook" in p:
        return "facebook"
    return "shorts"


def _choose_ab_variant(topic: str, platform: str) -> str:
    seed = f"{topic}|{platform}|{time.strftime('%Y-%m-%d')}"
    return "B" if _hash_string(seed) % 2 == 0 else "A"


def _hook_rules(platform: str, variant: str) -> str:
    rules = {
        "tiktok": {
            "A": "Hook de impacto inmediato con dato extremo en <=1.8s; tension y recompensa rapida.",
            "B": "Hook de pregunta polarizante en <=1.8s; conflicto social + promesa clara de payoff.",
        },
        "reels": {
            "A": "Hook estetico-emocional en <=2.0s; mini-historia personal + giro inspiracional.",
            "B": "Hook aspiracional en <=2.0s; contraste antes/despues + CTA suave para guardar.",
        },
        "shorts": {
            "A": "Hook curiosidad tecnica en <=1.8s; revelacion progresiva por bloques.",
            "B": "Hook mito-vs-realidad en <=1.8s; desmonta una creencia y cierra con accion concreta.",
        },
    }
    by_platform = rules.get(platform, rules["shorts"])
    return by_platform.get(variant, by_platform["A"])


SYSTEM_PROMPT = """Eres head writer de videos faceless top 1%. Objetivo: CTR alto y retencion brutal.

REGLAS MAESTRAS:
- Gancho en <=1.8 segundos con polarizacion real.
- En los primeros 3 segundos rompe una creencia popular o revela una trampa oculta.
- Escribe 3 variantes de gancho: shock, pregunta, promesa.
- Frases cortas de 5 a 12 palabras.
- Cliffhangers cada 8-10 segundos.
- Evita tono enciclopedico; usa conflicto, friccion y consecuencia directa.
- Incluir 2 a 4 muletillas humanas naturales (mira, o sea, te digo algo, ...).
- PROHIBIDO: No uses comillas dobles en los textos generados. Si necesitas resaltar algo, usa comillas simples.
- Mantener coherencia total con estilo, tendencia y nicho.
- Modo razonamiento maximo: doble verificacion interna de consistencia y calidad antes de responder.

RUBRICA OBLIGATORIA (0-10):
- hook: fuerza de apertura y curiosidad inmediata.
- desarrollo: claridad, progresion y ritmo narrativo.
- cierre: payoff + CTA natural sin cortar la emocion.
Si algun bloque queda < 7, reescribe internamente antes de responder.

AB TEST ACTIVO:
- Variante: {ab_variant}
- Plataforma objetivo: {plataforma}
- Regla de hook por variante: {hook_rule}

ESTILO: {estilo_narrativo}
DIRECCION VISUAL OBLIGATORIA: {direccion_visual}
REGLA FRICCION: Abre rompiendo una creencia popular o revelando una trampa oculta en los primeros 3 segundos; evita tono enciclopedico.
MULETILLAS: Incluye 2 a 4 muletillas naturales repartidas en el guion: mira, o sea, te digo algo...
TRENDING: {trending_context}
HISTORIAL: {memoria}

Devuelve solo JSON valido, sin texto extra."""

USER_PROMPT = """Genera contenido viral para {nicho} tono {tono} en {plataforma}. Usa variante {ab_variant}.

Tu apertura debe desafiar una creencia popular o exponer una manipulacion habitual.

Devuelve EXACTAMENTE este JSON:
{{
  "num_clips": 8,
  "titulo": "titulo corto potente max 9 palabras",
  "gancho": "gancho principal de 9 a 14 palabras",
  "gancho_variants": ["gancho shock","gancho pregunta","gancho promesa"],
  "hooks_alternos": ["hook alterno A","hook alterno B","hook alterno C"],
  "hook_score": 9,
  "block_scores": {{
    "hook": 9,
    "desarrollo": 8,
    "cierre": 8
  }},
  "guion": "guion de 90-150 palabras con micro cliffhangers cada 8-10 segundos y 2-4 muletillas humanas naturales",
  "cta": "cta breve natural de una oracion",
  "caption": "caption max 160 caracteres con 3 hashtags",
  "palabras_clave": ["kw1_ingles","kw2_ingles","kw3_ingles","kw4_ingles","kw5_ingles","kw6_ingles","kw7_ingles","kw8_ingles"],
  "mood_musica": "cinematic|motivational|dark|ambient|epic|sad|corporate",
  "velocidad_cortes": "ultra_rapido|rapido|mixto|cinematografico",
  "prompt_imagen": "thumbnail prompt in English, ultra specific, must include: {direccion_visual}, dramatic premium composition, no text overlays",
  "duraciones_clips": [2.0,2.0,2.0,2.0,2.0,2.0,2.0,2.0],
  "viral_score": 9
}}"""


def generate_content(
    nicho: NichoConfig,
    trending_context: str = "",
    memoria: str = "Sin memoria previa",
    correction_prompt: Optional[str] = None,
) -> dict:
    """Call GPT-4.1 to generate viral video content.

    Args:
        nicho: Niche configuration.
        trending_context: Trending topics string.
        memoria: Previous video memory string.
        correction_prompt: If provided, this is a self-healing re-generation
                          with specific correction instructions.

    Returns:
        Raw parsed JSON dict from the AI response.

    Raises:
        ContentGenerationError: If all attempts fail.
    """
    platform = _resolve_platform(nicho.plataforma)
    ab_variant = _choose_ab_variant(nicho.nombre, platform)
    hook_rule = _hook_rules(platform, ab_variant)

    system = SYSTEM_PROMPT.format(
        ab_variant=ab_variant,
        plataforma=platform,
        hook_rule=hook_rule,
        estilo_narrativo=nicho.estilo_narrativo,
        direccion_visual=nicho.direccion_visual,
        trending_context=trending_context,
        memoria=memoria,
    )

    if correction_prompt:
        user = correction_prompt
    else:
        user = USER_PROMPT.format(
            nicho=nicho.nombre,
            tono=nicho.tono,
            plataforma=platform,
            ab_variant=ab_variant,
            direccion_visual=nicho.direccion_visual,
        )

    payload = {
        "model": settings.inference_model,
        "temperature": 0.93,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Content-Type": "application/json",
    }

    # Try primary model, then fallback
    models = [settings.inference_model, settings.inference_fallback_model]
    last_error = ""

    for model in models:
        payload["model"] = model
        try:
            response = request_with_retry(
                "POST",
                settings.inference_api_url,
                json_data=payload,
                headers=headers,
                max_retries=2,
                timeout=60,
            )

            if response.status_code >= 400:
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(f"Model {model} failed: {last_error}")
                continue

            data = response.json()
            content_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")

            if not content_text:
                last_error = "Empty response from AI"
                continue

            # Parse JSON from response
            parsed = _parse_json_response(content_text)
            parsed["_ab_variant"] = ab_variant
            parsed["_platform"] = platform
            parsed["_model_used"] = model

            logger.info(f"Content generated with {model}: {parsed.get('titulo', '?')[:50]}")
            return parsed

        except Exception as e:
            last_error = str(e)
            logger.warning(f"Model {model} error: {e}")

    raise ContentGenerationError(f"All models failed. Last error: {last_error}")


def _parse_json_response(raw: str) -> dict:
    """Parse JSON from AI response with multiple fallback strategies."""
    # Clean markdown code blocks
    text = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
    text = re.sub(r"```\s*", "", text)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.strip()

    # Extract JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    # Remove trailing commas
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Remove control chars
    text = re.sub(r"[\x00-\x1f]", " ", text)

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try fixing single quotes
    try:
        fixed = re.sub(r"([{,]\s*)'([^']+)'\s*:", r'\1"\2":', text)
        fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    raise ContentGenerationError(f"Could not parse JSON from AI response. First 300 chars: {raw[:300]}")


class ContentGenerationError(Exception):
    """Raised when content generation fails."""
    pass
