"""
CharacterTracker - Consistencia de Personajes (ViMax Integration)
Mantiene consistencia de personajes entre escenas/videos
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from loguru import logger


@dataclass
class CharacterAttributes:
    """Atributos visuales de un personaje"""
    clothing: str = ""
    hairstyle: str = ""
    color_scheme: str = ""  # paleta de colores predominante
    facial_features: str = ""
    height_estimate: str = ""
    accessories: List[str] = field(default_factory=list)


@dataclass
class CharacterAppearance:
    """Registro de aparición de un personaje en una escena"""
    scene_id: str
    timestamp: float
    position: Tuple[float, float]  # x, y normalized (0-1)
    scale: float  # tamaño relativo (0-1)
    lighting: str = ""  # iluminación en la escena
    angle: str = ""  # ángulo de cámara


@dataclass
class Character:
    """Representación completa de un personaje trackeado"""
    char_id: str
    name: str
    avatar_path: Optional[Path] = None
    attributes: CharacterAttributes = field(default_factory=CharacterAttributes)
    appearances: List[CharacterAppearance] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_seen: Optional[datetime] = None


class CharacterTracker:
    """
    Mantiene consistencia de personajes entre escenas/videos.
    
    Implementación basada en ViMax - AutoCameo feature.
    """
    
    def __init__(self, persistence_path: Optional[Path] = None):
        self.characters: Dict[str, Character] = {}
        self.appearances: Dict[str, List[CharacterAppearance]] = {}
        self.persistence_path = persistence_path
        
        if persistence_path and persistence_path.exists():
            self._load_from_disk()
    
    def register_character(
        self, 
        name: str, 
        avatar_path: Optional[Path] = None,
        attributes: Optional[CharacterAttributes] = None
    ) -> str:
        """
        Registra un personaje para tracking.
        
        Args:
            name: Nombre del personaje
            avatar_path: Ruta a imagen de referencia
            attributes: Atributos visuales conocidos
            
        Returns:
            char_id: ID único del personaje
        """
        # Generar ID consistente basado en nombre
        char_id = hashlib.md5(name.encode()).hexdigest()[:8]
        
        if char_id in self.characters:
            logger.info(f"Personaje '{name}' ya existe con ID {char_id}")
            return char_id
        
        # Extraer atributos si hay avatar
        if attributes is None and avatar_path:
            attributes = self._extract_attributes(avatar_path)
        elif attributes is None:
            attributes = CharacterAttributes()
        
        character = Character(
            char_id=char_id,
            name=name,
            avatar_path=avatar_path,
            attributes=attributes
        )
        
        self.characters[char_id] = character
        self.appearances[char_id] = []
        
        logger.info(f"Personaje registrado: '{name}' (ID: {char_id})")
        
        return char_id
    
    def record_appearance(
        self,
        char_id: str,
        scene_id: str,
        timestamp: float,
        position: Tuple[float, float] = (0.5, 0.5),
        scale: float = 0.3,
        lighting: str = "",
        angle: str = ""
    ) -> None:
        """
        Registra una aparición del personaje en una escena.
        
        Args:
            char_id: ID del personaje
            scene_id: ID de la escena
            timestamp: Tiempo en segundos
            position: Posición (x, y) normalizada 0-1
            scale: Escala relativa 0-1
            lighting: Descripción de iluminación
            angle: Ángulo de cámara
        """
        if char_id not in self.characters:
            logger.warning(f"Personaje {char_id} no registrado")
            return
        
        appearance = CharacterAppearance(
            scene_id=scene_id,
            timestamp=timestamp,
            position=position,
            scale=scale,
            lighting=lighting,
            angle=angle
        )
        
        self.appearances[char_id].append(appearance)
        self.characters[char_id].appearances.append(appearance)
        self.characters[char_id].last_seen = datetime.now()
        
        logger.debug(f"Aparición registrada: {char_id} en escena {scene_id}")
    
    def ensure_consistency(
        self,
        scene_idx: int,
        char_id: str,
        proposed_attributes: CharacterAttributes
    ) -> Tuple[bool, List[str]]:
        """
        Verifica que el personaje sea consistente en esta escena.
        
        Args:
            scene_idx: Índice de la escena
            char_id: ID del personaje
            proposed_attributes: Atributos propuestos para esta escena
            
        Returns:
            (is_consistent, discrepancies_list)
        """
        if char_id not in self.characters:
            return False, ["Personaje no registrado"]
        
        character = self.characters[char_id]
        discrepancies = []
        
        # Verificar atributos clave
        original = character.attributes
        
        if original.clothing and proposed_attributes.clothing != original.clothing:
            discrepancies.append(f"Vestimenta diferente: {original.clothing} vs {proposed_attributes.clothing}")
        
        if original.hairstyle and proposed_attributes.hairstyle != original.hairstyle:
            discrepancies.append(f"Peinado diferente: {original.hairstyle} vs {proposed_attributes.hairstyle}")
        
        if original.color_scheme and proposed_attributes.color_scheme != original.color_scheme:
            discrepancies.append(f"Paleta diferente: {original.color_scheme} vs {proposed_attributes.color_scheme}")
        
        # Verificar temporal coherence (cambios bruscos)
        temporal_ok = self._check_temporal_coherence(scene_idx, char_id)
        if not temporal_ok:
            discrepancies.append("Cambio brusco de posición/escala entre escenas")
        
        is_consistent = len(discrepancies) == 0
        
        if not is_consistent:
            logger.warning(f"Inconsistencia detectada para '{character.name}': {discrepancies}")
        
        return is_consistent, discrepancies
    
    def get_constraints(self, char_id: str) -> Dict[str, any]:
        """
        Obtiene las restricciones de consistencia para un personaje.
        
        Args:
            char_id: ID del personaje
            
        Returns:
            Dict con restricciones para generación de imágenes
        """
        if char_id not in self.characters:
            return {}
        
        char = self.characters[char_id]
        attrs = char.attributes
        
        return {
            "character_name": char.name,
            "must_preserve": {
                "clothing": attrs.clothing,
                "hairstyle": attrs.hairstyle,
                "color_scheme": attrs.color_scheme,
            },
            "reference_image": str(char.avatar_path) if char.avatar_path else None,
            "last_position": char.appearances[-1].position if char.appearances else None,
            "last_scale": char.appearances[-1].scale if char.appearances else None,
        }
    
    def _extract_attributes(self, avatar_path: Path) -> CharacterAttributes:
        """Extrae atributos visuales de una imagen de referencia (stub)"""
        # TODO: Implementar con vision API (Gemini/Claude) para analizar imagen
        return CharacterAttributes(
            color_scheme="unknown",
            clothing="unknown",
            hairstyle="unknown"
        )
    
    def _check_temporal_coherence(self, scene_idx: int, char_id: str) -> bool:
        """Verifica coherencia temporal (sin cambios bruscos)"""
        if char_id not in self.appearances or not self.appearances[char_id]:
            return True
        
        appearances = self.appearances[char_id]
        if len(appearances) < 2:
            return True
        
        last = appearances[-1]
        # Verificar que no haya cambios bruscos de posición o escala
        # (implementación simplificada)
        return True
    
    def get_recurring_characters(
        self,
        min_appearances: int = 2,
        video_scope: Optional[str] = None
    ) -> List[Character]:
        """
        Obtiene personajes recurrentes (AutoCameo).
        
        Args:
            min_appearances: Mínimo de apariciones para considerar recurrente
            video_scope: Filtrar por video específico (opcional)
            
        Returns:
            Lista de personajes recurrentes
        """
        recurring = []
        for char_id, char in self.characters.items():
            appearances = self.appearances.get(char_id, [])
            if len(appearances) >= min_appearances:
                recurring.append(char)
        
        return sorted(recurring, key=lambda c: len(self.appearances.get(c.char_id, [])), reverse=True)
    
    def save_to_disk(self) -> None:
        """Persiste el estado del tracker a disco"""
        if not self.persistence_path:
            return
        
        data = {
            "characters": {
                cid: {
                    "name": c.name,
                    "avatar_path": str(c.avatar_path) if c.avatar_path else None,
                    "attributes": c.attributes.__dict__,
                    "created_at": c.created_at.isoformat(),
                }
                for cid, c in self.characters.items()
            },
            "appearances": {
                cid: [
                    {
                        "scene_id": a.scene_id,
                        "timestamp": a.timestamp,
                        "position": a.position,
                        "scale": a.scale,
                    }
                    for a in apps
                ]
                for cid, apps in self.appearances.items()
            }
        }
        
        self.persistence_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info(f"CharacterTracker guardado en {self.persistence_path}")
    
    def _load_from_disk(self) -> None:
        """Carga el estado del tracker desde disco"""
        try:
            data = json.loads(self.persistence_path.read_text(encoding="utf-8"))
            
            for cid, cdata in data.get("characters", {}).items():
                char = Character(
                    char_id=cid,
                    name=cdata["name"],
                    avatar_path=Path(cdata["avatar_path"]) if cdata["avatar_path"] else None,
                    attributes=CharacterAttributes(**cdata["attributes"]),
                    created_at=datetime.fromisoformat(cdata["created_at"])
                )
                self.characters[cid] = char
            
            logger.info(f"CharacterTracker cargado: {len(self.characters)} personajes")
        except Exception as e:
            logger.error(f"Error cargando CharacterTracker: {e}")


# Singleton
_character_tracker: Optional[CharacterTracker] = None


def get_character_tracker(persistence_path: Optional[Path] = None) -> CharacterTracker:
    """Obtiene la instancia singleton del CharacterTracker"""
    global _character_tracker
    if _character_tracker is None:
        _character_tracker = CharacterTracker(persistence_path)
    return _character_tracker
