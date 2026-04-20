"""Smart Scorer - Reemplaza temporalmente OpenMontage hasta que conectemos el real"""

from typing import List, Dict, Any
from loguru import logger

def score_and_rank_clips(clips: List[Dict], guion: str, nicho_slug: str, target_duration: float = 30.0) -> List[Dict]:
    """Da score a los clips según relevancia con el guion."""
    scored = []
    guion_lower = guion.lower()
    
    for clip in clips:
        score = 0.5  # base
        
        # Relevancia por palabras clave
        if any(kw in clip.get("tags", []) for kw in guion_lower.split()[:5]):
            score += 0.3
        
        # Preferir clips más nuevos y de buena resolución
        if clip.get("duration", 0) > 3 and clip.get("duration", 0) < 8:
            score += 0.15
        
        # Penalizar si es muy slideshow
        if clip.get("is_static", False):
            score -= 0.25
        
        clip["retention_score"] = round(score, 2)
        scored.append(clip)
    
    # Ordenar por score
    scored.sort(key=lambda x: x.get("retention_score", 0), reverse=True)
    return scored[:len(clips)]  # mantener cantidad original


def evaluate_scene_quality(clip: Dict) -> float:
    """Evalúa calidad de una escena (0.0 a 1.0)"""
    score = 0.6
    if clip.get("resolution", 0) >= 1080:
        score += 0.2
    if not clip.get("is_static", False):
        score += 0.15
    return min(1.0, score)
