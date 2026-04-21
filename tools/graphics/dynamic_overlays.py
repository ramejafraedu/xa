"""Dynamic Overlays Automáticos - El estilo depende 100% del guion
Genera gráficos, iconos, texto cinético y efectos encima del video automáticamente.
"""

from typing import Any, Dict, List

from loguru import logger


def _clamp_overlay(start: float, duration: float, total: float) -> tuple[float, float]:
    """Keep overlay inside [0, total] with a small safety margin."""
    t = max(0.0, float(total or 0.0))
    if t < 0.5:
        return 0.0, 0.2
    st = max(0.0, min(float(start), t - 0.15))
    dur = max(0.15, float(duration))
    if st + dur > t - 0.05:
        dur = max(0.15, t - st - 0.05)
    return round(st, 3), round(dur, 3)


def analyze_guion(
    guion: str,
    nicho_slug: str = "general",
    audio_duration: float = 60.0,
) -> Dict[str, Any]:
    """Analiza el guion y decide qué overlays poner; tiempos escalados a la duración real."""
    guion_lower = (guion or "").lower()
    D = max(4.0, float(audio_duration or 4.0))

    emotion = "neutral"
    overlays: List[Dict[str, Any]] = []

    def add(
        typ: str,
        start_frac: float,
        dur_frac: float,
        *,
        text: str = "",
        style: str = "default",
        position: str = "center",
    ) -> None:
        st, du = _clamp_overlay(start_frac * D, dur_frac * D, D)
        if du < 0.12:
            return
        item: Dict[str, Any] = {
            "type": typ,
            "start_time": st,
            "duration": du,
            "style": style,
            "position": position,
        }
        if text:
            item["text"] = text
        overlays.append(item)

    # === DETECCIÓN DE ESTILO SEGÚN GUION ===
    if nicho_slug == "finanzas" or any(
        w in guion_lower for w in ["dinero", "ganar", "invertir", "ahorrar", "millones", "precio", "$", "€"]
    ):
        emotion = "money"
        add("money_rain", 0.06, 0.12, style="gold_pop")
        add("lower_third", 0.14, 0.14, text="💰 El truco que nadie te cuenta", style="gold", position="bottom_third")

    elif nicho_slug == "curiosidades" or any(
        w in guion_lower for w in ["¿", "cómo", "por qué", "nunca", "secreto", "¡"]
    ):
        emotion = "curiosity"
        add("question_burst", 0.04, 0.08, style="pop")
        add("lightbulb", 0.42, 0.08, style="glow")

    elif nicho_slug == "historia" or any(
        w in guion_lower for w in ["año", "siglo", "antiguo", "guerra", "rey", "imperio"]
    ):
        emotion = "history"
        add("old_timeline", 0.10, 0.18, style="sepia")

    elif nicho_slug == "ia_herramientas" or any(
        w in guion_lower for w in ["ia", "inteligencia", "chatgpt", "herramienta", "app"]
    ):
        emotion = "tech"
        add("tech_lines", 0.06, 0.12, style="neon")

    # Hook fuerte en los primeros ~15% del video
    hook_text = (guion or "").strip()
    hook_text = (hook_text[:55] + "…") if len(hook_text) > 55 else hook_text
    if hook_text:
        add("hook_kinetic", 0.03, min(0.16, 3.8 / D), text=hook_text, style="bold_impact", position="center")

    # Pattern interrupt escalado (visible en short y long form)
    add("flash_pop", 0.34, max(0.06 / D, 0.06), style="white_flash")
    if D >= 18.0:
        add("flash_pop", 0.68, max(0.05 / D, 0.05), style="white_flash")

    return {
        "emotion": emotion,
        "overlays": overlays,
        "total": len(overlays),
    }


def enrich_schema_with_overlays(
    schema: dict,
    guion: str,
    nicho_slug: str,
    audio_duration: float | None = None,
) -> dict:
    """Enriquece el schema del EditingEngine con overlays automáticos según el guion."""
    meta = schema.get("metadata") or {}
    if audio_duration is None:
        audio_duration = float(meta.get("total_duration") or meta.get("duration") or 60.0)
    audio_duration = max(4.0, float(audio_duration))

    analysis = analyze_guion(guion, nicho_slug, audio_duration)

    for ov in analysis["overlays"]:
        layer = {
            "type": ov["type"],
            "text": ov.get("text", ""),
            "start_time": float(ov["start_time"]),
            "duration": float(ov["duration"]),
            "style": ov.get("style", "default"),
            "position": ov.get("position", "center"),
            "z_index": 30,
            "effects": [ov.get("style", "")],
        }
        schema.setdefault("timeline", []).append(layer)

    if "metadata" not in schema or not isinstance(schema["metadata"], dict):
        schema["metadata"] = {}
    schema["metadata"]["overlays_automaticos"] = analysis["total"]
    schema["metadata"]["emocion_detectada"] = analysis["emotion"]

    logger.success(
        f"✅ Overlays automáticos agregados: {analysis['total']} | Estilo: {analysis['emotion']} | D={audio_duration:.1f}s"
    )
    return schema
