from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FlexibleModel(BaseModel):
    # Keep models permissive so legacy agent payloads from LLMs don't fail validation.
    model_config = ConfigDict(extra="allow")


class CharacterInScene(FlexibleModel):
    index: int = 0
    identifier_in_scene: str = "Character"
    static_features: str = ""
    dynamic_features: str = ""
    visibility: str = "visible"
    is_visible: bool = True

    def __str__(self) -> str:
        visible = "visible" if self.is_visible else "invisible"
        return (
            f"{self.identifier_in_scene} [{visible}]\n"
            f"static features: {self.static_features}\n"
            f"dynamic features: {self.dynamic_features}\n"
        )


class CharacterInEvent(FlexibleModel):
    index: int = 0
    identifier_in_event: str = "Character"
    static_features: str = ""
    active_scenes: Dict[int, str] = Field(default_factory=dict)


class CharacterInNovel(FlexibleModel):
    index: int = 0
    identifier_in_novel: str = "Character"
    static_features: str = ""
    active_events: Dict[int, str] = Field(default_factory=dict)


class Event(FlexibleModel):
    index: int = 0
    description: str = ""
    process_chain: List[str] = Field(default_factory=list)
    is_last: bool = False

    def __str__(self) -> str:
        process = "\n".join(f"- {step}" for step in self.process_chain)
        return f"Description: {self.description}\nProcess Chain:\n{process}"


class Scene(FlexibleModel):
    idx: int = 0
    environment: str = ""
    script: str = ""
    characters: List[CharacterInScene] = Field(default_factory=list)

    def __str__(self) -> str:
        chars = "\n".join(str(c).strip() for c in self.characters)
        return (
            f"Scene {self.idx}\n"
            f"Environment: {self.environment}\n"
            f"Script:\n{self.script}\n"
            f"Characters:\n{chars}"
        )


class ShotBriefDescription(FlexibleModel):
    idx: int = 0
    cam_idx: int = 0
    visual_desc: str = ""
    audio_desc: str = ""
    is_last: bool = False


ShotBriefDescr = ShotBriefDescription


class ShotDescription(ShotBriefDescription):
    variation_type: str = "small"
    variation_reason: str = ""
    ff_desc: str = ""
    ff_vis_char_idxs: List[int] = Field(default_factory=list)
    lf_desc: str = ""
    lf_vis_char_idxs: List[int] = Field(default_factory=list)
    motion_desc: str = ""


class Camera(FlexibleModel):
    idx: int = 0
    active_shot_idxs: List[int] = Field(default_factory=list)
    parent_cam_idx: Optional[int] = None
    parent_shot_idx: Optional[int] = None
    reason: Optional[str] = None
    is_parent_fully_covers_child: Optional[bool] = None
    missing_info: Optional[str] = None


class ImageOutput(FlexibleModel):
    fmt: str = "pil"
    ext: str = "png"
    data: Any = None
    path: Optional[str] = None


class VideoOutput(FlexibleModel):
    fmt: str = "video"
    ext: str = "mp4"
    data: Any = None
    path: Optional[str] = None
