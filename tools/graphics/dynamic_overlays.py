"""Dynamic Overlays Automáticos - Estilo depende del guion
Genera overlays inteligentes (iconos, texto cinético, gráficos, partículas)
según el contenido del guion y el nicho.
"""

import re
from typing import Dict, List, Any
from loguru import logger

def analyze_guion(guion: str, nicho_slug: str) -> Dict[str, Any]:
    """Analiza el guion y decide qué overlays poner."""
    guion_lower = guion.lower()
    
    # Detectar emoción y keywords
    emotion = "neutral"
    if any(w in guion_lower for w in ["sorprendente", "increíble", "nunca", "secreto", "¡"]):
        emotion = "surprise"
    elif any(w in guion_lower for w in ["dinero", "ganar", "invertir", "ahorrar", "millones", "$"]):
        emotion = "money"
    elif any(w in guion_lower for w in ["historia", "año", "siglo", "antiguo", "guerra"]):
        emotion = "history"
    elif any(w in guion_lower for w in ["cómo", "por qué", "qué es", "descubre"]):
        emotion = "curiosity"
    
    # Overlays base según nicho + emoción
    overlays = []
    
    if nicho_slug == "finanzas" or emotion == "money":
        overlays.append({
            "type": "animated_number",
            "text": "💰",
            "start_time": 1.5,
            "duration": 2.5,
            "style": "pop_explode",
            "position": "top_right"
        })
        overlays.append({
            "type": "lower_third",
            "text": "¡El truco que usan los millonarios!",
            "start_time": 3.0,
            "duration": 3.5,
            "style": "gold_gradient"
        })
    
    elif nicho_slug == "curiosidades" or emotion == "curiosity":
        overlays.append({
            "type": "icon_burst",
            "icon": "❓",
            "start_time": 0.8,
            "duration": 2.0,
            "style": "question_pop"
        })
        overlays.append({
            "type": "lightbulb",
            "text": "💡",
            "start_time": 4.5,
            "duration": 2.0,
            "style": "glow"
        })
    
    elif nicho_slug == "historia" or emotion == "history":
        overlays.append({
            "type": "timeline_bar",
            "start_time": 2.0,
            "duration": 4.0,
            "style": "old_paper"
        })
    
    # Siempre agregar un hook visual fuerte en los primeros 3 segundos
    overlays.append({
        "type": "hook_text",
        "text": guion[:60] + "..." if len(guion) > 60 else guion,
        "start_time": 0.3,
        "duration": 2.8,
        "style": "kinetic_bold",
        "position": "center"
    })
    
    # Pattern interrupt cada ~5 segundos
    overlays.append({
        "type": "flash_effect",
        "start_time": 5.5,
        "duration": 0.4,
        "style": "white_flash"
    })
    
    return {
        "emotion": emotion,
        "overlays": overlays,
        "total_overlays": len(overlays)
    }


def enrich_schema_with_overlays(base_schema: dict, guion: str, nicho_slug: str) -> dict:
    """Toma el schema base del EditingEngine y le agrega overlays automáticos."""
    analysis = analyze_guion(guion, nicho_slug)
    
    for overlay in analysis["overlays"]:
        layer = {
            "type": overlay["type"],
            "text": overlay.get("text", ""),
            "start_time": overlay["start_time"],
            "duration": overlay["duration"],
            "style": overlay.get("style", "default"),
            "position": overlay.get("position", "center"),
            "z_index": 25,  # siempre encima del video
            "effects": [overlay.get("style", "")]
        }
        base_schema["timeline"].append(layer)
    
    base_schema["metadata"]["overlays_added"] = analysis["total_overlays"]
    base_schema["metadata"]["emotion_detected"] = analysis["emotion"]
    
    logger.info(f"✅ Overlays automáticos agregados: {analysis['total_overlays']} | Emoción: {analysis['emotion']}")
    return base_schema
