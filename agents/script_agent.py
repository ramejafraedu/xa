"""Video Factory V15 — Script Agent.

Replaces the monolithic content_gen.py call with a structured approach:
1. Receives ResearchBrief + StoryState
2. Uses prompt chaining: outline first → then full script
3. Outputs structured scene-ready script with narrative coherence

Still uses the same GPT-4.1 backend as V14.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from loguru import logger

from config import settings
from core.state import StoryState
from models.config_models import NichoConfig
from services.http_client import request_with_retry


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

        system = (
            "Eres un director creativo de contenido viral. "
            "Genera un OUTLINE (esquema) para un video corto. "
            "NO escribas el guion completo — solo la estructura narrativa.\n\n"
            "Formato:\n"
            "HOOK: [concepto del gancho en 1 línea]\n"
            "BEAT 1: [primer punto de tensión]\n"
            "BEAT 2: [desarrollo/revelación]\n"
            "BEAT 3: [giro o dato impactante]\n"
            "PAYOFF: [cierre emocional]\n"
            "CTA: [llamada a acción]\n"
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
            f"{precedence_block}\n"
            f"{research_ctx}\n"
            f"TRENDING: {state.research.trending_context_raw[:200]}\n\n"
            f"Genera el OUTLINE del video."
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
        precedence_block = self._build_precedence_block(state, nicho)

        correction_block = ""
        if correction_notes:
            correction_block = (
                f"\n⚠️ CORRECCIÓN REQUERIDA:\n{correction_notes}\n"
                f"Mejora específicamente lo indicado sin cambiar la estructura general.\n"
            )
            
        few_shot_examples = """
EJEMPLOS DE GUIONES DE ALTA RETENCIÓN (COPIA ESTE RITMO):

EJEMPLO 1 (Misterio/Psicología):
"gancho": "Esta es la razón por la que te sientes cansado todo el tiempo...",
"guion": "Y no, no es por falta de sueño. Se llama 'fatiga de decisión'. Tu cerebro toma más de 35,000 decisiones al día. Al llegar a las 4 PM, tu corteza prefrontal está literalmente frita. Por eso pides comida chatarra en lugar de cocinar. El truco militar para evitarlo: planea tu día la noche anterior. Aplícalo hoy y verás la diferencia."

EJEMPLO 2 (Finanzas/Éxito):
"gancho": "Si ganas menos de $2000 al mes, deja de hacer esto...",
"guion": "Ahorrar el 10% no te hará rico. Te están mintiendo. La inflación está en 4%, el banco te da 1%. Pierdes dinero todos los días. Lo que necesitas no es ahorrar, es multiplicar. Aprender a vender o programar te dará un ROI del 1000%. Cambia tu mentalidad de consumidor a productor."
"""

        system = f"""Eres head writer de videos faceless top 1%. Objetivo: CTR alto y retención brutal.

CONTEXTO NARRATIVO:
{state.to_context_string()}

OUTLINE APROBADO:
{outline}
{few_shot_examples}

REGLAS MAESTRAS:
- Gancho en <=1.8 segundos con polarización real.
- En los primeros 3 segundos rompe una creencia popular o revela una trampa oculta.
- Escribe 3 variantes de gancho: shock, pregunta, promesa.
- Frases cortas de 5 a 12 palabras.
- Cliffhangers cada 8-10 segundos.
- Evita tono enciclopédico; usa conflicto, fricción y consecuencia directa.
- Incluir 2 a 4 muletillas humanas naturales (mira, o sea, te digo algo, ...).
- PROHIBIDO: No uses comillas dobles en los textos generados.
- Mantener coherencia total con el OUTLINE y el CONTEXTO NARRATIVO.
- Si hay conflicto de fuentes, respeta estrictamente: {state.precedence_rule}.

RUBRICA OBLIGATORIA (0-10):
- hook_score: fuerza de apertura y curiosidad inmediata.
- script_score: claridad, progresión y ritmo narrativo.
Si algún bloque queda < 7, reescribe internamente antes de responder.

AB TEST: Variante {ab_variant} | Plataforma: {platform}
Regla hook: {hook_rule}
ESTILO: {nicho.estilo_narrativo}
DIRECCIÓN VISUAL: {nicho.direccion_visual}
{precedence_block}
{correction_block}

Devuelve SOLO JSON válido, sin texto extra."""

        user = f"""Genera contenido viral para {nicho.nombre} tono {nicho.tono} en {platform}.
Usa variante {ab_variant}. Sigue el OUTLINE proporcionado.

Devuelve EXACTAMENTE este JSON:
{{
  "num_clips": 8,
  "titulo": "titulo corto potente max 9 palabras",
  "gancho": "gancho principal de 9 a 14 palabras",
  "gancho_variants": ["gancho shock","gancho pregunta","gancho promesa"],
  "hooks_alternos": ["hook alterno A","hook alterno B","hook alterno C"],
  "hook_score": 9,
  "script_score": 8,
  "block_scores": {{
    "hook": 9,
    "desarrollo": 8,
    "cierre": 8
  }},
  "guion": "guion de 90-150 palabras coherente con el OUTLINE",
  "cta": "cta breve natural de una oración",
  "caption": "caption max 160 caracteres con 3 hashtags",
  "key_points": ["punto clave 1", "punto clave 2", "punto clave 3"],
  "palabras_clave": ["kw1","kw2","kw3","kw4","kw5","kw6","kw7","kw8"],
  "mood_musica": "cinematic|motivational|dark|ambient|epic",
  "velocidad_cortes": "ultra_rapido|rapido|mixto|cinematografico",
  "prompt_imagen": "thumbnail prompt in English with {nicho.direccion_visual}",
  "duraciones_clips": [2.0,2.0,2.0,2.0,2.0,2.0,2.0,2.0],
  "viral_score": 9
}}"""

        text = self._call_llm(system, user, temperature=0.93)
        parsed = self._parse_json(text)

        if parsed:
            parsed["_ab_variant"] = ab_variant
            parsed["_platform"] = platform
            parsed["_model_used"] = settings.inference_model
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
        """Call Gemini usando google-genai SDK con rotación de las 4 API keys."""
        try:
            import google.genai as genai
            from google.genai import types
        except ImportError:
            logger.error("google-genai no está instalado. Instala con: pip install google-genai")
            return ""

        all_keys = settings.get_gemini_keys()
        if not all_keys:
            logger.error("No Gemini API keys found for ScriptAgent")
            return ""

        # Modelos ordenados por cuota disponible (mayor RPM primero)
        models_to_try = [
            "gemini-3.1-flash-lite",   # 15 RPM — mayor cuota free
            "gemini-2.5-flash-lite",   # 10 RPM
            "gemini-2.5-flash",        # 5 RPM
            "gemini-3-flash",          # 5 RPM
            "gemini-3.1-pro",          # 0 RPM free / cuota pago
            "gemini-3.1-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        ]
        last_error = ""

        # Rotación agresiva: por cada modelo, intenta con todas las keys
        # 4 keys × 15 RPM = ~60 RPM efectivos en gemini-3.1-flash-lite
        for model_name in models_to_try:
            for key_idx, api_key in enumerate(all_keys):
                try:
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[user],
                        config=types.GenerateContentConfig(
                            system_instruction=system,
                            temperature=temperature,
                        )
                    )
                    if response.text:
                        logger.debug(f"ScriptAgent: {model_name} con key #{key_idx+1}")
                        return response.text
                    last_error = f"Empty response from {model_name}"
                except Exception as e:
                    last_error = str(e)
                    err_str = str(e).lower()
                    # RESOURCE_EXHAUSTED → probar siguiente key de inmediato
                    if "resource_exhausted" in err_str or "quota" in err_str or "429" in err_str:
                        logger.debug(f"{model_name} key#{key_idx+1} agotada, rotando...")
                        continue
                    # Error de modelo (NOT_FOUND, INVALID_ARGUMENT) → siguiente modelo
                    if "not_found" in err_str or "invalid" in err_str or "404" in err_str:
                        logger.debug(f"{model_name} no disponible, saltando modelo")
                        break
                    logger.warning(f"{model_name} key#{key_idx+1} error: {e}")

            # Pausa breve entre modelos para evitar flood
            time.sleep(0.5)

        logger.error(f"ScriptAgent: todos los modelos/keys fallaron. Último: {last_error}")
        return ""

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
