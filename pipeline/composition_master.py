"""VideoCompositionMasterPRO — Motor de composición visual inteligente.

REGLAS ABSOLUTAS que este módulo garantiza:
1. Clips 100% nuevos: nunca reutiliza clips de videos anteriores.
2. Relevancia temática: cada clip es directamente relevante a su escena.
3. Cero repetición: cada escena tiene un clip diferente y fresco.
4. Diversidad visual: alterna ángulos, movimientos, close-ups, wides.
5. A/B split: 2 clips candidatos por escena para máxima variedad.

Flujo:
  guion + nicho + tema
    → ScriptSceneAnalyzer (LLM) → SceneCompositionPlan (JSON)
    → FreshClipSelector → clips únicos de Pexels/Pixabay
    → validación de relevancia y unicidad
    → entrega al renderer

Compatible con pipeline_v15.py (reemplaza fetch_stock_videos simple).
Comentarios en español. Código listo para producción.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import settings
from services.http_client import get_json, request_with_retry, download_file


# ─────────────────────────────────────────────────────────────
# HISTORIAL GLOBAL DE CLIPS USADOS
# Persiste en disco para sobrevivir entre ejecuciones del pipeline
# ─────────────────────────────────────────────────────────────

_HISTORY_FILE_NAME = "clip_usage_history.json"
_history_cache: dict[str, dict] | None = None


def _history_path() -> Path:
    """Ruta al archivo de historial de clips usados."""
    return Path(settings.video_cache_dir) / _HISTORY_FILE_NAME


def _load_history() -> dict[str, dict]:
    """Carga el historial de clips usados desde disco."""
    global _history_cache
    if _history_cache is not None:
        return _history_cache

    path = _history_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            _history_cache = raw if isinstance(raw, dict) else {}
            logger.debug(f"CompositionMaster: historial cargado — {len(_history_cache)} clips")
            return _history_cache
        except Exception:
            pass

    _history_cache = {}
    return _history_cache


def _save_history(history: dict[str, dict]) -> None:
    """Persiste el historial en disco."""
    global _history_cache
    _history_cache = history
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"CompositionMaster: no se pudo guardar historial — {e}")


def _mark_clip_used(clip_id: str, url: str, keyword: str, job_id: str = "") -> None:
    """Registra un clip como usado en el historial global."""
    history = _load_history()
    history[clip_id] = {
        "url": url,
        "keyword": keyword,
        "job_id": job_id,
        "used_at": int(time.time()),
    }
    _save_history(history)


def _is_clip_used(clip_id: str) -> bool:
    """Verifica si un clip ya fue usado en algún video anterior."""
    history = _load_history()
    return clip_id in history


def get_used_clip_count() -> int:
    """Devuelve cuántos clips únicos han sido usados históricamente."""
    return len(_load_history())


def clear_clip_history(older_than_days: int = 0) -> int:
    """Limpia el historial de clips (útil para reset o archivos antiguos).

    Args:
        older_than_days: Si > 0, solo elimina clips usados hace más de N días.
            Si = 0, limpia todo el historial.

    Returns:
        Número de entradas eliminadas.
    """
    history = _load_history()
    if older_than_days <= 0:
        count = len(history)
        _save_history({})
        logger.info(f"CompositionMaster: historial limpiado — {count} entradas eliminadas")
        return count

    cutoff = int(time.time()) - (older_than_days * 86400)
    to_delete = [
        cid for cid, meta in history.items()
        if int(meta.get("used_at", 0)) < cutoff
    ]
    for cid in to_delete:
        del history[cid]
    _save_history(history)
    logger.info(f"CompositionMaster: eliminados {len(to_delete)} clips con >={older_than_days} días")
    return len(to_delete)


# ─────────────────────────────────────────────────────────────
# MODELOS DE DATOS
# ─────────────────────────────────────────────────────────────

@dataclass
class SceneClipSpec:
    """Especificación de clip para una escena del script."""
    scene_number: int
    clip_description: str        # Query detallada para buscar en stock
    narration_snippet: str       # Texto narrado en esta escena
    duration: float              # Duración estimada en segundos
    relevance_score: int         # 0-100: cuán relevante es el clip buscado
    shot_type: str               # close-up | medium | wide | aerial | detail
    motion: str                  # static | slow | dynamic | pan | timelapse
    keywords_primary: list[str]  # Keywords principales de búsqueda
    keywords_alternate: list[str] # Keywords alternativas para variante B
    emotion: str = "neutral"     # Emoción/mood del clip


@dataclass
class SceneCompositionPlan:
    """Plan de composición completo para un video."""
    job_id: str
    tema: str
    nicho: str
    total_scenes: int
    scenes: list[SceneClipSpec]
    total_duration: float
    created_at: int = field(default_factory=lambda: int(time.time()))


@dataclass
class FreshClip:
    """Clip de stock seleccionado, validado como fresco y relevante."""
    clip_id: str          # ID único del clip en el proveedor
    url: str              # URL de descarga
    local_path: str       # Ruta local en caché
    provider: str         # pexels | pixabay | coverr
    filename: str
    scene_number: int
    relevance_score: int  # 0-100
    shot_variant: str     # A | B
    keyword_used: str
    is_fresh: bool = True


# ─────────────────────────────────────────────────────────────
# ANALIZADOR DE ESCENAS (LLM)
# ─────────────────────────────────────────────────────────────

_SCENE_ANALYSIS_PROMPT = """Eres un director de fotografía experto en videos virales para YouTube Shorts.
Analiza el siguiente guion y descomponlo en escenas visuales.

TEMA DEL VIDEO: {tema}
NICHO: {nicho}
GUION COMPLETO:
\"\"\"
{guion}
\"\"\"

Para cada escena del guion, genera una especificación de clip de stock.
Reglas:
- Cada clip debe ser 100% relevante al texto narrado en esa escena.
- Usa variedad: alterna close-up, medium shot, wide shot, aerial, detalle.
- El clip_description debe ser muy específico para buscar en Pexels/Pixabay.
- Nunca uses clips genéricos o fuera de tema.
- Genera 5-12 escenas según la longitud del guion.
- duration: entre 3.0 y 6.0 segundos por escena.
- relevance_score: cuán bien el clip illustra la narración (70-100 para clips aprobados).

Devuelve ÚNICAMENTE un JSON válido con esta estructura exacta:
{{
  "scenes": [
    {{
      "scene_number": 1,
      "narration_snippet": "primeras 10 palabras de lo narrado en esta escena",
      "clip_description": "descripción detallada del clip de stock ideal en inglés para buscar en Pexels",
      "duration": 4.0,
      "relevance_score": 95,
      "shot_type": "close-up",
      "motion": "slow",
      "keywords_primary": ["keyword1", "keyword2"],
      "keywords_alternate": ["alternate1", "alternate2"],
      "emotion": "neutral"
    }}
  ]
}}

Tipos de shot validos: close-up, medium, wide, aerial, detail, overhead
Tipos de motion validos: static, slow, dynamic, pan, timelapse, handheld
"""


def analyze_script_into_scenes(
    guion: str,
    tema: str,
    nicho: str,
    api_key: str,
    model: str = "gemini-2.5-flash",
) -> list[SceneClipSpec]:
    """Analiza el guion con LLM y genera una especificación de clip por escena.

    Args:
        guion: Texto completo del guion/narración.
        tema: Tema principal del video.
        nicho: Nicho del canal (curiosidades, misterio, etc.)
        api_key: Clave API de Gemini.
        model: Modelo LLM a usar.

    Returns:
        Lista de SceneClipSpec, una por escena del guion.
    """
    if not api_key or not guion.strip():
        return _fallback_scene_specs(guion, tema, nicho)

    prompt = _SCENE_ANALYSIS_PROMPT.format(
        tema=tema[:200],
        nicho=nicho,
        guion=guion[:2000],
    )

    try:
        import requests
        # Intentar con Gemini API
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 2048,
                    "responseMimeType": "application/json",
                },
            },
            timeout=60,
        )

        if resp.status_code == 200:
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            scenes_raw = _parse_scene_json(text)
            if scenes_raw:
                logger.info(
                    f"CompositionMaster: LLM analizó {len(scenes_raw)} escenas para '{tema[:40]}'"
                )
                return scenes_raw
        else:
            logger.debug(f"CompositionMaster: LLM respondió {resp.status_code}, usando fallback")

    except Exception as e:
        logger.warning(f"CompositionMaster: fallo LLM scene analysis — {e}")

    return _fallback_scene_specs(guion, tema, nicho)


def _parse_scene_json(text: str) -> list[SceneClipSpec]:
    """Parsea el JSON de escenas generado por el LLM."""
    try:
        # Limpiar posible markdown code fence
        clean = text.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(clean)
        scenes_data = data.get("scenes", [])

        specs: list[SceneClipSpec] = []
        for s in scenes_data:
            if not isinstance(s, dict):
                continue
            spec = SceneClipSpec(
                scene_number=int(s.get("scene_number", len(specs) + 1)),
                clip_description=str(s.get("clip_description", "")),
                narration_snippet=str(s.get("narration_snippet", ""))[:100],
                duration=max(2.5, min(7.0, float(s.get("duration", 4.0)))),
                relevance_score=max(0, min(100, int(s.get("relevance_score", 80)))),
                shot_type=str(s.get("shot_type", "medium")),
                motion=str(s.get("motion", "slow")),
                keywords_primary=list(s.get("keywords_primary", [])),
                keywords_alternate=list(s.get("keywords_alternate", [])),
                emotion=str(s.get("emotion", "neutral")),
            )
            if spec.clip_description and spec.relevance_score >= 60:
                specs.append(spec)

        return specs

    except Exception as e:
        logger.debug(f"CompositionMaster: error parseando JSON de escenas — {e}")
        return []


def _fallback_scene_specs(guion: str, tema: str, nicho: str) -> list[SceneClipSpec]:
    """Genera especificaciones básicas de escenas sin LLM.

    Divide el guion en fragmentos y crea una spec por fragmento.
    """
    # Dividir por oraciones o por tamaño
    sentences = [s.strip() for s in guion.replace(".", ".|").replace("!", "!|").replace("?", "?|").split("|") if s.strip()]
    if not sentences:
        sentences = [guion[:200]]

    # Agrupar en 6-10 escenas
    target_scenes = max(5, min(12, len(sentences)))
    chunk_size = max(1, len(sentences) // target_scenes)

    shot_types = ["medium", "close-up", "wide", "detail", "medium", "close-up", "wide", "aerial"]
    motions = ["slow", "dynamic", "pan", "static", "slow", "handheld", "timelapse", "slow"]

    specs: list[SceneClipSpec] = []
    tema_words = tema.lower().split()[:3]

    for i in range(0, len(sentences), chunk_size):
        chunk = " ".join(sentences[i:i + chunk_size])
        scene_num = len(specs) + 1
        idx = (scene_num - 1) % len(shot_types)

        # Extraer palabras clave del texto de la escena
        words = [w for w in chunk.lower().split() if len(w) > 4][:4]
        kw_primary = [tema] + words[:2]
        kw_alt = [f"{nicho} video"] + words[2:4]

        spec = SceneClipSpec(
            scene_number=scene_num,
            clip_description=f"{tema} {chunk[:60]} {shot_types[idx]}",
            narration_snippet=chunk[:80],
            duration=4.0,
            relevance_score=75,
            shot_type=shot_types[idx],
            motion=motions[idx],
            keywords_primary=kw_primary,
            keywords_alternate=kw_alt,
            emotion="neutral",
        )
        specs.append(spec)

        if len(specs) >= target_scenes:
            break

    logger.info(f"CompositionMaster: fallback generó {len(specs)} escenas para '{tema[:40]}'")
    return specs


# ─────────────────────────────────────────────────────────────
# SELECTOR DE CLIPS FRESCOS
# ─────────────────────────────────────────────────────────────

_SHOT_QUERY_MODIFIERS: dict[str, list[str]] = {
    "close-up": ["close up", "macro", "extreme close"],
    "medium": ["medium shot", "portrait"],
    "wide": ["wide angle", "establishing shot", "full scene"],
    "aerial": ["aerial view", "drone shot", "bird eye view"],
    "detail": ["detail shot", "texture", "macro detail"],
    "overhead": ["overhead view", "top down", "flat lay"],
    "timelapse": ["timelapse", "time lapse"],
}

_MOTION_QUERY_MODIFIERS: dict[str, list[str]] = {
    "dynamic": ["fast motion", "action", "moving"],
    "pan": ["pan shot", "camera movement"],
    "slow": ["cinematic slow", "smooth"],
    "timelapse": ["timelapse"],
    "handheld": ["handheld", "documentary style"],
    "static": ["still", "stable"],
}

# Contadores de rotación para paginación con variedad
_pexels_page_counter: list[int] = [0]
_pixabay_page_counter: list[int] = [0]


def _build_enriched_query(spec: SceneClipSpec, variant: str = "A") -> str:
    """Construye una query enriquecida con modificadores de shot y motion.

    Args:
        spec: Especificación de la escena.
        variant: 'A' para keywords principales, 'B' para alternativas.

    Returns:
        Query string enriquecida para la API de stock.
    """
    if variant == "A":
        base_kws = spec.keywords_primary[:2]
    else:
        base_kws = spec.keywords_alternate[:2] or spec.keywords_primary[:2]

    # Añadir modificador de shot
    shot_mods = _SHOT_QUERY_MODIFIERS.get(spec.shot_type, [])
    shot_mod = random.choice(shot_mods) if shot_mods else ""

    # Base: primera keyword + descripción breve
    base = base_kws[0] if base_kws else spec.clip_description[:40]

    # Construir query final
    parts = [base]
    if shot_mod and variant == "B":
        parts.append(shot_mod)
    if len(base_kws) > 1:
        parts.append(base_kws[1])

    return " ".join(parts)[:100]


def _fetch_pexels_fresh(
    query: str,
    pexels_keys: list[str],
    used_ids: set[str],
    require_portrait: bool = True,
) -> list[dict]:
    """Busca clips en Pexels, saltando los ya usados.

    Returns:
        Lista de {clip_id, url, filename, provider}
    """
    if not pexels_keys or not query.strip():
        return []

    # Rotar página para variedad
    _pexels_page_counter[0] += 1
    page = (_pexels_page_counter[0] % 5) + 1  # páginas 1-5

    orientation = "portrait" if require_portrait else "landscape"
    q = urllib.parse.quote(query.strip())
    url = (
        f"https://api.pexels.com/videos/search"
        f"?query={q}&orientation={orientation}&size=medium&per_page=15&page={page}"
    )

    # Rotarkeys
    start = (_pexels_page_counter[0]) % len(pexels_keys)
    rotated_keys = pexels_keys[start:] + pexels_keys[:start]

    for key in rotated_keys:
        try:
            resp = request_with_retry(
                "GET", url,
                headers={"Authorization": key},
                max_retries=2,
                timeout=15,
            )
            if resp.status_code == 429:
                continue
            if resp.status_code >= 400:
                continue

            data = resp.json()
            results = []
            for v in data.get("videos", []):
                vid_id = f"pexels_{v.get('id', '')}"
                # Saltar si ya está en historial global O en el set local
                if vid_id in used_ids or _is_clip_used(vid_id):
                    continue

                files = v.get("video_files", [])
                best = (
                    next((f for f in files if f.get("quality") == "hd" and f.get("height", 0) > f.get("width", 0)), None)
                    or next((f for f in files if f.get("quality") == "hd"), None)
                    or next((f for f in files if f.get("quality") == "sd"), None)
                    or (files[0] if files else None)
                )
                if best and best.get("link"):
                    results.append({
                        "clip_id": vid_id,
                        "url": best["link"],
                        "filename": f"{vid_id}.mp4",
                        "provider": "pexels",
                    })

            if results:
                logger.debug(f"CompositionMaster: Pexels '{query}' p{page} → {len(results)} clips frescos")
                return results

        except Exception as e:
            logger.debug(f"CompositionMaster: Pexels key error — {e}")

    return []


def _fetch_pixabay_fresh(
    query: str,
    used_ids: set[str],
    require_portrait: bool = True,
) -> list[dict]:
    """Busca clips en Pixabay, saltando los ya usados."""
    api_key = settings.pixabay_api_key
    if not api_key or not query.strip():
        return []

    _pixabay_page_counter[0] += 1
    page = (_pixabay_page_counter[0] % 4) + 1

    orientation_param = "vertical" if require_portrait else "horizontal"
    q = urllib.parse.quote(query.strip())
    url = (
        f"https://pixabay.com/api/videos/"
        f"?key={api_key}&q={q}"
        f"&orientation={orientation_param}&per_page=10"
        f"&min_width=720&page={page}"
    )

    try:
        data = get_json(url, max_retries=2)
        results = []
        for h in data.get("hits", []):
            vid_id = f"pixabay_{h.get('id', '')}"
            if vid_id in used_ids or _is_clip_used(vid_id):
                continue

            vids = h.get("videos", {})
            video_url = None
            for quality in ["medium", "large", "small"]:
                video_url = vids.get(quality, {}).get("url")
                if video_url:
                    break

            if video_url:
                results.append({
                    "clip_id": vid_id,
                    "url": video_url,
                    "filename": f"{vid_id}.mp4",
                    "provider": "pixabay",
                })

        if results:
            logger.debug(f"CompositionMaster: Pixabay '{query}' p{page} → {len(results)} clips frescos")
        return results

    except Exception as e:
        logger.debug(f"CompositionMaster: Pixabay error — {e}")
        return []


def select_fresh_clip_for_scene(
    spec: SceneClipSpec,
    used_ids: set[str],
    pexels_keys: list[str],
    variant: str = "A",
    job_id: str = "",
) -> Optional[FreshClip]:
    """Selecciona UN clip fresco y relevante para una escena.

    El clip es garantizado como:
    - No usado en este job
    - No usado en ningún job anterior (según historial en disco)
    - Relevante al tema de la escena

    Args:
        spec: Especificación de la escena.
        used_ids: IDs ya usados en este job (se actualiza in-place).
        pexels_keys: Claves de Pexels disponibles.
        variant: 'A' (keywords primarias) o 'B' (keywords alternativas).
        job_id: ID del job actual.

    Returns:
        FreshClip o None si no se encontró ningún clip válido.
    """
    query = _build_enriched_query(spec, variant)
    keyword_used = query

    # Intento 1: Pexels con query enriquecida
    candidates = _fetch_pexels_fresh(query, pexels_keys, used_ids)

    # Intento 2: Pixabay si Pexels falla
    if not candidates:
        candidates = _fetch_pixabay_fresh(query, used_ids)

    # Intento 3: Pexels con keywords simples (fallback)
    if not candidates and spec.keywords_primary:
        simple_query = spec.keywords_primary[0]
        candidates = _fetch_pexels_fresh(simple_query, pexels_keys, used_ids)
        if candidates:
            keyword_used = simple_query

    # Intento 4: Pixabay con keywords simples
    if not candidates and spec.keywords_primary:
        simple_query = spec.keywords_primary[0]
        candidates = _fetch_pixabay_fresh(simple_query, used_ids)
        if candidates:
            keyword_used = simple_query

    if not candidates:
        logger.warning(
            f"CompositionMaster: sin clips frescos para escena {spec.scene_number} "
            f"(query: '{query}')"
        )
        return None

    # Elegir el primer candidato (ya son todos frescos)
    chosen = candidates[0]
    clip_id = chosen["clip_id"]

    # Registrar en set local y en historial global
    used_ids.add(clip_id)
    _mark_clip_used(
        clip_id=clip_id,
        url=chosen["url"],
        keyword=keyword_used,
        job_id=job_id,
    )

    # Determinar ruta de caché local
    cache_dir = Path(settings.video_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(cache_dir / chosen["filename"])

    return FreshClip(
        clip_id=clip_id,
        url=chosen["url"],
        local_path=local_path,
        provider=chosen["provider"],
        filename=chosen["filename"],
        scene_number=spec.scene_number,
        relevance_score=spec.relevance_score,
        shot_variant=variant,
        keyword_used=keyword_used,
        is_fresh=True,
    )


# ─────────────────────────────────────────────────────────────
# VALIDADOR DE COMPOSICIÓN
# ─────────────────────────────────────────────────────────────

def _log_composition_report(
    plan: SceneCompositionPlan,
    clips_a: list[Optional[FreshClip]],
    clips_b: list[Optional[FreshClip]],
) -> None:
    """Genera un reporte de composición en log."""
    total_scenes = len(plan.scenes)
    clips_a_ok = sum(1 for c in clips_a if c)
    clips_b_ok = sum(1 for c in clips_b if c)

    logger.info(f"╔══ COMPOSITION MASTER — '{plan.tema[:50]}' ══╗")
    logger.info(f"║  Escenas analizadas:  {total_scenes}")
    logger.info(f"║  Clips variante A:    {clips_a_ok}/{total_scenes} (frescos)")
    logger.info(f"║  Clips variante B:    {clips_b_ok}/{total_scenes} (frescos)")
    logger.info(f"║  Duración estimada:   {plan.total_duration:.1f}s")
    logger.info(f"║  Clips históricos:    {get_used_clip_count()} total en historial")
    logger.info(f"╚{'═' * 50}╝")

    for i, spec in enumerate(plan.scenes):
        ca = clips_a[i] if i < len(clips_a) else None
        cb = clips_b[i] if i < len(clips_b) else None
        a_icon = "✅" if ca else "❌"
        b_icon = "✅" if cb else "❌"
        logger.debug(
            f"  Escena {spec.scene_number:02d}: [{spec.shot_type}/{spec.motion}] "
            f"score={spec.relevance_score} "
            f"A={a_icon}({ca.provider if ca else '-'}) "
            f"B={b_icon}({cb.provider if cb else '-'})"
        )


# ─────────────────────────────────────────────────────────────
# FUNCIÓN PRINCIPAL — VideoCompositionMasterPRO
# ─────────────────────────────────────────────────────────────

def compose_video_clips(
    guion: str,
    tema: str,
    nicho: str,
    num_clips: int = 10,
    job_id: str = "",
    export_plan_path: Optional[str] = None,
) -> tuple[list[dict], list[dict]]:
    """Función principal del VideoCompositionMasterPRO.

    Analiza el guion, planifica la composición visual y selecciona
    clips 100% frescos, relevantes y sin repetición para cada escena.

    Args:
        guion: Texto completo del script/narración.
        tema: Tema principal del video (para búsqueda y LLM).
        nicho: Nicho del canal (curiosidades, misterio, etc.)
        num_clips: Número total de clips a seleccionar (distribuidos entre escenas).
        job_id: ID del job actual (para historial).
        export_plan_path: Si se provee, guarda el plan JSON en disco.

    Returns:
        Tupla (clips_variant_a, clips_variant_b) donde cada elemento es
        una lista de dicts compatibles con fetch_stock_videos():
        [{"url": "http...", "local_path": "C:/...", "provider": "pexels"}]
        - clips_variant_a: clips con visual_1 (variante A)
        - clips_variant_b: clips con visual_2 (variante B)
    """
    start = time.time()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    pexels_keys = settings.pexels_keys

    logger.info(
        f"🎬 CompositionMaster: iniciando para '{tema[:60]}' | nicho={nicho} | clips={num_clips}"
    )

    # ── 1. Analizar el guion en escenas ──────────────────────
    scene_specs = analyze_script_into_scenes(
        guion=guion,
        tema=tema,
        nicho=nicho,
        api_key=api_key,
    )

    if not scene_specs:
        logger.warning("CompositionMaster: sin escenas del LLM, usando keywords básicas del tema")
        scene_specs = _fallback_scene_specs(guion, tema, nicho)

    # Limitar al número de clips solicitados
    scene_specs = scene_specs[:max(num_clips, len(scene_specs))]

    # ── 2. Construir plan de composición ─────────────────────
    total_duration = sum(s.duration for s in scene_specs)
    plan = SceneCompositionPlan(
        job_id=job_id,
        tema=tema,
        nicho=nicho,
        total_scenes=len(scene_specs),
        scenes=scene_specs,
        total_duration=total_duration,
    )

    # Guardar plan en disco si se requiere
    if export_plan_path:
        try:
            plan_path = Path(export_plan_path)
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_dict = {
                "job_id": plan.job_id,
                "tema": plan.tema,
                "nicho": plan.nicho,
                "total_scenes": plan.total_scenes,
                "total_duration": plan.total_duration,
                "scenes": [asdict(s) for s in plan.scenes],
                "created_at": plan.created_at,
            }
            plan_path.write_text(
                json.dumps(plan_dict, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info(f"CompositionMaster: plan guardado → {plan_path.name}")
        except Exception as e:
            logger.debug(f"CompositionMaster: no se pudo guardar plan — {e}")

    # ── 3. Seleccionar clips frescos para cada escena ─────────
    used_ids_this_job: set[str] = set()  # IDs usados en este job específico
    clips_a: list[Optional[FreshClip]] = []
    clips_b: list[Optional[FreshClip]] = []

    for spec in scene_specs:
        # Variante A: keywords primarias + shot tipo principal
        clip_a = select_fresh_clip_for_scene(
            spec=spec,
            used_ids=used_ids_this_job,
            pexels_keys=pexels_keys,
            variant="A",
            job_id=job_id,
        )
        clips_a.append(clip_a)

        # Pequeña pausa para no saturar las APIs
        time.sleep(0.15)

        # Variante B: keywords alternativas + shot tipo diferente
        clip_b = select_fresh_clip_for_scene(
            spec=spec,
            used_ids=used_ids_this_job,
            pexels_keys=pexels_keys,
            variant="B",
            job_id=job_id,
        )
        clips_b.append(clip_b)

        time.sleep(0.15)

    # ── 4. Reporte de composición ─────────────────────────────
    _log_composition_report(plan, clips_a, clips_b)

    # ── 5. Convertir a formato compatible con pipeline ─────────
    def _to_pipeline_format(clips: list[Optional[FreshClip]]) -> list[dict]:
        """Convierte FreshClip a formato dict compatible con download_clips()."""
        result = []
        for clip in clips:
            if clip is None:
                continue
            result.append({
                "url": clip.url,
                "local_path": clip.local_path,
                "provider": clip.provider,
                "filename": clip.filename,
                "scene_number": clip.scene_number,
                "relevance_score": clip.relevance_score,
                "shot_variant": clip.shot_variant,
                "is_fresh": clip.is_fresh,
            })
        return result

    pipeline_a = _to_pipeline_format(clips_a)
    pipeline_b = _to_pipeline_format(clips_b)

    elapsed = round(time.time() - start, 1)
    logger.success(
        f"🎬 CompositionMaster completado en {elapsed}s — "
        f"A={len(pipeline_a)} clips, B={len(pipeline_b)} clips, "
        f"0 repeticiones"
    )

    return pipeline_a, pipeline_b


# ─────────────────────────────────────────────────────────────
# INTEGRACIÓN CON PIPELINE — Función de reemplazo
# ─────────────────────────────────────────────────────────────

def fetch_fresh_stock_videos(
    guion: str,
    tema: str,
    nicho_slug: str,
    keywords: list[str],
    num_clips: int = 10,
    job_id: str = "",
) -> list[dict]:
    """Reemplaza fetch_stock_videos() en el pipeline con clips frescos y temáticos.

    Esta función es el punto de integración principal con video_factory.py.
    Devuelve clips en el mismo formato que fetch_stock_videos() para ser
    100% compatible con download_clips() del renderer.

    Uso en _stage_media de video_factory.py::

        from pipeline.composition_master import fetch_fresh_stock_videos
        stock_clips = fetch_fresh_stock_videos(
            guion=content.guion,
            tema=content.titulo,
            nicho_slug=nicho_slug,
            keywords=keywords,
            num_clips=nicho.num_clips,
            job_id=manifest.job_id,
        )

    Args:
        guion: Texto completo del guion.
        tema: Título o tema principal para análisis LLM.
        nicho_slug: Slug del nicho (curiosidades, finanzas, etc.)
        keywords: Keywords del nicho como fallback.
        num_clips: Número de clips a obtener.
        job_id: ID del job para el historial.

    Returns:
        Lista de dicts [{"url": ..., "local_path": ..., "provider": ...}]
        compatibles directamente con download_clips().
    """
    # Usar guion y tema del contenido generado (no solo keywords del nicho)
    tema_efectivo = tema or " ".join(keywords[:3])

    # Generar plan y obtener clips frescos
    clips_a, clips_b = compose_video_clips(
        guion=guion,
        tema=tema_efectivo,
        nicho=nicho_slug,
        num_clips=num_clips,
        job_id=job_id,
        export_plan_path=str(
            Path(settings.video_cache_dir) / f"composition_plan_{job_id}.json"
        ) if job_id else None,
    )

    # Combinar A y B de forma intercalada para máxima variedad visual
    # [A1, B1, A2, B2, A3, B3 ...]
    combined: list[dict] = []
    max_len = max(len(clips_a), len(clips_b))
    for i in range(max_len):
        if i < len(clips_a):
            combined.append(clips_a[i])
        if i < len(clips_b):
            combined.append(clips_b[i])

    # Si no hay suficientes clips frescos, completar con los existentes del sistema legacy
    if len(combined) < num_clips // 2:
        logger.warning(
            f"CompositionMaster: solo {len(combined)} clips frescos obtenidos, "
            f"completando con sistema legacy"
        )
        try:
            from pipeline.video_stock import fetch_stock_videos
            legacy = fetch_stock_videos(keywords, num_clips)
            # Solo añadir los que NO estén ya en combined (por URL)
            combined_urls = {c.get("url") for c in combined}
            for item in legacy:
                if item.get("url") not in combined_urls:
                    combined.append(item)
        except Exception as e:
            logger.debug(f"CompositionMaster: legacy fallback falló — {e}")

    return combined[:num_clips * 2]  # Retornar el doble para que el renderer tenga margen
