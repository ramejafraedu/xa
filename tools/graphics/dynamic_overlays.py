"""Dynamic Overlays Automáticos - El estilo depende 100% del guion
Genera gráficos, iconos, texto cinético y efectos encima del video automáticamente.
"""

import re
from typing import Dict, List, Any
from loguru import logger

def analyze_guion(guion: str, nicho_slug: str = "general") -> Dict[str, Any]:
    """Analiza el guion y decide qué overlays poner automáticamente."""
    guion_lower = guion.lower()
    
    emotion = "neutral"
    overlays = []
    
    # === DETECCIÓN DE ESTILO SEGÚN GUION ===
    if nicho_slug == "finanzas" or any(w in guion_lower for w in ["dinero", "ganar", "invertir", "ahorrar", "millones", "precio", "$", "€"]):
        emotion = "money"
        overlays.append({"type": "money_rain", "start_time": 1.2, "duration": 2.8, "style": "gold_pop"})
        overlays.append({"type": "lower_third", "text": "💰 El truco que nadie te cuenta", "start_time": 3.0, "duration": 3.2, "style": "gold"})
    
    elif nicho_slug == "curiosidades" or any(w in guion_lower for w in ["¿", "cómo", "por qué", "nunca", "secreto", "¡"]):
        emotion = "curiosity"
        overlays.append({"type": "question_burst", "start_time": 0.6, "duration": 2.2, "style": "pop"})
        overlays.append({"type": "lightbulb", "start_time": 4.0, "duration": 2.0, "style": "glow"})
    
    elif nicho_slug == "historia" or any(w in guion_lower for w in ["año", "siglo", "antiguo", "guerra", "rey", "imperio"]):
        emotion = "history"
        overlays.append({"type": "old_timeline", "start_time": 2.0, "duration": 4.5, "style": "sepia"})
    
    elif nicho_slug == "ia_herramientas" or any(w in guion_lower for w in ["ia", "inteligencia", "chatgpt", "herramienta", "app"]):
        emotion = "tech"
        overlays.append({"type": "tech_lines", "start_time": 1.0, "duration": 3.0, "style": "neon"})
    
    # Hook fuerte siempre en los primeros 3 segundos
    hook_text = guion[:55].strip() + "..." if len(guion) > 55 else guion
    overlays.append({
        "type": "hook_kinetic",
        "text": hook_text,
        "start_time": 0.4,
        "duration": 2.6,
        "style": "bold_impact",
        "position": "center"
    })
    
    # Pattern interrupt cada ~5 segundos
    overlays.append({
        "type": "flash_pop",
        "start_time": 5.2,
        "duration": 0.35,
        "style": "white_flash"
    })
    
    return {
        "emotion": emotion,
        "overlays": overlays,
        "total": len(overlays)
    }


def enrich_schema_with_overlays(schema: dict, guion: str, nicho_slug: str) -> dict:
    """Enriquece el schema del EditingEngine con overlays automáticos según el guion."""
    analysis = analyze_guion(guion, nicho_slug)
    
    for ov in analysis["overlays"]:
        layer = {
            "type": ov["type"],
            "text": ov.get("text", ""),
            "start_time": round(ov["start_time"], 2),
            "duration": round(ov["duration"], 2),
            "style": ov.get("style", "default"),
            "position": ov.get("position", "center"),
            "z_index": 30,
            "effects": [ov.get("style", "")]
        }
        schema["timeline"].append(layer)
    
    schema["metadata"]["overlays_automaticos"] = analysis["total"]
    schema["metadata"]["emocion_detectada"] = analysis["emotion"]
    
    logger.success(f"✅ Overlays automáticos agregados: {analysis['total']} | Estilo: {analysis['emotion']}")
    return schema
