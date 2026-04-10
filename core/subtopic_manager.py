"""Gestor de historial de subtemas para evitar repeticiones por nicho.

V16.1: Anti-Repetition System - Garantiza variedad real de subtemas dentro de cada nicho.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from difflib import SequenceMatcher

from loguru import logger

from config import settings


class SubtopicManager:
    """Gestiona el historial de subtemas usados por nicho para evitar repeticiones."""
    
    def __init__(self, history_dir: Optional[Path] = None):
        self.history_dir = history_dir or Path(settings.temp_dir) / "subtopic_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.history_limit = settings.subtopic_history_limit
        self.similarity_threshold = settings.subtopic_similarity_threshold
    
    def _get_history_file(self, nicho_slug: str) -> Path:
        """Obtiene el archivo de historial para un nicho específico."""
        return self.history_dir / f"{nicho_slug}_history.json"
    
    def _normalize_subtopic(self, subtopic: str) -> str:
        """Normaliza un subtema para comparación (minúsculas, sin puntuación)."""
        normalized = subtopic.lower().strip()
        # Remover puntuación común
        normalized = re.sub(r'[^\w\s]', '', normalized)
        # Remover palabras comunes de relleno
        stop_words = {'el', 'la', 'los', 'las', 'un', 'una', 'de', 'del', 'al', 'y', 'o', 'con', 'por', 'para', 'en', 'sobre'}
        words = [w for w in normalized.split() if w not in stop_words]
        return ' '.join(words)
    
    def _calculate_similarity(self, subtopic1: str, subtopic2: str) -> float:
        """Calcula la similitud entre dos subtemas (0.0 a 1.0)."""
        norm1 = self._normalize_subtopic(subtopic1)
        norm2 = self._normalize_subtopic(subtopic2)
        
        if not norm1 or not norm2:
            return 0.0
        
        # Usar SequenceMatcher para similitud de secuencia
        similarity = SequenceMatcher(None, norm1, norm2).ratio()
        
        # Bonus si comparten palabras significativas
        words1 = set(norm1.split())
        words2 = set(norm2.split())
        if words1 and words2:
            common_words = words1.intersection(words2)
            if len(common_words) >= min(len(words1), len(words2)) * 0.5:
                similarity = max(similarity, 0.8)
        
        return similarity
    
    def is_subtopic_used(self, nicho_slug: str, subtopic: str) -> tuple[bool, float]:
        """Verifica si un subtema ya fue usado recientemente.
        
        Returns:
            (is_used, similarity_score): True si ya existe similar, y el score de similitud
        """
        history_file = self._get_history_file(nicho_slug)
        if not history_file.exists():
            return False, 0.0
        
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            return False, 0.0
        
        for entry in history:
            existing_subtopic = entry.get('subtopic', '')
            similarity = self._calculate_similarity(subtopic, existing_subtopic)
            if similarity >= self.similarity_threshold:
                logger.warning(
                    f"Subtema similar detectado: '{subtopic}' vs '{existing_subtopic}' "
                    f"(similitud: {similarity:.2f})"
                )
                return True, similarity
        
        return False, 0.0
    
    def record_subtopic(self, nicho_slug: str, subtopic: str, video_id: Optional[str] = None):
        """Registra un subtema como usado."""
        history_file = self._get_history_file(nicho_slug)
        
        # Cargar historial existente
        history = []
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
            except (json.JSONDecodeError, IOError):
                history = []
        
        # Agregar nueva entrada
        entry = {
            'subtopic': subtopic,
            'timestamp': datetime.now().isoformat(),
            'video_id': video_id,
            'normalized': self._normalize_subtopic(subtopic)
        }
        history.insert(0, entry)  # Más reciente primero
        
        # Mantener solo el límite configurado
        history = history[:self.history_limit]
        
        # Guardar
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        
        logger.info(f"✅ Subtema registrado para {nicho_slug}: {subtopic[:60]}...")
    
    def get_used_subtopics(self, nicho_slug: str) -> list[str]:
        """Obtiene lista de subtemas usados recientemente."""
        history_file = self._get_history_file(nicho_slug)
        if not history_file.exists():
            return []
        
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            return [entry['subtopic'] for entry in history]
        except (json.JSONDecodeError, IOError):
            return []
    
    def clear_history(self, nicho_slug: Optional[str] = None):
        """Limpia el historial (de un nicho específico o todo)."""
        if nicho_slug:
            history_file = self._get_history_file(nicho_slug)
            if history_file.exists():
                history_file.unlink()
                logger.info(f"🗑️ Historial limpiado para {nicho_slug}")
        else:
            # Limpiar todo
            for f in self.history_dir.glob("*_history.json"):
                f.unlink()
            logger.info("🗑️ Todo el historial de subtemas limpiado")
    
    def get_exclusion_prompt(self, nicho_slug: str) -> str:
        """Genera texto para prompt del LLM excluyendo subtemas usados."""
        used = self.get_used_subtopics(nicho_slug)
        if not used:
            return ""
        
        # Tomar los últimos 10 para no saturar el prompt
        recent = used[:10]
        exclusion_text = "\n⚠️  SUBTEMAS RECIENTEMENTE USADOS (NO repetir):\n"
        for i, subtopic in enumerate(recent, 1):
            exclusion_text += f"  {i}. {subtopic[:80]}\n"
        exclusion_text += "\n👉 Elige un subtema COMPLETAMENTE DIFERENTE y ORIGINAL.\n"
        
        return exclusion_text


# Instancia global para uso en todo el pipeline
_subtopic_manager: Optional[SubtopicManager] = None


def get_subtopic_manager() -> SubtopicManager:
    """Obtiene la instancia global del gestor de subtemas."""
    global _subtopic_manager
    if _subtopic_manager is None:
        _subtopic_manager = SubtopicManager()
    return _subtopic_manager


def reset_subtopic_manager():
    """Reinicia la instancia global (útil para tests)."""
    global _subtopic_manager
    _subtopic_manager = None
