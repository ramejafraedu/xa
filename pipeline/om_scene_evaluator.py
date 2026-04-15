"""Scene Evaluator nativo derivado de OpenMontage (V16.1)

Combina y adapta la lógica analítica de `slideshow_risk.py` y `variation_checker.py` 
para ajustarse al modelo local de `SceneClipSpec` generado en `composition_master.py`.
"""

from __future__ import annotations
from collections import Counter
from typing import Any

# Frases genéricas extraídas de OpenMontage que indican una selección vaga de escenas
GENERIC_PHRASES = {
    "a person", "a beautiful", "modern", "futuristic", "cutting-edge",
    "in today's world", "sleek design", "innovative", "state-of-the-art",
    "next-generation", "revolutionary", "a professional", "dynamic",
    "vibrant", "stunning", "breathtaking", "amazing", "incredible",
    "powerful", "seamless", "elegant solution", "someone", "people",
}

def evaluate_composition_plan(scene_specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Evalúa un conjunto de especificaciones de escenas para detectar repeticiones
    y el síndrome de 'slideshow' (presentación de diapositivas estáticas).

    Args:
        scene_specs: Lista de diccionarios creados a partir de `SceneClipSpec`.

    Returns:
        Dict con:
        - score: float (0.0 a 5.0, donde >3.0 requiere revisión)
        - is_slideshow_risk: bool
        - verdict: "strong" | "acceptable" | "revise" | "fail"
        - violations: list[str] de violaciones detectadas
        - suggestions: list[str] de sugerencias
    """
    if not scene_specs:
        return {
            "score": 5.0,
            "is_slideshow_risk": True,
            "verdict": "fail",
            "violations": ["El plan no tiene escenas."],
            "suggestions": []
        }

    violations: list[str] = []
    suggestions: list[str] = []
    
    # Extraer elementos clave de las escenas
    shot_types = [str(s.get("shot_type", "medium")).lower() for s in scene_specs]
    motions = [str(s.get("motion", "slow")).lower() for s in scene_specs]
    emotions = [str(s.get("emotion", "neutral")).lower() for s in scene_specs]
    descriptions = [str(s.get("clip_description", "")).lower() for s in scene_specs]

    # --- Check 1: Diversidad de Shot Type (Tamaño de Plano) ---
    size_counts = Counter(shot_types)
    if len(scene_specs) >= 4:
        most_common_size, most_common_count = size_counts.most_common(1)[0]
        if most_common_count / len(scene_specs) > 0.5:
            violations.append(
                f"Plano '{most_common_size}' usado muy frecuentemente "
                f"({most_common_count}/{len(scene_specs)}). Alterna más planos."
            )
            suggestions.append("Mezcla planos amplios (wide) con primer plano (close-up) para ritmo visual.")

    # --- Check 2: Planos repetidos consecutivamente ---
    consecutive_same_size = 0
    for i in range(1, len(shot_types)):
        if shot_types[i] == shot_types[i-1]:
            consecutive_same_size += 1
    if consecutive_same_size >= len(scene_specs) * 0.4:
        violations.append(
            f"{consecutive_same_size} transiciones entre planos del mismo encuadre. "
            f"Evita repetir planos consecutivos."
        )

    # --- Check 3: Sobrecarga Estática (Motion) ---
    static_count = sum(1 for m in motions if m in ("static", "unspecified"))
    if len(scene_specs) >= 4 and static_count / len(scene_specs) > 0.6:
        violations.append(
            f"{static_count}/{len(scene_specs)} escenas son estáticas. Demasiada inactividad visual."
        )
        suggestions.append("Agrega movimientos intencionales (pan, dynamic, handheld) a mínimo el 40% de escenas.")

    # --- Check 4: Diversidad Emocional / Tono ---
    emotion_counts = Counter(emotions)
    if len(scene_specs) >= 5 and "neutral" in emotion_counts:
        neu_ratio = emotion_counts["neutral"] / len(scene_specs)
        if neu_ratio > 0.7:
            violations.append(
                f"El video se siente emocionalmente plano ({neu_ratio:.0%} tonos neutrales). "
            )

    # --- Check 5: Descripciones Genéricas (Lazy Prompts) ---
    generic_count = 0
    for desc in descriptions:
        if any(phrase in desc for phrase in GENERIC_PHRASES):
            generic_count += 1
    if generic_count >= len(scene_specs) * 0.3:
        violations.append(
            f"{generic_count}/{len(scene_specs)} escenas utilizan frases demasiado genéricas (ej: 'a person')."
        )
        suggestions.append("Reemplaza frases ambiguas con detalles visuales ricos y sujetos definidos.")

    # --- Score Calculation ---
    # Cada violación añade entre 0.8 y 1.2 puntos de "riesgo".
    score = min(5.0, len(violations) * 0.9)
    
    if score < 2.0:
        verdict = "strong"
    elif score < 3.0:
        verdict = "acceptable"
    elif score < 4.5:
        verdict = "revise"
    else:
        verdict = "fail"

    is_slideshow_risk = (score >= 3.0) or (static_count / len(scene_specs) > 0.7)

    return {
        "score": round(score, 2),
        "is_slideshow_risk": is_slideshow_risk,
        "verdict": verdict,
        "violations": violations,
        "suggestions": suggestions,
    }
