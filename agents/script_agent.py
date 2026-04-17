"""Video Factory V15/V16 — Script Agent.

Replaces the monolithic content_gen.py call with a structured approach:
1. Receives ResearchBrief + StoryState
2. Uses prompt chaining: outline first → then full script
3. Outputs structured scene-ready script with narrative coherence
4. V16: Loads Skills Markdown files to improve prompt quality

Still uses the same Gemini backend as V15.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from core.state import StoryState
from models.config_models import NichoConfig
from services.llm_router import call_llm_primary_gemini

# Skills directory (relative to project root)
_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@lru_cache(maxsize=16)
def _load_skill(relative_path: str) -> str:
    """Load a skill markdown file and cache it in memory.

    Args:
        relative_path: Path relative to skills/ dir, e.g. 'script/hooks.md'

    Returns:
        File content as string, or empty string if not found.
    """
    skill_path = _SKILLS_DIR / relative_path
    if not skill_path.exists():
        logger.debug(f"Skill not found: {skill_path}")
        return ""
    try:
        content = skill_path.read_text(encoding="utf-8")
        logger.debug(f"✅ Skill loaded: {relative_path} ({len(content)} chars)")
        return content
    except Exception as exc:
        logger.warning(f"Failed reading skill {relative_path}: {exc}")
        return ""


def _build_skills_block(*skill_paths: str) -> str:
    """Build a combined skills context block from multiple skill files."""
    parts = []
    for path in skill_paths:
        content = _load_skill(path)
        if content:
            parts.append(f"\n# SKILL: {path}\n{content}")
    if not parts:
        return ""
    return "\n\n## SKILLS DE ESCRITURA (SEGUIR ESTRICTAMENTE)\n" + "\n---\n".join(parts)


def _script_profile(platform: str) -> tuple[int, int, str]:
    """Return target script length profile by platform.

    V16 PRO — Shorts strategy (30-45s). When
    ``settings.enforce_duration_hard_limit`` is True (default), word budgets
    are clamped to ``settings.short_script_word_min/max`` regardless of
    platform; Facebook gets a slightly bigger explanatory budget.
    """
    p = (platform or "").lower()

    if getattr(settings, "enforce_duration_hard_limit", False):
        word_min = int(getattr(settings, "short_script_word_min", 110))
        word_max = int(getattr(settings, "short_script_word_max", 130))
        target_s = int(getattr(settings, "target_duration_seconds", 40))
        max_s = int(getattr(settings, "max_video_duration", 60))
        label = f"{max(25, target_s - 10)}-{max_s} segundos (objetivo {target_s}s)"
        if p == "facebook":
            return word_min + 40, word_max + 80, f"{target_s + 20}-{max_s + 40} segundos"
        return word_min, word_max, label

    # Legacy long-form defaults.
    if p == "facebook":
        return 190, 240, "70-120 segundos"
    if p == "reels":
        return 180, 220, "60-90 segundos"
    if p == "shorts":
        return 180, 220, "60-90 segundos"
    if p == "tiktok":
        return 180, 220, "60-90 segundos"
    return 180, 220, "60-90 segundos"


def _script_word_count(data: dict) -> int:
    """Count words in script body."""
    text = str(data.get("guion", "") or "").strip()
    return len(text.split())


def _nicho_story_template_block(nicho_slug: str) -> str:
    """Return a niche-specific long-form storytelling template block."""
    slug = str(nicho_slug or "").strip().lower()
    templates = {
        "finanzas": (
            "PLANTILLA_FINANZAS:\n"
            "1) Error caro actual (situacion real)\n"
            "2) Evidencia concreta (dato/ejemplo numerico)\n"
            "3) Mecanismo oculto (por que ocurre)\n"
            "4) Giro de accion (cambio de estrategia)\n"
            "5) Mini plan de ejecucion en 2-3 pasos\n"
            "6) Cierre con costo de inaccion"
        ),
        "historia": (
            "PLANTILLA_HISTORIA:\n"
            "1) Escena inicial cinematica (lugar/fecha/personaje)\n"
            "2) Conflicto central\n"
            "3) Evento detonante con detalle verificable\n"
            "4) Consecuencia humana/politica\n"
            "5) Giro final que recontextualiza lo anterior\n"
            "6) Cierre: por que importa hoy"
        ),
        "historias_reddit": (
            "PLANTILLA_HISTORIAS_REDDIT:\n"
            "1) Setup humano (quien cuenta y por que duele)\n"
            "2) Punto de no retorno\n"
            "3) Escalada por capas (3 micro-revelaciones)\n"
            "4) Climax emocional\n"
            "5) Resolucion (consecuencia real)\n"
            "6) Leccion accionable sin moralina"
        ),
        "curiosidades": (
            "PLANTILLA_CURIOSIDADES:\n"
            "1) Pregunta imposible o intuicion rota\n"
            "2) Experimento mental corto\n"
            "3) Explicacion cientifica simple\n"
            "4) Caso real sorprendente\n"
            "5) Aplicacion cotidiana\n"
            "6) Cierre con nueva pregunta"
        ),
        "ia_herramientas": (
            "PLANTILLA_IA_HERRAMIENTAS:\n"
            "1) Dolor concreto del usuario\n"
            "2) Herramienta y promesa realista\n"
            "3) Flujo paso a paso (input > proceso > output)\n"
            "4) Resultado medible (tiempo/dinero/calidad)\n"
            "5) Error comun y como evitarlo\n"
            "6) CTA para probar hoy"
        ),
    }
    base = (
        "PLANTILLA_BASE_HISTORIA_COMPLETA:\n"
        "1) Contexto inicial\n"
        "2) Conflicto\n"
        "3) Desarrollo con evidencia\n"
        "4) Giro\n"
        "5) Payoff\n"
        "6) Cierre accionable"
    )
    return templates.get(slug, base)


class ScriptAgent:
    """Generate a structured, coherent script using prompt chaining."""

    def run(
        self,
        state: StoryState,
        nicho: NichoConfig,
        correction_notes: str = "",
    ) -> StoryState:
        """Generate or regenerate a script.

        Steps:
          1. Generate outline (narrative arc + key beats)
          2. Expand into full script with hook, body, CTA
          3. Update StoryState with results

        Args:
            state: Current StoryState (updated in place).
            nicho: Niche configuration.
            correction_notes: If provided, regenerate with these corrections.

        Returns:
            Updated StoryState.
        """
        t0 = time.time()

        # Step 1: Generate outline
        if not correction_notes:
            outline = self._generate_outline(state, nicho)
            logger.info(f"📋 Outline generated ({len(outline)} chars)")
        else:
            outline = correction_notes  # Let the correction guide the outline

        # Step 2: Expand to full script
        script_data = self._expand_script(state, nicho, outline, correction_notes)

        # Step 3: Update StoryState
        if script_data:
            state.hook = script_data.get("gancho", "")
            state.hook_variants = script_data.get("gancho_variants", [])
            state.script_full = script_data.get("guion", "")
            state.cta = script_data.get("cta", "")
            state.caption = script_data.get("caption", "")
            state.key_points = script_data.get("key_points", [])
            state.hook_score = float(script_data.get("hook_score", 0))
            state.script_score = float(script_data.get("script_score", 0))

            # Store raw data for V14 compatibility
            state._raw_content = script_data

        elapsed = round(time.time() - t0, 2)
        logger.info(
            f"✍️ Script generated: '{state.hook[:50]}...' "
            f"({len(state.script_full.split())} words, {elapsed}s)"
        )
        return state

    def get_raw_content(self, state: StoryState) -> dict:
        """Get raw content dict for V14 compatibility (quality_gate, etc)."""
        return getattr(state, "_raw_content", {})

    # ----- Internal -----

    def _generate_outline(self, state: StoryState, nicho: NichoConfig) -> str:
        """Step 1: Generate a narrative outline (not the full script)."""
        precedence_block = self._build_precedence_block(state, nicho)
        niche_template = _nicho_story_template_block(getattr(nicho, "slug", ""))

        # V16: Load skills for hook and storytelling guidance
        skills_block = _build_skills_block(
            "script/hooks.md",
            "script/storytelling.md",
            "script/niche_templates.md",
        )

        system = (
            "Eres un director creativo de contenido viral. "
            "Genera un OUTLINE (esquema) para un video corto. "
            "NO escribas el guion completo — solo la estructura narrativa.\n\n"
            "Formato:\n"
            "HOOK: [concepto del gancho en 1 línea]\n"
            "CONTEXT: [contexto y problema real que conecta con la audiencia]\n"
            "MECHANISM: [explicacion causal clara de por que ocurre]\n"
            "BEAT 1: [primer punto de tensión]\n"
            "BEAT 2: [desarrollo/revelación]\n"
            "BEAT 3: [giro o dato impactante]\n"
            "TWIST: [ruptura de expectativa o reencuadre contundente]\n"
            "PAYOFF: [cierre emocional]\n"
            "CTA: [llamada a acción]\n"
            + skills_block
        )

        # Build context from research
        research_ctx = ""
        if state.research.recommended_angles:
            research_ctx = f"ÁNGULOS SUGERIDOS: {', '.join(state.research.recommended_angles)}\n"
        if state.research.hook_suggestions:
            research_ctx += f"HOOKS SUGERIDOS: {', '.join(state.research.hook_suggestions[:3])}\n"
        if state.research.avoid_topics:
            research_ctx += f"EVITAR: {', '.join(state.research.avoid_topics)}\n"
        if state.research.web_sources:
            research_ctx += f"WEB HEADLINES: {'; '.join(state.research.web_sources[:4])}\n"

        user = (
            f"NICHO: {nicho.nombre}\n"
            f"TONO: {nicho.tono}\n"
            f"PLATAFORMA: {state.platform}\n"
            f"ESTILO: {nicho.estilo_narrativo}\n"
            f"TEMPLATE_NICHO:\n{niche_template}\n"
            f"IDEAS_MANUALES_PRIORITARIAS: {' | '.join(state.manual_ideas[:6]) if state.manual_ideas else 'N/A'}\n"
            f"MEMORIA_LOCAL_NICHO: {' | '.join(state.niche_memory_entries[:6]) if state.niche_memory_entries else 'N/A'}\n"
            f"{precedence_block}\n"
            f"{research_ctx}\n"
            f"TRENDING: {state.research.trending_context_raw[:200]}\n\n"
            "Genera el OUTLINE del video aplicando los skills de storytelling y la plantilla del nicho. "
            "Debe ser una historia completa, no una lista de frases sueltas."
        )

        return self._call_llm(system, user, temperature=0.9)

    def _expand_script(
        self,
        state: StoryState,
        nicho: NichoConfig,
        outline: str,
        correction_notes: str,
    ) -> Optional[dict]:
        """Step 2: Expand outline into full structured script."""

        # Import AB variant logic from V14
        from pipeline.content_gen import _choose_ab_variant, _hook_rules, _resolve_platform

        platform = _resolve_platform(nicho.plataforma)
        ab_variant = _choose_ab_variant(nicho.nombre, platform)
        hook_rule = _hook_rules(platform, ab_variant)
        word_min, word_max, target_duration = _script_profile(platform)
        
        # User requested manual duration (up to 3 mins vertical, more for horizontal)
        if getattr(state, "target_duration_seconds", 0) > 0:
            target_words = int((state.target_duration_seconds / 60.0) * 145)
            word_min = int(target_words * 0.9)
            word_max = int(target_words * 1.1)

        precedence_block = self._build_precedence_block(state, nicho)
        niche_template = _nicho_story_template_block(getattr(nicho, "slug", ""))

        correction_block = ""
        if correction_notes:
            correction_block = (
                f"\n⚠️ CORRECCIÓN REQUERIDA:\n{correction_notes}\n"
                f"Mejora específicamente lo indicado sin cambiar la estructura general.\n"
            )
            
        few_shot_examples = """
EJEMPLOS DE TONO DE ALTA RETENCIÓN (Tu guion DEBE ser mucho más largo que estos ejemplos, profundizando en la explicación):

EJEMPLO 1 (Misterio/Psicología):
"gancho": "Esta es la razón por la que te sientes cansado todo el tiempo...",
"guion": "Y no, no es por falta de sueño. Se llama 'fatiga de decisión'. Tu cerebro toma más de 35,000 decisiones al día. Al llegar a las 4 PM, tu corteza prefrontal está literalmente frita. Por eso pides comida chatarra en lugar de cocinar. (Aquí continuas desarrollando el problema con datos reales, estudios, y un cierre mucho más extenso...)"

EJEMPLO 2 (Finanzas/Éxito):
"gancho": "Si ganas menos de $2000 al mes, deja de hacer esto...",
"guion": "Ahorrar el 10% no te hará rico. Te están mintiendo. La inflación está en 4%, el banco te da 1%. Pierdes dinero todos los días. Lo que necesitas no es ahorrar, es multiplicar. Aprender a vender o programar te dará un ROI del 1000%. Cambia tu mentalidad de consumidor a productor. (El guion real debe seguir profundizando y dar pasos aplicables reales...)"
"""

        # V16: Load all script skills
        skills_block = _build_skills_block(
            "script/hooks.md",
            "script/storytelling.md",
            "script/structure.md",
            "script/niche_templates.md",
        )

        system = f"""Eres head writer de SHORTS virales top 1%. Objetivo: retencion brutal en 30-45 segundos.

CONTEXTO NARRATIVO:
{state.to_context_string()}

OUTLINE APROBADO:
{outline}
{few_shot_examples}
{skills_block}

ESTRATEGIA V16 PRO (MANDATORIA — SHORTS 30-45s):
- Duracion objetivo: 35-45 segundos. MAX 55s. NO mini-documentales.
- Estructura del guion en 3 bloques:
  1. HOOK (0-2s): frase brutal/curiosa que obliga a quedarse. Max 10 palabras.
  2. DESARROLLO (2-35s): 8-10 frases cortas; cada frase = un cambio visual/idea. Ritmo rapido.
  3. MICRO-LOOP FINAL: frase que genera curiosidad para re-ver o continuar (ej: "pero lo peor todavia esta por venir...", "nadie sabe que paso despues...", "y el final te va a romper la cabeza..."). NO es un CTA tradicional.
- El campo 'guion' DEBE terminar con el micro-loop.

REGLAS MAESTRAS Y NEGATIVE PROMPTS (MANDATORIO):
- PROHIBIDO: No saludes ("Hola a todos"), no te presentes ("Soy tu presentador").
- PROHIBIDO: Estilo Wikipedia o ensayo escolar. Ve directo al problema, controversia o punto de fricción.
- PROHIBIDO: No pidas "like", "follow", "guarda este video". Cierre = micro-loop narrativo.
- Gancho en <=2 segundos con polarización real. En los primeros 3s rompe una creencia popular.
- Incluye un mini-climax antes del micro-loop: sube tension, revela giro y entrega payoff corto.
- ESTRUCTURA OBLIGATORIA: sigue esta plantilla del nicho de inicio a fin:
{niche_template}
- SECUENCIA NARRATIVA OBLIGATORIA: Hook (<=2s) → Context (1 frase) → Mechanism (2-3 frases) → Twist (1 frase) → Micro-loop final.
- Escribe 3 variantes de gancho: shock, pregunta, promesa.
- Longitud OBLIGATORIA para {platform}: STRICTAMENTE entre {word_min} y {word_max} palabras. FUERA DE ESE RANGO SERA RECHAZADO.
- Frases MUY cortas de 5 a 10 palabras. Una idea = una frase = un corte visual cada 3-5s.
- Usa conflicto, fricción y consecuencia directa. No seas genérico ("aplica esto a tu vida").
- Incluir 1 a 2 muletillas humanas naturales (mira, o sea, seamos sinceros, la verdad es que).
- Al menos 1 dato concreto/verificable (fecha, numero, caso puntual).
- PROHIBIDO: No uses comillas dobles en los textos generados.
- Las 'palabras_clave' DEBEN ser traducciones a INTENCIONES VISUALES en INGLÉS (ej. "red stock chart falling", "person worried laptop"). NO repitas el guion literalmente.
- Mantener coherencia total con el OUTLINE. Si hay conflicto de fuentes, respeta estrictamente: {state.precedence_rule}.

RUBRICA OBLIGATORIA (0-10):
- hook_score: fuerza de apertura y curiosidad inmediata.
- script_score: claridad, progresión y ritmo narrativo.
Si algún bloque queda < 7, reescribe internamente antes de responder.

AB TEST: Variante {ab_variant} | Plataforma: {platform}
Regla hook: {hook_rule}
ESTILO: {nicho.estilo_narrativo}
DIRECCIÓN VISUAL: {nicho.direccion_visual}
IDEAS_MANUALES_PRIORITARIAS: {' | '.join(state.manual_ideas[:6]) if state.manual_ideas else 'N/A'}
MEMORIA_LOCAL_NICHO: {' | '.join(state.niche_memory_entries[:6]) if state.niche_memory_entries else 'N/A'}
{precedence_block}
{correction_block}

Devuelve SOLO JSON válido, sin texto extra."""

        user = f"""Genera un SHORT viral de 30-45s para {nicho.nombre} tono {nicho.tono} en {platform}.
Usa variante {ab_variant}. Sigue el OUTLINE proporcionado.
El cierre del guion DEBE ser un micro-loop de curiosidad (sin pedir likes ni follow).

Devuelve EXACTAMENTE este JSON:
{{
  "num_clips": 10,
  "titulo": "titulo corto potente max 9 palabras",
  "gancho": "hook max 10 palabras, leible en <=2s, polarizante o curioso",
  "gancho_variants": ["gancho shock","gancho pregunta","gancho promesa"],
  "hooks_alternos": ["hook alterno A","hook alterno B","hook alterno C"],
  "hook_score": 9,
  "script_score": 8,
  "block_scores": {{
    "hook": 9,
    "desarrollo": 8,
    "micro_loop": 9
  }},
    "guion": "guion de STRICTAMENTE {word_min}-{word_max} palabras, frases cortas de 5-10 palabras, una idea por frase, terminando con el micro-loop",
  "micro_loop": "frase final corta que genera curiosidad (ej: 'pero lo peor todavia esta por venir...')",
  "cta": "frase corta opcional, puede repetir el micro_loop",
  "caption": "caption max 160 caracteres con 3 hashtags",
  "key_points": ["punto clave 1", "punto clave 2", "punto clave 3"],
  "palabras_clave": ["visual concept 1", "aesthetic scene 2", "b-roll action 3", "cinematic shot 4"],
  "mood_musica": "cinematic|motivational|dark|ambient|epic",
  "velocidad_cortes": "ultra_rapido",
  "prompt_imagen": "thumbnail prompt in English with {nicho.direccion_visual}",
  "duraciones_clips": [2.0,3.0,3.0,4.0,4.0,4.0,4.0,4.0,4.0,3.5],
  "viral_score": 9
}}"""

        text = self._call_llm(system, user, temperature=0.93)
        parsed = self._parse_json(text)

        if parsed:
            current_words = _script_word_count(parsed)
            if current_words < word_min:
                logger.warning(
                    f"ScriptAgent short output for {platform}: {current_words} words < {word_min}. Expanding."
                )
                rewrite_system = (
                    "Eres editor de guiones virales. "
                    "Amplia el guion para que sea mas explicativo y claro sin perder impacto. "
                    "Mantén tema, hook y micro-loop final. Devuelve SOLO JSON valido con la misma estructura."
                )
                rewrite_user = (
                    f"Reescribe este JSON para que el campo guion tenga entre {word_min} y {word_max} palabras "
                    f"en formato {platform}.\n\n"
                    f"JSON ACTUAL:\n{json.dumps(parsed, ensure_ascii=False)}"
                )
                rewritten = self._parse_json(self._call_llm(rewrite_system, rewrite_user, temperature=0.7))
                if rewritten and _script_word_count(rewritten) >= word_min:
                    parsed = rewritten
            elif current_words > word_max and getattr(settings, "enforce_duration_hard_limit", False):
                logger.warning(
                    f"ScriptAgent long output for {platform}: {current_words} words > {word_max}. "
                    "Trimming to enforce short-form rhythm."
                )
                parsed = self._trim_script_to_budget(parsed, word_max)

        if parsed:
            parsed = self._sanitize_script_payload(parsed)
            parsed["_ab_variant"] = ab_variant
            parsed["_platform"] = platform
            parsed["_model_used"] = getattr(self, "_last_model_used", settings.inference_model)
            parsed["_source_precedence"] = state.precedence_rule
            parsed["_reference_applied"] = bool(state.has_reference())
            parsed["_reference_url"] = state.reference_url if state.has_reference() else ""

        return parsed

    def _build_precedence_block(self, state: StoryState, nicho: NichoConfig) -> str:
        """Build source-priority context injected into prompts."""
        lines = [f"PRECEDENCIA DE FUENTES: {state.precedence_rule}"]

        if state.has_reference():
            lines.append(f"REFERENCE_URL: {state.reference_url}")
            if state.reference_title:
                lines.append(f"REFERENCE_TITLE: {state.reference_title}")
            if state.reference_delivery_promise:
                lines.append(f"REFERENCE_DELIVERY_PROMISE: {state.reference_delivery_promise}")
            if state.reference_hook_seconds > 0:
                lines.append(f"REFERENCE_HOOK_SECONDS: {state.reference_hook_seconds:.2f}")
            if state.reference_avg_cut_seconds > 0:
                lines.append(f"REFERENCE_AVG_CUT_SECONDS: {state.reference_avg_cut_seconds:.2f}")
            if state.reference_key_points:
                points = " | ".join(state.reference_key_points[:4])
                lines.append(f"REFERENCE_KEY_POINTS: {points}")
            elif state.reference_summary:
                lines.append(f"REFERENCE_SUMMARY: {state.reference_summary[:320]}")
        else:
            lines.append("REFERENCE: N/A")

        if state.research.recommended_angles:
            lines.append(f"RESEARCH_ANGLES: {', '.join(state.research.recommended_angles[:3])}")
        else:
            lines.append("RESEARCH_ANGLES: N/A")

        lines.append(f"NICHO_DEFAULT: {nicho.estilo_narrativo[:180]}")
        return "\n".join(lines)

    def _call_llm(self, system: str, user: str, temperature: float = 0.9) -> str:
        """Call LLM with Gemini primary (4-key rotation) + Azure GPT fallback."""
        text, model_used = call_llm_primary_gemini(
            system_prompt=system,
            user_prompt=user,
            temperature=temperature,
            timeout=60,
            max_retries=2,
            purpose="script_agent",
        )
        self._last_model_used = model_used or ""

        if text:
            logger.debug(f"ScriptAgent model used: {model_used}")
            return text

        logger.error("ScriptAgent: Gemini+GPT fallback failed")
        return ""

    def _trim_script_to_budget(self, payload: dict, word_max: int) -> dict:
        """Hard-trim a script JSON payload to fit the short-form word budget.

        Keeps the hook and early sentences, snaps to a sentence boundary, and
        appends the ``micro_loop`` (or a default curiosity tag) as closing.
        """
        if not isinstance(payload, dict):
            return payload
        text = str(payload.get("guion", "") or "").strip()
        if not text:
            return payload

        words = text.split()
        if len(words) <= word_max:
            return payload

        budget = max(20, word_max - 10)
        trimmed = " ".join(words[:budget]).rstrip(",;:- ")
        for stop in (".", "!", "?"):
            idx = trimmed.rfind(stop)
            if idx > len(trimmed) * 0.5:
                trimmed = trimmed[: idx + 1]
                break

        loop = str(payload.get("micro_loop", "") or "").strip()
        if not loop:
            loop = "pero lo peor todavia esta por venir..."
        payload["guion"] = f"{trimmed} {loop}".strip()
        payload["micro_loop"] = loop
        return payload

    def _sanitize_script_payload(self, payload: dict) -> dict:
        cleaned = dict(payload or {})
        cta = self._clean_sentence(str(cleaned.get("cta", "") or ""))
        script = str(cleaned.get("guion", "") or "").strip()
        script = self._remove_duplicate_cta(script, cta)
        cleaned["cta"] = cta
        cleaned["guion"] = script
        return cleaned

    def _remove_duplicate_cta(self, script: str, cta: str) -> str:
        sentences = self._split_sentences(script)
        if not sentences:
            return script.strip()

        # Common CTA phrases to block at the end of script field
        cta_keywords = {
            "comenta", "guarda", "sigueme", "suscribete", "comparte", "follow", "comment", "share", "save",
            "dale like", "toca el boton", "mira el link", "enlace", "biografia", "bio", "perfil",
        }

        def _is_cta_like(text: str) -> bool:
            norm = self._normalize_compare_text(text)
            tokens = set(norm.split())
            if tokens & cta_keywords:
                # If it has a keyword and is short, it's likely a CTA
                if len(tokens) < 15:
                    return True
            return False

        deduped: list[str] = []
        for sentence in sentences:
            if deduped and self._sentences_similar(deduped[-1], sentence):
                continue
            deduped.append(sentence)

        # Iteratively remove similar to CTA or CTA-like sentences from the end
        while deduped:
            last = deduped[-1]
            is_similar = cta and self._sentences_similar(last, cta)
            is_cta_like = _is_cta_like(last)
            
            if is_similar or is_cta_like:
                logger.debug(f"Removing duplicate/extra CTA from script end: '{last}'")
                deduped.pop()
            else:
                break

        return " ".join(deduped).strip()

    def _clean_sentence(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        normalized = re.sub(r"\s+([,.!?;:])", r"\1", normalized)
        return normalized

    def _split_sentences(self, text: str) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+|\n+", str(text or "").strip())
        return [self._clean_sentence(p) for p in parts if self._clean_sentence(p)]

    def _sentences_similar(self, left: str, right: str) -> bool:
        norm_left = self._normalize_compare_text(left)
        norm_right = self._normalize_compare_text(right)
        if not norm_left or not norm_right:
            return False
        if norm_left == norm_right:
            return True
        left_tokens = set(norm_left.split())
        right_tokens = set(norm_right.split())
        if not left_tokens or not right_tokens:
            return False
        overlap = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
        return overlap >= 0.72

    def _normalize_compare_text(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKD", str(text or "").lower())
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized

    def _parse_json(self, raw: str) -> Optional[dict]:
        """Parse JSON from LLM response (reuses V14 logic)."""
        text = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text)
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.strip()

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]

        text = re.sub(r",\s*([}\]])", r"\1", text)
        text = re.sub(r"[\x00-\x1f]", " ", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        try:
            fixed = re.sub(r"([{,]\s*)'([^']+)'\s*:", r'\1"\2":', text)
            fixed = re.sub(r":\s*'([^']*)'", r': "\1"', fixed)
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        logger.error(f"ScriptAgent: JSON parse failed. First 200: {raw[:200]}")
        return None
