"""
ViMax Consistency Module

Proporciona tracking de personajes y coherencia temporal para videos multi-escena.
"""

from .character_tracker import (
    Character,
    CharacterAppearance,
    CharacterAttributes,
    CharacterTracker,
    get_character_tracker,
)

__all__ = [
    "Character",
    "CharacterAppearance",
    "CharacterAttributes",
    "CharacterTracker",
    "get_character_tracker",
]
