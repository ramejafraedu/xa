"""Video Factory V15 — Research Agent.

Goes beyond simple trending. Produces a ResearchBrief that tells
the ScriptAgent WHAT to write about, HOW to angle it, and WHAT to avoid.

Uses existing V14 services (trends, supabase) internally.
"""
from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from config import settings
from core.state import ResearchBrief, StoryState
from models.config_models import NichoConfig


class ResearchAgent:
    """Produce a research brief for the ScriptAgent.

    Combines:
      1. Google Trends RSS (existing)
      2. TikTok trending (existing)
      3. Supabase memory (what worked/failed before)
      4. LLM synthesis of recommended angles
    """

    def run(self, nicho: NichoConfig, state: StoryState) -> ResearchBrief:
        """Execute research phase.

        Args:
            nicho: Niche configuration.
            state: Current StoryState (will be updated in place).

        Returns:
            ResearchBrief with actionable insights.
        """
        t0 = time.time()
        brief = ResearchBrief()

        # 1. Trending topics (existing V14 services)
        try:
            from services.trends import get_trending_context
            raw_trending = get_trending_context(nicho.nombre, settings.rapidapi_key)
            brief.trending_context_raw = raw_trending

            # Parse individual topics from the raw string
            if "TRENDING" in raw_trending:
                parts = raw_trending.split(":")
                for part in parts[1:]:
                    topics = [t.strip() for t in part.split(",") if t.strip() and not t.strip().startswith("#")]
                    brief.trending_topics.extend(topics[:6])
        except Exception as e:
            logger.debug(f"Trending fetch failed: {e}")
            brief.trending_context_raw = f"Tendencias no disponibles — usa contexto del nicho: {nicho.nombre}"

        # 2. Supabase memory (what worked before)
        memoria = "Sin memoria previa"
        try:
            from services.supabase_client import read_memory
            memoria = read_memory(
                settings.supabase_url, settings.supabase_anon_key, nicho.slug
            )
            if memoria and "titulos_recientes" in str(memoria):
                brief.avoid_topics.append("Evitar repetir temas recientes del historial")
            brief.audience_insight = f"Historial del nicho disponible: {str(memoria)[:200]}"
        except Exception as e:
            logger.debug(f"Memory read failed: {e}")

        # 3. LLM-powered angle recommendations
        try:
            brief.recommended_angles = self._generate_angles(nicho, brief, memoria)
        except Exception as e:
            logger.debug(f"Angle generation failed: {e}")
            # Fallback angles based on nicho
            brief.recommended_angles = self._fallback_angles(nicho)

        # 4. Hook suggestions
        try:
            brief.hook_suggestions = self._generate_hooks(nicho, brief)
        except Exception as e:
            logger.debug(f"Hook generation failed: {e}")

        # Update state
        state.research = brief

        elapsed = round(time.time() - t0, 2)
        logger.info(
            f"🔍 Research complete: {len(brief.trending_topics)} trends, "
            f"{len(brief.recommended_angles)} angles, "
            f"{len(brief.hook_suggestions)} hooks ({elapsed}s)"
        )
        return brief

    def _generate_angles(
        self, nicho: NichoConfig, brief: ResearchBrief, memoria: str,
    ) -> list[str]:
        """Use LLM to suggest content angles based on trends + memory."""
        from services.http_client import request_with_retry

        system = (
            "Eres un estratega de contenido viral. "
            "Sugiere 3 ángulos ÚNICOS para un video viral basado en el contexto. "
            "Cada ángulo debe ser una frase corta y accionable. "
            "Devuelve SOLO una lista JSON: [\"angulo1\", \"angulo2\", \"angulo3\"]"
        )

        user = (
            f"NICHO: {nicho.nombre}\n"
            f"TONO: {nicho.tono}\n"
            f"TRENDING: {brief.trending_context_raw[:300]}\n"
            f"HISTORIAL: {str(memoria)[:300]}\n"
            f"ESTILO: {nicho.estilo_narrativo}\n\n"
            f"Sugiere 3 ángulos virales ORIGINALES."
        )

        payload = {
            "model": settings.inference_model,
            "temperature": 0.9,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Content-Type": "application/json",
        }

        response = request_with_retry(
            "POST", settings.inference_api_url,
            json_data=payload, headers=headers,
            max_retries=2, timeout=30,
        )

        if response.status_code >= 400:
            return self._fallback_angles(nicho)

        import json, re
        text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text).strip()

        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])

        return self._fallback_angles(nicho)

    def _generate_hooks(self, nicho: NichoConfig, brief: ResearchBrief) -> list[str]:
        """Generate viral hook suggestions."""
        from services.http_client import request_with_retry

        system = (
            "Eres experto en hooks virales de TikTok. "
            "Genera 5 hooks en español, cada uno de 8-14 palabras. "
            "Deben generar curiosidad inmediata. "
            "Devuelve SOLO una lista JSON."
        )

        angle = brief.recommended_angles[0] if brief.recommended_angles else nicho.nombre
        user = (
            f"NICHO: {nicho.nombre}\n"
            f"ÁNGULO: {angle}\n"
            f"ESTILO: {nicho.estilo_narrativo[:100]}\n\n"
            f"5 hooks virales, formato: [\"hook1\", \"hook2\", ...]"
        )

        payload = {
            "model": settings.inference_model,
            "temperature": 0.95,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.github_token}",
            "Content-Type": "application/json",
        }

        response = request_with_retry(
            "POST", settings.inference_api_url,
            json_data=payload, headers=headers,
            max_retries=1, timeout=25,
        )

        if response.status_code >= 400:
            return []

        import json, re
        text = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text).strip()

        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])[:5]

        return []

    def _fallback_angles(self, nicho: NichoConfig) -> list[str]:
        """Fallback angles when LLM is unavailable."""
        fallbacks = {
            "finanzas": [
                "Error financiero que el 90% comete sin saberlo",
                "Hábito de ricos que parece contraintuitivo",
                "Trampa bancaria que nadie te explica",
            ],
            "historia": [
                "Evento histórico censurado que cambió todo",
                "Conspiración que resultó ser verdad",
                "Detalle oscuro que los libros omiten",
            ],
            "curiosidades": [
                "Dato psicológico que explica tu comportamiento diario",
                "Fenómeno científico que desafía la lógica",
                "Truco mental que usan las marcas contra ti",
            ],
            "salud": [
                "Hábito 'saludable' que en realidad te daña",
                "Señal del cuerpo que todos ignoran",
                "Alimento común con efectos ocultos",
            ],
            "recetas": [
                "Error de cocina que arruina el sabor sin que lo notes",
                "Ingrediente secreto de chefs profesionales",
                "Receta viral con solo 3 ingredientes",
            ],
        }
        return fallbacks.get(nicho.slug, [
            "Dato impactante que pocos conocen",
            "Error común que casi todos cometen",
            "Secreto que cambia la perspectiva",
        ])
