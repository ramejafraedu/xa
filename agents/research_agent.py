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
from services.llm_router import call_llm_primary_gemini


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
        brief.precedence_rule = state.precedence_rule or (
            "REFERENCE > RESEARCH > NICHO_DEFAULT"
            if state.reference_url
            else "RESEARCH > NICHO_DEFAULT"
        )

        if state.reference_key_points:
            brief.reference_signals = state.reference_key_points[:5]
        elif state.reference_summary:
            brief.reference_signals = [state.reference_summary[:220]]

        # 1. Trending topics (existing V14 services)
        try:
            from services.trends import get_trending_context, get_trending_signals

            signals = get_trending_signals(nicho.nombre, settings.rapidapi_key)
            raw_trending = get_trending_context(nicho.nombre, settings.rapidapi_key)
            brief.trending_context_raw = raw_trending

            merged_topics = signals.get("merged_topics", [])
            if merged_topics:
                brief.trending_topics = merged_topics[:8]

            web_pool = (
                signals.get("youtube_hot", [])
                + signals.get("reddit_hot", [])
                + signals.get("news_headlines", [])
            )
            if web_pool:
                brief.web_sources = self._merge_unique(web_pool)[:8]

            if signals.get("tiktok_hashtags", []):
                brief.trending_topics = self._merge_unique(
                    brief.trending_topics + [f"#{tag}" for tag in signals.get("tiktok_hashtags", [])]
                )[:10]
        except Exception as e:
            logger.debug(f"Trending fetch failed: {e}")
            brief.trending_context_raw = f"Tendencias no disponibles — usa contexto del nicho: {nicho.nombre}"

        # Optional: web research plus (extra RSS enrichment)
        if settings.enable_web_research_plus:
            self._augment_web_research_plus(nicho, brief)

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

        if state.niche_memory_entries:
            local_memory = " | ".join(state.niche_memory_entries[:6])
            if memoria and memoria != "Sin memoria previa":
                memoria = f"{memoria} | MEMORIA_LOCAL: {local_memory}"
            else:
                memoria = f"MEMORIA_LOCAL: {local_memory}"

        if state.manual_ideas:
            manual_seed = self._merge_unique(state.manual_ideas)
            brief.recommended_angles = manual_seed[:3]

        # 3. LLM-powered angle recommendations
        try:
            generated_angles = self._generate_angles(nicho, brief, memoria, state)
            if state.manual_ideas:
                brief.recommended_angles = self._merge_unique(state.manual_ideas + generated_angles)[:4]
            else:
                brief.recommended_angles = generated_angles
        except Exception as e:
            logger.debug(f"Angle generation failed: {e}")
            # Fallback angles based on nicho
            brief.recommended_angles = self._fallback_angles(nicho)

        if brief.reference_signals:
            seeded = self._angles_from_reference_signals(brief.reference_signals)
            brief.recommended_angles = self._merge_unique(seeded + brief.recommended_angles)[:4]

        # 4. Hook suggestions
        try:
            brief.hook_suggestions = self._generate_hooks(nicho, brief, state)
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

    def _augment_web_research_plus(self, nicho: NichoConfig, brief: ResearchBrief) -> None:
        """Enrich research with extra web headlines (RSS-based, low-risk)."""
        try:
            from services.trends import get_google_news_rss

            q1 = nicho.nombre
            q2 = (brief.recommended_angles[0] if brief.recommended_angles else nicho.slug)
            headlines = self._merge_unique(get_google_news_rss(q1, limit=4) + get_google_news_rss(q2, limit=4))
            if headlines:
                brief.web_sources = self._merge_unique(brief.web_sources + headlines)[:8]
                web_line = "WEB HEADLINES: " + "; ".join(brief.web_sources[:4])
                brief.trending_context_raw = (
                    f"{brief.trending_context_raw} | {web_line}"
                    if brief.trending_context_raw
                    else web_line
                )
        except Exception as exc:
            logger.debug(f"Web research plus failed: {exc}")

    def _generate_angles(
        self,
        nicho: NichoConfig,
        brief: ResearchBrief,
        memoria: str,
        state: StoryState,
    ) -> list[str]:
        """Use LLM to suggest content angles based on trends + memory."""
        system = (
            "Eres un estratega de contenido viral. "
            "Sugiere 3 ángulos ÚNICOS para un video viral basado en el contexto. "
            "Cada ángulo debe ser una frase corta y accionable. "
            "Devuelve SOLO una lista JSON: [\"angulo1\", \"angulo2\", \"angulo3\"]"
        )

        user = (
            f"NICHO: {nicho.nombre}\n"
            f"TONO: {nicho.tono}\n"
            f"PRECEDENCIA: {brief.precedence_rule}\n"
            f"TRENDING: {brief.trending_context_raw[:300]}\n"
            f"SEÑALES_REFERENCIA: {' | '.join(brief.reference_signals[:4]) if brief.reference_signals else 'N/A'}\n"
            f"IDEAS_MANUALES: {' | '.join(state.manual_ideas[:6]) if state.manual_ideas else 'N/A'}\n"
            f"HISTORIAL: {str(memoria)[:300]}\n"
            f"ESTILO: {nicho.estilo_narrativo}\n\n"
            f"Sugiere 3 ángulos virales ORIGINALES respetando PRECEDENCIA."
        )

        text, _model_used = call_llm_primary_gemini(
            system_prompt=system,
            user_prompt=user,
            temperature=0.9,
            timeout=30,
            max_retries=2,
            purpose="research_angles",
        )

        if not text:
            return self._fallback_angles(nicho)

        import json, re
        text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text).strip()

        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])

        return self._fallback_angles(nicho)

    def _generate_hooks(self, nicho: NichoConfig, brief: ResearchBrief, state: StoryState) -> list[str]:
        """Generate viral hook suggestions."""
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
            f"PRECEDENCIA: {brief.precedence_rule}\n"
            f"IDEAS_MANUALES: {' | '.join(state.manual_ideas[:4]) if state.manual_ideas else 'N/A'}\n"
            f"REFERENCIA: {state.reference_summary[:180] if state.reference_summary else 'N/A'}\n"
            f"ESTILO: {nicho.estilo_narrativo[:100]}\n\n"
            f"5 hooks virales, formato: [\"hook1\", \"hook2\", ...]"
        )

        text, _model_used = call_llm_primary_gemini(
            system_prompt=system,
            user_prompt=user,
            temperature=0.95,
            timeout=25,
            max_retries=1,
            purpose="research_hooks",
        )

        if not text:
            return []

        import json, re
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

    @staticmethod
    def _angles_from_reference_signals(signals: list[str]) -> list[str]:
        """Convert reference signals into angle seeds."""
        seeds = []
        for s in signals[:3]:
            part = s.strip()
            if not part:
                continue
            seeds.append(f"Revelar la implicación oculta de: {part[:110]}")
        return seeds

    @staticmethod
    def _merge_unique(items: list[str]) -> list[str]:
        """Preserve order while removing duplicates and empties."""
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            clean = (item or "").strip()
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
        return out
