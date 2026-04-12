"""
ABAssetManager - A/B Testing Visual Integration (SaarD00)
Gestiona 2 clips por escena para A/B testing y variedad visual
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from loguru import logger


@dataclass
class VisualAsset:
    """Representa un asset visual para una escena"""
    id: str
    url: str
    local_path: Optional[Path] = None
    duration: float = 5.0
    quality_score: float = 0.0
    variation_type: str = "original"  # original | alternative | closeup | detail


class ABAssetManager:
    """
    Gestiona 2 clips por escena para A/B testing visual.
    
    Basado en AI-Youtube-Shorts-Generator (SaarD00) - módulo asset_manager.py
    """
    
    def __init__(self, pexels_api_key: Optional[str] = None):
        self.pexels_api_key = pexels_api_key
        self.used_asset_ids: set = set()
        self.cache: dict = {}
        
    def fetch_dual_assets(
        self, 
        query: str, 
        orientation: str = "9:16",
        min_duration: float = 3.0,
        exclude_ids: Optional[List[str]] = None
    ) -> Tuple[VisualAsset, VisualAsset]:
        """
        Retorna 2 clips alternativos para la misma escena.
        
        Args:
            query: Término de búsqueda para Pexels
            orientation: Orientación del video (9:16 para Shorts)
            min_duration: Duración mínima del clip
            exclude_ids: IDs a excluir (ya usados)
            
        Returns:
            Tuple de (clip_a, clip_b) donde:
            - clip_a: Opción original/estándar
            - clip_b: Variación (ángulo diferente, closeup, etc.)
        """
        # Clip A: Original - búsqueda directa
        clip_a = self._search_single(
            query=query,
            orientation=orientation,
            min_duration=min_duration,
            exclude_ids=exclude_ids or [],
            variation_type="original"
        )
        
        # Clip B: Variación - búsqueda con modificadores
        variation_query = self._generate_variation_query(query)
        clip_b = self._search_single(
            query=variation_query,
            orientation=orientation,
            min_duration=min_duration,
            exclude_ids=(exclude_ids or []) + [clip_a.id] if clip_a else [],
            variation_type="alternative"
        )
        
        # Si no se encontró B, duplicar A con metadata diferente
        if not clip_b and clip_a:
            clip_b = VisualAsset(
                id=f"{clip_a.id}_alt",
                url=clip_a.url,
                duration=clip_a.duration,
                quality_score=clip_a.quality_score * 0.95,  # Ligeramente menor
                variation_type="duplicate"
            )
        
        logger.info(f"AB Assets: '{query}' -> A({clip_a.id if clip_a else 'None'}), B({clip_b.id if clip_b else 'None'})")
        
        return clip_a, clip_b
    
    def _generate_variation_query(self, base_query: str) -> str:
        """Genera una query variada para encontrar alternativas visuales"""
        modifiers = [
            "closeup",
            "detail",
            "different angle",
            "slow motion",
            "macro",
            "wide shot",
            "aerial view",
            "time lapse",
        ]
        
        # Seleccionar 1-2 modificadores aleatorios
        num_modifiers = random.randint(1, 2)
        selected = random.sample(modifiers, num_modifiers)
        
        return f"{base_query} {' '.join(selected)}"
    
    def _search_single(
        self,
        query: str,
        orientation: str,
        min_duration: float,
        exclude_ids: List[str],
        variation_type: str
    ) -> Optional[VisualAsset]:
        """
        Busca un solo asset en Pexels (stub - implementar con API real)
        """
        # TODO: Implementar integración real con Pexels API
        # Por ahora, generar un asset simulado
        
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        asset_id = f"pexels_{query_hash}_{variation_type}"
        
        if asset_id in self.used_asset_ids or asset_id in exclude_ids:
            return None
        
        self.used_asset_ids.add(asset_id)
        
        return VisualAsset(
            id=asset_id,
            url=f"https://api.pexels.com/videos/{asset_id}",
            duration=random.uniform(min_duration, min_duration + 2.0),
            quality_score=random.uniform(0.7, 0.95),
            variation_type=variation_type
        )
    
    def select_best_visual(
        self,
        visual_a: VisualAsset,
        visual_b: VisualAsset,
        criteria: str = "quality"
    ) -> VisualAsset:
        """
        Selecciona el mejor visual basado en criterios.
        
        Args:
            visual_a: Primera opción
            visual_b: Segunda opción
            criteria: "quality", "duration", "variety"
            
        Returns:
            El visual seleccionado
        """
        if criteria == "quality":
            return visual_a if visual_a.quality_score >= visual_b.quality_score else visual_b
        elif criteria == "duration":
            return visual_a if visual_a.duration >= visual_b.duration else visual_b
        elif criteria == "variety":
            # Preferir el que NO es duplicate
            if visual_b.variation_type == "duplicate":
                return visual_a
            if visual_a.variation_type == "duplicate":
                return visual_b
            return random.choice([visual_a, visual_b])
        else:
            return random.choice([visual_a, visual_b])
    
    def auto_select_with_fallback(
        self,
        visual_a: VisualAsset,
        visual_b: Optional[VisualAsset],
        min_quality_threshold: float = 0.6
    ) -> VisualAsset:
        """
        Selección automática con fallback si la calidad es baja.
        
        Args:
            visual_a: Opción A
            visual_b: Opción B (puede ser None)
            min_quality_threshold: Calidad mínima aceptable
            
        Returns:
            Visual seleccionado
        """
        if not visual_b:
            logger.warning(f"No hay alternativa B para {visual_a.id}, usando A")
            return visual_a
        
        # Si ambos están por debajo del threshold, usar A (original)
        if visual_a.quality_score < min_quality_threshold and visual_b.quality_score < min_quality_threshold:
            logger.warning(f"Calidad baja en ambos options para {visual_a.id}")
            return visual_a
        
        return self.select_best_visual(visual_a, visual_b, criteria="quality")
    
    def get_scene_variants(
        self,
        scene_keywords: List[str],
        num_variants: int = 2
    ) -> List[List[VisualAsset]]:
        """
        Genera variantes completas para todas las escenas.
        
        Args:
            scene_keywords: Lista de keywords por escena
            num_variants: Número de variantes a generar (default: 2 para A/B)
            
        Returns:
            Lista de variantes, donde cada variante es una lista de assets
        """
        variants = [[] for _ in range(num_variants)]
        
        for keyword in scene_keywords:
            a, b = self.fetch_dual_assets(keyword)
            if a and b:
                variants[0].append(a)
                variants[1].append(b)
            elif a:
                variants[0].append(a)
                variants[1].append(a)  # Duplicar si no hay B
        
        return variants


# Singleton para uso en pipeline
_ab_asset_manager: Optional[ABAssetManager] = None


def get_ab_asset_manager(api_key: Optional[str] = None) -> ABAssetManager:
    """Obtiene la instancia singleton del ABAssetManager"""
    global _ab_asset_manager
    if _ab_asset_manager is None:
        _ab_asset_manager = ABAssetManager(api_key)
    return _ab_asset_manager
