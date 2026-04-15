"""Provider Scoring nativo derivado de OpenMontage (V16.1)

Califica prospectos y candidatos a clips basándose en las matrices 
evaluativas de `scoring.py` pero iterándolo para optimizar Pexels/Pixabay.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class ClipCandidateScore:
    """Evaluación calificada de un clip candidato de Pexels/Pixabay."""
    clip_id: str
    provider: str               # pexels, pixabay
    resolution_fit: float = 0.0 # 0-1: cumple (HD/SD vs Vertical/Horizontal)
    freshness: float = 0.0      # 0-1: 1.0 si es nuevo, <1 si es riesgoso o visto
    search_relevance: float = 0.0 # 0-1: cuan fiel es a la tag primaria buscada
    reliability: float = 0.0    # 0-1: confiabilidad del provider (tiempos de descarga)

    @property
    def weighted_score(self) -> float:
        return (
            self.search_relevance * 0.40
            + self.freshness * 0.35
            + self.resolution_fit * 0.15
            + self.reliability * 0.10
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["weighted_score"] = self.weighted_score
        return d


def score_clip_candidate(
    clip_dict: dict[str, Any], 
    target_keyword: str,
    require_portrait: bool = True
) -> ClipCandidateScore:
    """Genera la métrica evaluativa (Score) de un clip obtenido antes de seleccionarlo definitivamente.
    
    Args:
        clip_dict: Un diccionario devuelto por `_fetch_pexels_fresh` u homólogos
                   (ej: {"clip_id": ..., "url": ..., "provider": ..., "filename": ...}).
        target_keyword: Búsqueda clave intencional.
        require_portrait: Si el vertical tiene prevalencia.
    """
    provider = str(clip_dict.get("provider", "unknown")).lower()
    
    # 1. Relevance: heurística básica basada en keywords o priorización del origen
    relevance = 0.8 # Promedio base, ya que pasaron query matching de API
    if provider == "pexels":
        relevance = 0.90 # Pexels suele tener metadata visual superior en HD
    elif provider == "pixabay":
        relevance = 0.85
        
    # 2. Freshness: Al venir por la query filtrada son "fresh" pero asignamos margen
    freshness = 1.0 
    
    # 3. Resolution fit (aproximado): Si pasaron nuestros filtros de HD
    resolution_fit = 1.0
    
    # 4. Reliability: qué tan rápido / estable es descargar de allá
    reliability_map = {
        "pexels": 0.95,
        "pixabay": 0.85,  # Pixabay a veces estrangula descargas si no estás validado full
        "unknown": 0.50
    }
    reliability = reliability_map.get(provider, 0.50)
    
    return ClipCandidateScore(
        clip_id=clip_dict.get("clip_id", ""),
        provider=provider,
        resolution_fit=resolution_fit,
        freshness=freshness,
        search_relevance=relevance,
        reliability=reliability
    )

def select_best_clip_candidate(candidates: list[dict[str, Any]], target_keyword: str) -> dict[str, Any] | None:
    """Recibe n-candidatos y extrae el mejor evaluado según Scoring holístico."""
    if not candidates:
        return None
        
    scored = []
    for c in candidates:
        score_obj = score_clip_candidate(c, target_keyword)
        scored.append((score_obj.weighted_score, c))
        
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]
