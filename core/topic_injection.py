"""Video Factory V16 — Topic Injection System.

Allows manual input of video ideas and narrative angles.
Replaces auto-generated trending topics with user-provided concepts.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger

from config import settings
from core.state import StoryState
from models.content import JobManifest, JobStatus


class IdeaStatus(str, Enum):
    PENDING = "pending"      # Submitted, awaiting review
    APPROVED = "approved"    # Approved, ready for generation
    REJECTED = "rejected"    # Rejected by user
    GENERATING = "generating"  # Currently being processed
    COMPLETED = "completed"  # Video generated successfully
    FAILED = "failed"        # Generation failed


@dataclass
class UserIdea:
    """A user-submitted video idea."""
    id: str
    topic: str                    # Main topic (e.g., "Efecto Dunning-Kruger")
    angle: str                    # Narrative angle (e.g., "¿Por qué los genios fracasan?")
    nicho: str                    # Target niche/category
    status: IdeaStatus
    created_at: float
    notes: str = ""               # User/admin notes
    script_approved: bool = False  # Whether script was approved
    video_path: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class TopicInjectionSystem:
    """Manage user-submitted video ideas.
    
    Usage:
        injection = TopicInjectionSystem()
        
        # Submit new idea
        idea = injection.submit_idea(
            topic="Efecto Dunning-Kruger",
            angle="¿Por qué los incompetentes no se dan cuenta?",
            nicho="curiosidades"
        )
        
        # Review pending ideas
        pending = injection.get_pending_ideas()
        
        # Approve and start generation
        injection.approve_idea(idea.id)
    """
    
    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or (settings.base_dir / "data" / "user_ideas.json")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._ideas: dict[str, UserIdea] = {}
        self._load_ideas()
    
    def _load_ideas(self) -> None:
        """Load existing ideas from storage."""
        if not self.storage_path.exists():
            return
        
        try:
            import json
            data = json.loads(self.storage_path.read_text("utf-8"))
            for item in data:
                idea = UserIdea(
                    id=item["id"],
                    topic=item["topic"],
                    angle=item.get("angle", ""),
                    nicho=item.get("nicho", "curiosidades"),
                    status=IdeaStatus(item.get("status", "pending")),
                    created_at=item.get("created_at", time.time()),
                    notes=item.get("notes", ""),
                    script_approved=item.get("script_approved", False),
                    video_path=item.get("video_path"),
                    metadata=item.get("metadata", {})
                )
                self._ideas[idea.id] = idea
            logger.info(f"Loaded {len(self._ideas)} user ideas from storage")
        except Exception as e:
            logger.warning(f"Failed to load user ideas: {e}")
    
    def _save_ideas(self) -> None:
        """Persist ideas to storage."""
        try:
            import json
            data = []
            for idea in self._ideas.values():
                data.append({
                    "id": idea.id,
                    "topic": idea.topic,
                    "angle": idea.angle,
                    "nicho": idea.nicho,
                    "status": idea.status.value,
                    "created_at": idea.created_at,
                    "notes": idea.notes,
                    "script_approved": idea.script_approved,
                    "video_path": idea.video_path,
                    "metadata": idea.metadata
                })
            self.storage_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        except Exception as e:
            logger.error(f"Failed to save user ideas: {e}")
    
    def submit_idea(
        self,
        topic: str,
        angle: str = "",
        nicho: str = "curiosidades",
        notes: str = ""
    ) -> UserIdea:
        """Submit a new video idea.
        
        Args:
            topic: Main topic/subject of the video
            angle: Narrative angle or specific approach (optional)
            nicho: Target niche/category
            notes: Additional notes (optional)
            
        Returns:
            The created UserIdea with generated ID
        """
        idea = UserIdea(
            id=str(uuid.uuid4())[:8],
            topic=topic.strip(),
            angle=angle.strip(),
            nicho=nicho.strip().lower(),
            status=IdeaStatus.PENDING,
            created_at=time.time(),
            notes=notes
        )
        
        self._ideas[idea.id] = idea
        self._save_ideas()
        
        logger.info(
            f"📝 User idea submitted: '{idea.topic}' "
            f"(ID: {idea.id}, Nicho: {idea.nicho})"
        )
        
        return idea
    
    def get_idea(self, idea_id: str) -> Optional[UserIdea]:
        """Get a specific idea by ID."""
        return self._ideas.get(idea_id)
    
    def get_pending_ideas(self) -> list[UserIdea]:
        """Get all ideas awaiting approval."""
        return [
            idea for idea in self._ideas.values()
            if idea.status == IdeaStatus.PENDING
        ]
    
    def get_all_ideas(self, status: Optional[IdeaStatus] = None) -> list[UserIdea]:
        """Get all ideas, optionally filtered by status."""
        if status:
            return [idea for idea in self._ideas.values() if idea.status == status]
        return list(self._ideas.values())
    
    def approve_idea(self, idea_id: str, admin_notes: str = "") -> Optional[JobManifest]:
        """Approve an idea and create a job manifest for generation.
        
        Args:
            idea_id: The idea ID to approve
            admin_notes: Optional notes from admin
            
        Returns:
            JobManifest ready for pipeline execution, or None if idea not found
        """
        idea = self._ideas.get(idea_id)
        if not idea:
            logger.error(f"Idea {idea_id} not found")
            return None
        
        idea.status = IdeaStatus.APPROVED
        if admin_notes:
            idea.notes += f"\n[Admin]: {admin_notes}"
        
        # Create job manifest
        from models.config_models import NichoConfig
        
        # Get or create nicho config
        nicho = self._get_nicho_config(idea.nicho)
        
        # Create manifest with user-provided topic
        manifest = JobManifest(
            job_id=f"user_{idea.id}_{int(time.time())}",
            nicho_slug=idea.nicho,
            status=JobStatus.QUEUED.value,
            user_provided_topic=True,  # Flag to skip trending research
            user_topic=idea.topic,
            user_angle=idea.angle,
            created_at=time.time()
        )
        
        idea.metadata["job_id"] = manifest.job_id
        self._save_ideas()
        
        logger.info(
            f"✅ Idea approved: '{idea.topic}' → Job {manifest.job_id}"
        )
        
        return manifest
    
    def reject_idea(self, idea_id: str, reason: str = "") -> bool:
        """Reject an idea.
        
        Args:
            idea_id: The idea ID to reject
            reason: Rejection reason (optional)
            
        Returns:
            True if rejected successfully, False if not found
        """
        idea = self._ideas.get(idea_id)
        if not idea:
            return False
        
        idea.status = IdeaStatus.REJECTED
        if reason:
            idea.notes += f"\n[Rejected]: {reason}"
        
        self._save_ideas()
        logger.info(f"❌ Idea rejected: '{idea.topic}' (ID: {idea.id})")
        return True
    
    def update_idea_status(
        self,
        idea_id: str,
        status: IdeaStatus,
        video_path: Optional[str] = None
    ) -> bool:
        """Update idea status (called by pipeline during generation).
        
        Args:
            idea_id: The idea ID
            status: New status
            video_path: Path to generated video (if completed)
            
        Returns:
            True if updated successfully
        """
        idea = self._ideas.get(idea_id)
        if not idea:
            return False
        
        idea.status = status
        if video_path:
            idea.video_path = video_path
        
        self._save_ideas()
        return True
    
    def delete_idea(self, idea_id: str) -> bool:
        """Permanently delete an idea."""
        if idea_id in self._ideas:
            del self._ideas[idea_id]
            self._save_ideas()
            logger.info(f"🗑️ Idea deleted: {idea_id}")
            return True
        return False
    
    def _get_nicho_config(self, nicho_slug: str) -> NichoConfig:
        """Get nicho config, creating default if not exists."""
        from nichos import get_nicho_by_slug
        
        nicho = get_nicho_by_slug(nicho_slug)
        if nicho:
            return nicho
        
        # Create default config for unknown niche
        return NichoConfig(
            slug=nicho_slug,
            nombre=f"User: {nicho_slug}",
            tono="informativo y curioso",
            plataforma="tiktok_reels",
            num_clips=8,
            keywords_count=8
        )
    
    def get_stats(self) -> dict:
        """Get statistics about user ideas."""
        all_ideas = list(self._ideas.values())
        
        return {
            "total": len(all_ideas),
            "pending": len([i for i in all_ideas if i.status == IdeaStatus.PENDING]),
            "approved": len([i for i in all_ideas if i.status == IdeaStatus.APPROVED]),
            "completed": len([i for i in all_ideas if i.status == IdeaStatus.COMPLETED]),
            "rejected": len([i for i in all_ideas if i.status == IdeaStatus.REJECTED]),
            "failed": len([i for i in all_ideas if i.status == IdeaStatus.FAILED])
        }


# Singleton instance
_injection_system: Optional[TopicInjectionSystem] = None

def get_injection_system() -> TopicInjectionSystem:
    """Get or create the singleton TopicInjectionSystem."""
    global _injection_system
    if _injection_system is None:
        _injection_system = TopicInjectionSystem()
    return _injection_system
