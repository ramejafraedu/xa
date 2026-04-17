"""Content Generator — Gemini primary + Azure GPT fallback.

Migrates the logic from n8n nodes: 📝 Preparar Prompt + 🤖 Copilot Generar + 🛡️ Fallback.
Uses the unified LLM router so Gemini runs first (4-key rotation) and GPT is backup.
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
from services.llm_router import call_llm_primary_gemini
from services.niche_memory import normalize_manual_ideas

try:
    from pipeline.om_scene_evaluator import evaluate_composition_plan
except Exception:  # pragma: no cover
    evaluate_composition_plan = None


# V16.1 PRO coherence thresholds — on a 0..10 scale where 10 is best.
# We accept scripts with score >= 8.5 on first pass; between 6 and 8.5 we retry
# once; below 6 we retry once. After retry, we accept whatever we get and log.
_COHERENCE_MIN_SCORE = 8.5
_COHERENCE_HARD_FLOOR = 6.0


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


def _script_profile(platform: str) -> tuple[int, int, str]:
    """Return target script length profile by platform.

    V16 PRO — Shorts strategy: prioritize 30-45s high-retention clips over
    slow mini-documentaries. Word counts are derived from
    ``settings.short_script_word_min/max`` and
    ``settings.target_duration_seconds`` when ``enforce_duration_hard_limit``
    is enabled (default).
    """
    p = (platform or "").lower()

    if getattr(settings, "enforce_duration_hard_limit", False):
        word_min = int(getattr(settings, "short_script_word_min", 110))
        word_max = int(getattr(settings, "short_script_word_max", 130))
        target_s = int(getattr(settings, "target_duration_seconds", 40))
        max_s = int(getattr(settings, "max_video_duration", 60))
        target_label = f"{target_s - 5}-{max_s} segundos (objetivo {target_s}s)"
        # Facebook allows slightly longer explanatory content.
        if p == "facebook":
            return word_min + 40, word_max + 80, f"{target_s + 20}-{max_s + 40} segundos"
        return word_min, word_max, target_label

    # Legacy fallback (when V16 PRO hard limit is disabled).
    if p == "facebook":
        return 170, 260, "70-120 segundos"
    if p == "reels":
        return 130, 200, "50-90 segundos"
    if p == "shorts":
        return 120, 185, "45-75 segundos"
    if p == "tiktok":
        return 130, 200, "50-90 segundos"
    return 120, 180, "45-75 segundos"


def _script_word_count(data: dict) -> int:
    """Count words in generated script body."""
    text = str(data.get("guion", "") or "").strip()
    return len(text.split())


def _trim_long_script(data: dict, word_max: int) -> dict:
    """Hard-trim a too-long script to stay within the short-form word budget.

    Keeps the hook, clips trailing sentences until budget is met, and
    appends the ``micro_loop`` (or a default curiosity line) as closing.
    """
    if not isinstance(data, dict):
        return data
    text = str(data.get("guion", "") or "").strip()
    if not text:
        return data

    words = text.split()
    if len(words) <= word_max:
        return data

    # Keep headroom for the micro-loop closing line (~10 words).
    budget = max(20, word_max - 10)
    trimmed = " ".join(words[:budget]).rstrip(",;:- ")
    # Snap to last sentence boundary if possible.
    for stop in (".", "!", "?"):
        idx = trimmed.rfind(stop)
        if idx > len(trimmed) * 0.5:
            trimmed = trimmed[: idx + 1]
            break

    loop = str(data.get("micro_loop", "") or "").strip()
    if not loop:
        loop = "pero lo peor todavia esta por venir..."
    data["guion"] = f"{trimmed} {loop}".strip()
    data["micro_loop"] = loop
    return data


def _rewrite_short_script(
    data: dict,
    word_min: int,
    word_max: int,
    platform: str,
) -> tuple[dict | None, str]:
    """Ask the model to expand a short script while preserving intent."""
    system = (
        "Eres editor senior de videos virales. "
        "Debes mantener el mismo tema, gancho y CTA, pero ampliar el guion para que quede mas explicativo y claro. "
        "Devuelve SOLO JSON valido con la misma estructura de entrada."
    )
    user = (
        f"El campo guion quedo corto para {platform}. "
        f"Reescribe para que tenga entre {word_min} y {word_max} palabras, "
        "con explicacion practica, ejemplo concreto y ritmo viral.\n\n"
        f"JSON ACTUAL:\n{json.dumps(data, ensure_ascii=False)}"
    )

    text, model_used = call_llm_primary_gemini(
        system_prompt=system,
        user_prompt=user,
        temperature=0.7,
        timeout=60,
        max_retries=2,
        purpose="content_gen_expand",
    )

    if not text:
        return None, ""

    try:
        parsed = _parse_json_response(text)
        return parsed, model_used
    except Exception:
        return None, model_used


SYSTEM_PROMPT = """Eres head writer de videos faceless top 1% (TikTok/Reels/Shorts). Objetivo: retencion brutal de 30-45 segundos.

ESTRATEGIA V16 PRO (OBLIGATORIA — SHORTS DE ALTA RETENCION):
- Duracion objetivo: 35-45 segundos (MAX 55s). NO mini-documentales.
- Estructura del guion en 3 bloques claros:
  1. HOOK (0-2s): una frase brutal, curiosa o polarizante que obliga a quedarse.
  2. DESARROLLO (2-35s): 8-10 micro-escenas cortas; cada frase = un cambio visual. Ritmo rapido, sin relleno.
  3. MICRO-LOOP FINAL: frase que genera curiosidad para re-ver o continuar (ej: "pero lo peor todavia esta por venir...", "nadie sabe que paso despues...", "y el final te va a romper la cabeza...").
- Prohibido el CTA tradicional que pida like/follow: el cierre DEBE ser un micro-loop narrativo.

REGLAS MAESTRAS:
- Gancho en <=2 segundos con polarizacion real o dato impactante.
- En los primeros 3 segundos rompe una creencia popular o revela una trampa oculta.
- Si recibes IDEAS MANUALES, tienen prioridad editorial sobre el resto del contexto.
- Construye tension creciente con giro claro justo antes del micro-loop.
- Usa polemica controlada para generar debate sin inventar datos ni hacer afirmaciones difamatorias.
- Escribe 3 variantes de gancho: shock, pregunta, promesa.
- Frases MUY cortas de 5 a 10 palabras (ritmo de escaneo rapido).
- Cambio visual/concepto cada 3-5 segundos (una idea por frase).
- Evita tono enciclopedico; usa conflicto, friccion y consecuencia directa.
- Incluir 1-2 muletillas humanas naturales (mira, o sea, te digo algo, ...).
- PROHIBIDO: No uses comillas dobles en los textos generados. Si necesitas resaltar algo, usa comillas simples.
- Mantener coherencia total con estilo, tendencia y nicho.
- Modo razonamiento maximo: doble verificacion interna de consistencia y calidad antes de responder.

RUBRICA OBLIGATORIA (0-10):
- hook: fuerza de apertura y curiosidad inmediata (<=2s).
- desarrollo: ritmo rapido, cambios cada 3-5s, sin relleno.
- micro_loop: cierre que genera curiosidad para re-ver / continuar.
Si algun bloque queda < 7, reescribe internamente antes de responder.

AB TEST ACTIVO:
- Variante: {ab_variant}
- Plataforma objetivo: {plataforma}
- Regla de hook por variante: {hook_rule}

ESTILO: {estilo_narrativo}
DIRECCION VISUAL OBLIGATORIA: {direccion_visual}
REGLA FRICCION: Abre rompiendo una creencia popular o revelando una trampa oculta en los primeros 3 segundos; evita tono enciclopedico.
MULETILLAS: Incluye 1-2 muletillas naturales repartidas en el guion: mira, o sea, te digo algo...
TRENDING: {trending_context}
HISTORIAL: {memoria}
IDEAS MANUALES PRIORITARIAS: {manual_ideas_block}
LONGITUD OBJETIVO: STRICTAMENTE {word_min}-{word_max} palabras ({target_duration}). Mas palabras sera RECHAZADO.

Devuelve solo JSON valido, sin texto extra."""

USER_PROMPT = """Genera un SHORT viral de 30-45 segundos para {nicho}, tono {tono}, plataforma {plataforma}. Usa variante {ab_variant}.
IDEAS MANUALES PRIORITARIAS: {manual_ideas_block}

HOOK BRUTAL (0-2s) — ESCOGE UNA tactica concreta:
  1) SHOCK: dato/estadistica imposible ("El 97% de X...").
  2) QUESTION: pregunta prohibida o imposible ("Por que nadie te cuenta que...?").
  3) STATEMENT: afirmacion polemica/contra-intuitiva que rompe el sentido comun.
PROHIBIDO empezar con "hola", "hoy", "sabes que", "te voy a contar".
Duracion objetivo del contenido: {target_duration}.

ESTRUCTURA del guion (OBLIGATORIA):
- Hook (0-2s) — frase de <=12 palabras que cumple una tactica de arriba.
- Desarrollo (2-35s): 10-12 micro-escenas, cada frase = un cambio visual (3-5s).
- Cierre con MICRO-LOOP de curiosidad (ej: "pero lo peor todavia esta por venir...", "nadie sabe que paso despues...", "y el final te va a romper la cabeza...").

Devuelve EXACTAMENTE este JSON (num_clips DEBE estar entre 10 y 12):
{{
  "num_clips": 11,
  "titulo": "titulo corto potente max 9 palabras",
  "gancho": "hook max 12 palabras, impactante y curioso (leible en <=2s)",
  "gancho_variants": ["gancho shock","gancho pregunta","gancho statement"],
  "hooks_alternos": ["hook alterno A","hook alterno B","hook alterno C"],
  "hook_score": 9,
  "hook_tactic": "shock|question|statement",
  "block_scores": {{
    "hook": 9,
    "desarrollo": 8,
    "micro_loop": 9
  }},
  "guion": "guion de STRICTAMENTE {word_min}-{word_max} palabras, frases cortas de 5-10 palabras, ritmo rapido con cambio de idea cada 3-5s, terminando con un micro-loop de curiosidad",
  "micro_loop": "frase final corta que genera curiosidad para re-ver o continuar (NO es un CTA tradicional)",
  "cta": "frase corta suave de una sola oracion, opcional; puede repetir el micro_loop",
  "caption": "caption max 160 caracteres con 3 hashtags",
  "palabras_clave": ["kw1_ingles","kw2_ingles","kw3_ingles","kw4_ingles","kw5_ingles","kw6_ingles","kw7_ingles","kw8_ingles"],
  "mood_musica": "cinematic|motivational|dark|ambient|epic|sad|corporate",
  "velocidad_cortes": "ultra_rapido",
  "prompt_imagen": "thumbnail prompt in English, ultra specific, must include: {direccion_visual}, dramatic premium composition, no text overlays",
  "duraciones_clips": [2.0,3.0,3.5,3.5,4.0,4.0,4.0,4.0,4.0,3.5,3.0],
  "transition_suggestions": ["cut","punch-in","whip","dissolve","wipe","cut","cut","dissolve","wipe","cut","zoom-out"],
  "viral_score": 9
}}"""


def generate_content(
    nicho: NichoConfig,
    trending_context: str = "",
    memoria: str = "Sin memoria previa",
    manual_ideas: str | list[str] | None = None,
    correction_prompt: Optional[str] = None,
) -> dict:
    """Generate viral video content via Gemini-first routing.

    Args:
        nicho: Niche configuration.
        trending_context: Trending topics string.
        memoria: Previous video memory string.
        manual_ideas: Optional manual topic/angle lines with priority.
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
    word_min, word_max, target_duration = _script_profile(platform)
    manual_idea_lines = normalize_manual_ideas(manual_ideas)
    manual_ideas_block = " | ".join(manual_idea_lines) if manual_idea_lines else "N/A"

    system = SYSTEM_PROMPT.format(
        ab_variant=ab_variant,
        plataforma=platform,
        hook_rule=hook_rule,
        estilo_narrativo=nicho.estilo_narrativo,
        direccion_visual=nicho.direccion_visual,
        trending_context=trending_context,
        memoria=memoria,
        manual_ideas_block=manual_ideas_block,
        word_min=word_min,
        word_max=word_max,
        target_duration=target_duration,
    )

    if correction_prompt:
        user = correction_prompt
    else:
        user = USER_PROMPT.format(
            nicho=nicho.nombre,
            tono=nicho.tono,
            plataforma=platform,
            ab_variant=ab_variant,
            manual_ideas_block=manual_ideas_block,
            direccion_visual=nicho.direccion_visual,
            word_min=word_min,
            word_max=word_max,
            target_duration=target_duration,
        )

    content_text, model_used = call_llm_primary_gemini(
        system_prompt=system,
        user_prompt=user,
        temperature=0.93,
        timeout=60,
        max_retries=2,
        purpose="content_gen",
    )

    if not content_text:
        raise ContentGenerationError("All models failed. Gemini primary and GPT fallback unavailable")

    parsed = _parse_json_response(content_text)

    current_words = _script_word_count(parsed)
    if current_words < word_min:
        logger.warning(
            f"Script too short for {platform}: {current_words} words < {word_min}. Expanding automatically."
        )
        for _ in range(2):
            rewritten, rewrite_model = _rewrite_short_script(
                parsed,
                word_min=word_min,
                word_max=word_max,
                platform=platform,
            )
            if not rewritten:
                continue
            rewritten_words = _script_word_count(rewritten)
            if rewritten_words >= word_min:
                parsed = rewritten
                model_used = rewrite_model or model_used
                break
    elif current_words > word_max:
        logger.warning(
            f"Script too long for {platform}: {current_words} words > {word_max}. "
            "Trimming hard to keep 30-45s short-form rhythm."
        )
        parsed = _trim_long_script(parsed, word_max)

    # --- V16.1 PRO: enforce 10-12 scenes for short-form pacing ---
    parsed = _enforce_scene_count(parsed)

    # --- V16.1 PRO: OpenMontage coherence scoring + 1-retry ---
    specs = _derive_pseudo_scene_specs(parsed)
    score_10, payload = _coherence_score_10(specs)
    parsed["_coherence"] = {
        "score_10": score_10,
        "verdict": payload.get("verdict"),
        "violations": payload.get("violations", []),
    }
    if score_10 < _COHERENCE_MIN_SCORE and specs:
        logger.warning(
            f"[content_gen] coherence={score_10}/10 "
            f"(min {_COHERENCE_MIN_SCORE}); requesting 1 retry "
            f"[verdict={payload.get('verdict')}]"
        )
        try:
            retry_prompt = _coherence_retry_prompt(parsed, payload)
            retry_text, retry_model = call_llm_primary_gemini(
                system_prompt=system,
                user_prompt=retry_prompt,
                temperature=0.85,
                timeout=60,
                max_retries=1,
                purpose="content_gen_coherence_retry",
            )
            if retry_text:
                retry_parsed = _parse_json_response(retry_text)
                retry_specs = _derive_pseudo_scene_specs(retry_parsed)
                retry_score, retry_payload = _coherence_score_10(retry_specs)
                if retry_score > score_10 and retry_score >= _COHERENCE_HARD_FLOOR:
                    parsed = retry_parsed
                    model_used = retry_model or model_used
                    parsed["_coherence"] = {
                        "score_10": retry_score,
                        "verdict": retry_payload.get("verdict"),
                        "violations": retry_payload.get("violations", []),
                        "retried": True,
                    }
                    logger.info(
                        f"[content_gen] coherence retry improved to {retry_score}/10"
                    )
                else:
                    logger.info(
                        f"[content_gen] coherence retry did not improve "
                        f"({retry_score}/10), keeping original"
                    )
        except Exception as exc:
            logger.debug(f"[content_gen] coherence retry failed: {exc}")

    parsed["_ab_variant"] = ab_variant
    parsed["_platform"] = platform
    parsed["_model_used"] = model_used or settings.inference_model

    logger.info(
        f"Content generated with {parsed['_model_used']} "
        f"[coherence={parsed.get('_coherence', {}).get('score_10', 'n/a')}]: "
        f"{parsed.get('titulo', '?')[:50]}"
    )
    return parsed


def _enforce_scene_count(parsed: dict) -> dict:
    """Clamp ``num_clips`` and ``duraciones_clips`` to the short-form rhythm.

    Uses ``settings.short_min_scenes`` and ``settings.short_max_scenes`` (defaults
    to 10..12). Re-balances ``duraciones_clips`` so their sum stays close to
    ``settings.target_duration_seconds``.
    """
    if not isinstance(parsed, dict):
        return parsed
    min_scenes = int(getattr(settings, "short_min_scenes", 10) or 10)
    max_scenes = int(getattr(settings, "short_max_scenes", 12) or 12)
    target_s = float(getattr(settings, "target_duration_seconds", 40) or 40)
    hook_max = float(getattr(settings, "short_hook_max_seconds", 2.0) or 2.0)
    scene_min = float(getattr(settings, "short_scene_min_seconds", 2.5) or 2.5)
    scene_max = float(getattr(settings, "short_scene_max_seconds", 5.0) or 5.0)

    try:
        num = int(parsed.get("num_clips") or 0)
    except Exception:
        num = 0
    if num <= 0:
        num = 11  # safe default inside 10..12

    # Clamp count to range.
    clamped = max(min_scenes, min(max_scenes, num))
    if clamped != num:
        logger.info(
            f"[content_gen] clamping num_clips {num} -> {clamped} "
            f"(range {min_scenes}..{max_scenes})"
        )
    parsed["num_clips"] = clamped

    # Rebuild durations so hook is <= hook_max and body is within [scene_min, scene_max].
    current = parsed.get("duraciones_clips")
    if not isinstance(current, list):
        current = []
    durations: list[float] = []
    for v in current[:clamped]:
        try:
            durations.append(float(v))
        except Exception:
            pass
    if len(durations) < clamped:
        # Distribute remaining budget evenly.
        remaining = clamped - len(durations)
        body_budget = max(0.0, target_s - hook_max - sum(durations[1:] if durations else []))
        filler = max(scene_min, min(scene_max, body_budget / max(remaining, 1)))
        durations.extend([filler] * remaining)

    # Enforce hook cap on index 0.
    if durations:
        durations[0] = min(hook_max, max(1.2, durations[0]))
    # Clamp rest.
    for i in range(1, len(durations)):
        durations[i] = max(scene_min, min(scene_max, durations[i]))
    # Trim to clamped length.
    durations = durations[:clamped]

    # Rescale to hit target if too far off.
    total = sum(durations)
    if total > 0 and abs(total - target_s) / target_s > 0.25 and len(durations) > 1:
        scale = target_s / total
        head = durations[0]
        body = [d * scale for d in durations[1:]]
        body = [max(scene_min, min(scene_max, d)) for d in body]
        durations = [head] + body

    parsed["duraciones_clips"] = [round(d, 2) for d in durations]

    return parsed


def _derive_pseudo_scene_specs(parsed: dict) -> list[dict]:
    """Build scene_specs compatible with ``evaluate_composition_plan``.

    The short-form pipeline does not emit full shot/motion/emotion metadata
    at this stage, so we synthesise specs by splitting the script into
    sentences and rotating a diverse shot/motion/emotion cycle. This keeps
    the OpenMontage heuristics useful (generic-phrase detection, slideshow
    risk) while avoiding false positives on shot/motion variety.
    """
    guion = str(parsed.get("guion", "") or "").strip()
    if not guion:
        return []
    # Sentence split (keep it dependency-free).
    sentences = [s.strip() for s in re.split(r"(?<=[\.\!\?])\s+", guion) if s.strip()]
    if not sentences:
        return []
    shot_cycle = ["wide", "medium", "close-up", "over-shoulder"]
    motion_cycle = ["dynamic", "pan", "push-in", "static", "handheld"]
    emotion_cycle = ["intrigue", "tension", "revelation", "curiosity", "surprise"]
    specs: list[dict] = []
    for i, sentence in enumerate(sentences[:12]):
        specs.append({
            "shot_type": shot_cycle[i % len(shot_cycle)],
            "motion": motion_cycle[i % len(motion_cycle)],
            "emotion": emotion_cycle[i % len(emotion_cycle)],
            "clip_description": sentence[:200],
        })
    return specs


def _coherence_score_10(scene_specs: list[dict]) -> tuple[float, dict]:
    """Return a 0..10 coherence score + raw evaluator payload."""
    if evaluate_composition_plan is None or not scene_specs:
        return 10.0, {"verdict": "unknown", "violations": [], "suggestions": []}
    try:
        payload = evaluate_composition_plan(scene_specs)
    except Exception as exc:
        logger.debug(f"[content_gen] OM evaluator error: {exc}")
        return 10.0, {"verdict": "error", "violations": [str(exc)], "suggestions": []}
    risk = float(payload.get("score", 0.0))
    # Evaluator returns risk 0..5 (lower is better). Map to 0..10 where 10 is best.
    score_10 = round(max(0.0, (5.0 - risk) * 2.0), 2)
    payload["score_10"] = score_10
    return score_10, payload


def _coherence_retry_prompt(prev_parsed: dict, payload: dict) -> str:
    """Build a correction prompt for a coherence retry."""
    violations = payload.get("violations", []) or []
    suggestions = payload.get("suggestions", []) or []
    reasons = "; ".join(str(x) for x in violations[:4]) or "coherencia visual debil"
    hints = "; ".join(str(x) for x in suggestions[:3]) or (
        "usa frases mas concretas con sujeto claro y accion visible; "
        "evita 'a person', 'modern', 'stunning', 'incredible', 'amazing'"
    )
    return (
        "El guion anterior fue RECHAZADO por coherencia visual debil. "
        f"Problemas detectados: {reasons}. "
        f"Aplicar estas correcciones: {hints}. "
        "Regenera el mismo JSON con frases mas especificas, sujetos concretos, "
        "mezcla de planos (wide/close-up/over-shoulder) implicita en el lenguaje, "
        "y micro-loop final conservado.\n\n"
        f"JSON ANTERIOR A CORREGIR:\n{json.dumps(prev_parsed, ensure_ascii=False)}"
    )


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
