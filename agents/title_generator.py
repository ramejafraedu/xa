"""Generador Automático de Títulos, Descripciones y Hashtags — Video Factory V16.1 PRO.

Agente SEO + Clickbait inteligente que genera:
- Títulos optimizados para YouTube Shorts (60-70 chars, clickbait por nicho)
- Descripciones con keywords SEO y call-to-action
- Hashtags relevantes (mix de viral + nicho)
- 3 variantes de cada elemento para A/B testing

Usa Gemini Flash 2.5 como LLM con prompts especializados por nicho.
Compatible con pipeline_v15.py y state_manager.py.

Comentarios en español. Código listo para producción V16.1
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


# ─────────────────────────────────────────────
# Prompts de sistema por nicho
# ─────────────────────────────────────────────

_SYSTEM_PROMPTS: dict[str, str] = {
    "curiosidades": (
        "Eres el mejor creador de contenido viral de YouTube Shorts en español. "
        "Especializas en videos de curiosidades y datos sorprendentes. "
        "Tus títulos siempre provocan shock, incredulidad o asombro. "
        "Usas números, preguntas retóricas y palabras de poder como: "
        "NUNCA TE CONTARON, INCREÍBLE, SECRETO, DESCUBIERTO, ¿SABÍAS QUE...?"
    ),
    "misterio": (
        "Eres el maestro del contenido de misterio y conspiración en YouTube Shorts. "
        "Tus títulos crean intriga, suspenso y FOMO extremo. "
        "Palabras clave favoritas: PROHIBIDO, OCULTO, NADIE SABE, "
        "LA VERDAD, DESCUBIERTO, EXPUESTO, LO QUE NO TE DICEN."
    ),
    "motivacion": (
        "Eres coach de motivación y desarrollo personal con millones de seguidores. "
        "Tus títulos inspiran acción inmediata y cambio de mentalidad. "
        "Usas: TRANSFORMA, CAMBIA TU VIDA, EL SECRETO, "
        "NUNCA ES TARDE, MENTALIDAD MILLONARIA, HÁBITOS."
    ),
    "historia": (
        "Eres historiador y narrador de hechos históricos fascinantes. "
        "Tus títulos revelan verdades ocultas de la historia. "
        "Palabras clave: LA VERDAD SOBRE, EL MAYOR SECRETO, "
        "LO QUE OCULTARON, REVISADO, EL DÍA QUE."
    ),
    "ciencia": (
        "Eres divulgador científico que hace la ciencia viral en español. "
        "Tus títulos hacen accesible lo complejo y provocan asombro. "
        "Usas: HA CAMBIADO TODO, DESCUBRIMIENTO, IMPOSIBLE PERO REAL, "
        "LA CIENCIA CONFIRMA, REVOLUCIONARIO."
    ),
    "default": (
        "Eres el mejor creador de contenido viral para YouTube Shorts en español. "
        "Tus títulos son irresistibles, generan clicks y alta retención. "
        "Combinas clickbait inteligente con valor real."
    ),
}

_HASHTAG_POOLS: dict[str, list[str]] = {
    "curiosidades": [
        "#curiosidades", "#datoscuriosos", "#aprendeyoutubeshortsespañol",
        "#¿sabíasque", "#hechosinsolitos", "#datosfascinantes",
        "#curiosidades2025", "#cortosyoutube"
    ],
    "misterio": [
        "#misterio", "#conspiración", "#lodesconocido", "#paranormal",
        "#secretos", "#misteriosinresolver", "#verdadesoccultas"
    ],
    "motivacion": [
        "#motivación", "#desarrollopersonal", "#mentalidadganadora",
        "#exito", "#emprendimiento", "#habitos", "#reflexiones",
        "#crecimientopersonal"
    ],
    "historia": [
        "#historia", "#historiamundial", "#historiainteresante",
        "#loquenotefcontaron", "#pasado", "#arqueologia"
    ],
    "ciencia": [
        "#ciencia", "#divulgacioncientifica", "#fisica", "#biologia",
        "#tecnologia", "#descubrimientos", "#cienciaycuriosidad"
    ],
    "viral": [
        "#shorts", "#youtubeshorts", "#viral", "#trending",
        "#parati", "#fyp", "#foryou", "#reels", "#tiktok",
        "#rapido", "#breve", "#cortometraje"
    ],
}

_DEFAULT_HASHTAGS_COUNT = 12


# ─────────────────────────────────────────────
# Función core: llamada a LLM
# ─────────────────────────────────────────────

def _call_gemini(prompt: str, api_key: str, model: str = "gemini-2.5-flash") -> str:
    """Llama a la API de Gemini y devuelve el texto de respuesta."""
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=api_key)
        gemini = genai.GenerativeModel(model)
        response = gemini.generate_content(
            prompt,
            generation_config={"temperature": 0.85, "max_output_tokens": 512},
        )
        return response.text.strip()
    except Exception as e:
        logger.warning(f"TitleGenerator: fallo Gemini — {e}")
        return ""


def _parse_numbered_list(text: str, expected: int = 3) -> list[str]:
    """Extrae una lista numerada del texto de respuesta del LLM."""
    lines = text.split("\n")
    results: list[str] = []
    for line in lines:
        cleaned = re.sub(r"^\s*\d+[\.\)]\s*", "", line).strip()
        if cleaned and len(cleaned) > 4:
            results.append(cleaned)
    return results[:expected] if results else [text[:80]] * min(expected, 1)


def _extract_hashtags(text: str) -> list[str]:
    """Extrae hashtags válidos de un texto."""
    tags = re.findall(r"#\w+", text)
    return list(dict.fromkeys(tag.lower() for tag in tags))  # deduplicar


# ─────────────────────────────────────────────
# Funciones de generación individual
# ─────────────────────────────────────────────

def generate_titles(
    guion: str,
    nicho: str,
    api_key: str,
    n: int = 3,
    model: str = "gemini-2.5-flash",
) -> list[str]:
    """Genera N títulos clickbait optimizados para YouTube Shorts."""
    system = _SYSTEM_PROMPTS.get(nicho.lower(), _SYSTEM_PROMPTS["default"])
    prompt = (
        f"{system}\n\n"
        f"Basándote en este guion de video de YouTube Shorts:\n"
        f"\"\"\"\n{guion[:800]}\n\"\"\"\n\n"
        f"Genera exactamente {n} títulos diferentes en español para este video. "
        f"Reglas: máximo 70 caracteres, formato ALL CAPS para palabras clave, "
        f"usar emojis relevantes, crear urgencia/curiosidad. "
        f"Devuelve SOLO la lista numerada (1. 2. 3.) sin explicación adicional."
    )
    raw = _call_gemini(prompt, api_key, model)
    titles = _parse_numbered_list(raw, n)
    logger.info(f"TitleGenerator: {len(titles)} títulos generados para nicho '{nicho}'")
    return titles


def generate_descriptions(
    guion: str,
    titulo: str,
    nicho: str,
    api_key: str,
    n: int = 3,
    model: str = "gemini-2.5-flash",
) -> list[str]:
    """Genera N descripciones SEO con CTA para YouTube Shorts."""
    system = _SYSTEM_PROMPTS.get(nicho.lower(), _SYSTEM_PROMPTS["default"])
    prompt = (
        f"{system}\n\n"
        f"Título del video: '{titulo}'\n"
        f"Guion (extracto): '{guion[:500]}'\n\n"
        f"Crea {n} descripciones distintas para YouTube Shorts (150-250 palabras cada una). "
        f"Cada descripción debe incluir: intro con keyword principal, párrafo de desarrollo, "
        f"3 bullet points de valor, CTA (suscribirse/like/comentar), y 5 keywords SEO al final. "
        f"Numbera las descripciones (1. 2. 3.) No incluyas hashtags aquí. "
        f"Devuelve SOLO las descripciones numeradas."
    )
    raw = _call_gemini(prompt, api_key, model)
    descriptions = _parse_numbered_list(raw, n)
    logger.info(f"TitleGenerator: {len(descriptions)} descripciones generadas")
    return descriptions


def generate_hashtags(
    titulo: str,
    nicho: str,
    api_key: str,
    count: int = _DEFAULT_HASHTAGS_COUNT,
    model: str = "gemini-2.5-flash",
) -> list[str]:
    """Genera hashtags optimizados mezclando LLM + pool curado por nicho."""
    # Pool curado base (siempre incluido)
    nicho_pool = _HASHTAG_POOLS.get(nicho.lower(), [])
    viral_pool = _HASHTAG_POOLS["viral"]
    base_tags = list(dict.fromkeys(nicho_pool[:5] + viral_pool[:4]))

    # Completar con LLM
    remaining = max(0, count - len(base_tags))
    if remaining > 0 and api_key:
        prompt = (
            f"Para un video de YouTube Shorts sobre: '{titulo}' (categoría: {nicho}), "
            f"genera {remaining} hashtags únicos en español relevantes para el tema y SEO. "
            f"Formato: solo hashtags separados por espacio, sin numeración, "
            f"sin explicación. Los hashtags deben ser de nicho específico, no genéricos."
        )
        raw = _call_gemini(prompt, api_key, model)
        llm_tags = _extract_hashtags(raw)
        base_tags = list(dict.fromkeys(base_tags + llm_tags))

    final_tags = base_tags[:count]
    logger.info(f"TitleGenerator: {len(final_tags)} hashtags generados")
    return final_tags


# ─────────────────────────────────────────────
# Clase principal: TitleGeneratorAgent (BaseTool)
# ─────────────────────────────────────────────

class TitleGeneratorAgent(BaseTool):
    """Agente generador de metadatos SEO para Video Factory V16.1.

    Genera títulos, descripciones y hashtags optimizados para
    YouTube Shorts usando Gemini Flash 2.5.
    """

    name = "title_generator"
    version = "1.0.0"
    tier = ToolTier.GENERATE
    capability = "metadata_generation"
    provider = "gemini_llm"
    stability = ToolStability.PRODUCTION
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # verificado dinámicamente
    install_instructions = (
        "Configura GOOGLE_API_KEY o GEMINI_API_KEY en el .env\n"
        "  → https://aistudio.google.com/apikey"
    )

    capabilities = [
        "generate_title",
        "generate_description",
        "generate_hashtags",
        "seo_optimization",
        "ab_test_metadata",
    ]
    best_for = [
        "Títulos virales para YouTube Shorts",
        "Descripciones SEO automáticas",
        "Hashtags de nicho + viral",
        "A/B testing de metadatos",
    ]
    not_good_for = [
        "contenido sin guion previo",
        "idiomas distintos al español (sin ajustar prompts)",
    ]

    input_schema = {
        "type": "object",
        "required": ["guion", "nicho"],
        "properties": {
            "guion": {
                "type": "string",
                "description": "Guion o resumen del video para contextualizar los metadatos",
            },
            "nicho": {
                "type": "string",
                "default": "default",
                "description": "Nicho: curiosidades, misterio, motivacion, historia, ciencia, default",
            },
            "titulo_actual": {
                "type": "string",
                "description": "Título actual (para mejorar/variar). Opcional.",
            },
            "variantes": {
                "type": "integer",
                "default": 3,
                "description": "Número de variantes de cada elemento",
            },
            "model": {
                "type": "string",
                "default": "gemini-2.5-flash",
                "description": "Modelo LLM a usar",
            },
            "include_hashtags": {
                "type": "boolean",
                "default": True,
            },
            "include_descriptions": {
                "type": "boolean",
                "default": True,
            },
            "hashtag_count": {
                "type": "integer",
                "default": 12,
                "description": "Número total de hashtags a generar",
            },
        },
    }

    output_schema = {
        "type": "object",
        "properties": {
            "titulos": {"type": "array", "items": {"type": "string"}},
            "descripciones": {"type": "array", "items": {"type": "string"}},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "titulo_recomendado": {"type": "string"},
            "descripcion_recomendada": {"type": "string"},
            "hashtags_string": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=10, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["guion", "nicho", "variantes"]
    side_effects = ["llama a Google Generative AI API"]
    user_visible_verification = [
        "Verifica que los títulos sean < 70 caracteres",
        "Comprueba que los hashtags incluyan tags virales",
        "Revisa la descripción tenga CTA claro",
    ]

    def _get_api_key(self) -> str | None:
        return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._get_api_key() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # Gemini Flash: ~$0.01 por 1K tokens, ~3 llamadas por ejecución
        return 0.01

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = self._get_api_key()
        if not api_key:
            return ToolResult(
                success=False,
                error="No se encontró GOOGLE_API_KEY. " + self.install_instructions,
            )

        start = time.time()
        guion: str = inputs["guion"]
        nicho: str = inputs.get("nicho", "default")
        titulo_actual: str = inputs.get("titulo_actual", "")
        n: int = int(inputs.get("variantes", 3))
        model: str = inputs.get("model", "gemini-2.5-flash")
        include_hashtags: bool = inputs.get("include_hashtags", True)
        include_desc: bool = inputs.get("include_descriptions", True)
        hashtag_count: int = int(inputs.get("hashtag_count", 12))

        # ── 1. Generar títulos ────────────────────────────────────
        titulos = generate_titles(guion, nicho, api_key, n, model)
        if not titulos:
            titulos = [titulo_actual or f"[Título pendiente para nicho: {nicho}]"]

        titulo_recomendado = titulos[0]

        # ── 2. Generar descripciones ──────────────────────────────
        descripciones: list[str] = []
        if include_desc:
            descripciones = generate_descriptions(
                guion, titulo_recomendado, nicho, api_key, n, model
            )

        descripcion_recomendada = descripciones[0] if descripciones else ""

        # ── 3. Generar hashtags ───────────────────────────────────
        hashtags: list[str] = []
        if include_hashtags:
            hashtags = generate_hashtags(titulo_recomendado, nicho, api_key, hashtag_count, model)

        hashtags_string = " ".join(hashtags)

        duration = round(time.time() - start, 2)
        logger.success(
            f"TitleGenerator: metadata generada en {duration}s — "
            f"{len(titulos)} títulos, {len(descripciones)} descripciones, {len(hashtags)} hashtags"
        )

        return ToolResult(
            success=True,
            data={
                "titulos": titulos,
                "descripciones": descripciones,
                "hashtags": hashtags,
                "titulo_recomendado": titulo_recomendado,
                "descripcion_recomendada": descripcion_recomendada,
                "hashtags_string": hashtags_string,
                "nicho": nicho,
            },
            cost_usd=self.estimate_cost(inputs),
            duration_seconds=duration,
            model=model,
        )


# ─────────────────────────────────────────────
# Helper standalone para el pipeline
# ─────────────────────────────────────────────

def generate_metadata(
    guion: str,
    nicho: str = "default",
    titulo_actual: str = "",
    variantes: int = 3,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Interfaz simplificada para el pipeline_v15.py.

    Ejemplo de uso::

        from agents.title_generator import generate_metadata
        meta = generate_metadata(
            guion=manifest.guion,
            nicho=manifest.nicho_slug,
            titulo_actual=manifest.titulo,
        )
        manifest.titulo = meta["titulo_recomendado"]
        manifest.descripcion = meta["descripcion_recomendada"]
        manifest.hashtags = meta["hashtags_string"]
    """
    agent = TitleGeneratorAgent()
    result = agent.execute({
        "guion": guion,
        "nicho": nicho,
        "titulo_actual": titulo_actual,
        "variantes": variantes,
        "model": model,
        "include_hashtags": True,
        "include_descriptions": True,
    })
    if result.success:
        return result.data
    logger.warning(f"TitleGenerator: falló LLM, devolviendo defaults — {result.error}")
    return {
        "titulos": [titulo_actual or f"Video de {nicho}"],
        "descripciones": [""],
        "hashtags": list(_HASHTAG_POOLS.get(nicho.lower(), []) + _HASHTAG_POOLS["viral"])[:12],
        "titulo_recomendado": titulo_actual,
        "descripcion_recomendada": "",
        "hashtags_string": " ".join(
            list(_HASHTAG_POOLS.get(nicho.lower(), []) + _HASHTAG_POOLS["viral"])[:12]
        ),
        "nicho": nicho,
    }
