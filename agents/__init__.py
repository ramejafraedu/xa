"""Agent modules. Heavy imports are lazy so ``agents.semantic_memory`` stays lightweight."""

from __future__ import annotations

from typing import Any

__all__ = [
    "Screenwriter",
    "StoryboardArtist",
    "CameraImageGenerator",
    "CharacterExtractor",
    "CharacterPortraitsGenerator",
    "ReferenceImageSelector",
]


def __getattr__(name: str) -> Any:
    if name == "Screenwriter":
        from .screenwriter import Screenwriter

        return Screenwriter
    if name == "StoryboardArtist":
        from .storyboard_artist import StoryboardArtist

        return StoryboardArtist
    if name == "CameraImageGenerator":
        from .camera_image_generator import CameraImageGenerator

        return CameraImageGenerator
    if name == "CharacterExtractor":
        from .character_extractor import CharacterExtractor

        return CharacterExtractor
    if name == "CharacterPortraitsGenerator":
        from .character_portraits_generator import CharacterPortraitsGenerator

        return CharacterPortraitsGenerator
    if name == "ReferenceImageSelector":
        from .reference_image_selector import ReferenceImageSelector

        return ReferenceImageSelector
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
