"""Video Factory V15 — Director (Human-in-the-Loop Control).

The Director orchestrates the multi-agent pipeline with optional
human checkpoints. Two modes:
  - INTERACTIVE: Rich CLI interface, you approve/edit at each stage
  - AUTO: Everything auto-approves (like V14 but with StoryState coherence)

MODULE CONTRACT:
  Input:  stage name + data to review
  Output: Decision (approved content, possibly edited)
"""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from core.state import SceneBlueprint, StoryState

console = Console()

# Web checkpoints shared state
WEB_CHECKPOINTS: dict[str, dict] = {}
WEB_RESOLUTIONS: dict[str, dict] = {}

class DirectorMode(str, Enum):
    INTERACTIVE = "interactive"
    AUTO = "auto"
    WEB = "web"


class Decision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


class CheckpointResult:
    """Result of a Director checkpoint."""

    def __init__(
        self,
        decision: Decision,
        content: str = "",
        notes: str = "",
    ):
        self.decision = decision
        self.content = content    # Original or edited content
        self.notes = notes        # Rejection/edit notes for the agent

    @property
    def approved(self) -> bool:
        return self.decision == Decision.APPROVE

    @property
    def edited(self) -> bool:
        return self.decision == Decision.EDIT


class Director:
    """Orchestrates the pipeline with human checkpoints.

    Usage:
        director = Director(DirectorMode.INTERACTIVE)
        result = director.checkpoint("script", script_text, state)
        if not result.approved:
            # re-generate with result.notes
    """

    def __init__(self, mode: DirectorMode = DirectorMode.AUTO, job_id: str = "default"):
        self.mode = mode
        self.job_id = job_id
        self._checkpoint_history: list[dict] = []

    # ----- Main checkpoint -----

    def checkpoint(
        self,
        stage: str,
        content: str,
        state: Optional[StoryState] = None,
        metadata: Optional[dict] = None,
    ) -> CheckpointResult:
        """Present content for review.

        In AUTO mode: always approves.
        In INTERACTIVE mode: shows content, asks for decision.

        Args:
            stage: Name of the pipeline stage ("research", "script", "scenes", etc.)
            content: The content to review (text, JSON string, etc.)
            state: Current StoryState for context.
            metadata: Extra info to display (scores, etc.)

        Returns:
            CheckpointResult with decision and optional notes.
        """
        if self.mode == DirectorMode.AUTO:
            logger.debug(f"Director AUTO-APPROVE: {stage}")
            self._record(stage, Decision.APPROVE)
            return CheckpointResult(Decision.APPROVE, content)

        if self.mode == DirectorMode.WEB:
            return self._web_checkpoint(stage, content, state, metadata)

        return self._interactive_checkpoint(stage, content, state, metadata)

    def checkpoint_scenes(
        self,
        scenes: list[SceneBlueprint],
        state: Optional[StoryState] = None,
    ) -> CheckpointResult:
        """Specialized checkpoint for scene review with table display."""
        if self.mode == DirectorMode.AUTO:
            self._record("scenes", Decision.APPROVE)
            scenes_json = json.dumps(
                [s.model_dump() for s in scenes], ensure_ascii=False, indent=2
            )
            return CheckpointResult(Decision.APPROVE, scenes_json)

        scenes_json = json.dumps(
            [s.model_dump() for s in scenes], ensure_ascii=False, indent=2
        )

        if self.mode == DirectorMode.WEB:
            return self._web_checkpoint("scenes", scenes_json, state, None)

        return self._interactive_scenes(scenes, state)

    # ----- Interactive implementations -----

    def _web_checkpoint(
        self,
        stage: str,
        content: str,
        state: Optional[StoryState],
        metadata: Optional[dict],
    ) -> CheckpointResult:
        """Dashboard checkpoint - pushes to a dict and waits for the frontend."""
        # Create payload
        checkpoint_data = {
            "job_id": self.job_id,
            "stage": stage,
            "content": content,
            "topic": state.topic if state else "Unknown",
            "metadata": metadata or {},
            "timestamp": time.time()
        }
        
        # Publish checkpoint
        WEB_CHECKPOINTS[self.job_id] = checkpoint_data
        WEB_RESOLUTIONS.pop(self.job_id, None)  # Clear previous resolution if any
        
        logger.info(f"⏸️ Pipeline paused at [{stage}] awaiting WEB approval for {self.job_id}")
        
        # Wait for resolution
        while self.job_id not in WEB_RESOLUTIONS:
            time.sleep(1)
            
        resolution = WEB_RESOLUTIONS.pop(self.job_id)
        WEB_CHECKPOINTS.pop(self.job_id, None)  # Clear the checkpoint
        
        decision_str = resolution.get("decision", "approve")
        notes = resolution.get("notes", "")
        
        decision_map = {
            "approve": Decision.APPROVE,
            "reject": Decision.REJECT,
            "edit": Decision.EDIT
        }
        decision = decision_map.get(decision_str.lower(), Decision.APPROVE)
        
        self._record(stage, decision, notes)
        logger.info(f"▶️ Pipeline resumed! WEB Decision: {decision.name}")
        return CheckpointResult(decision, content, notes)

    def _interactive_checkpoint(
        self,
        stage: str,
        content: str,
        state: Optional[StoryState],
        metadata: Optional[dict],
    ) -> CheckpointResult:
        """Rich CLI checkpoint — shows content and asks for decision."""
        # Header
        stage_icons = {
            "research": "🔍",
            "script": "✍️",
            "scenes": "🎬",
            "assets": "🎨",
            "render": "🎥",
            "review": "🔬",
        }
        icon = stage_icons.get(stage, "📋")
        console.print()
        console.print(Panel(
            f"[bold cyan]{icon} CHECKPOINT: {stage.upper()}[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        ))

        # Show context if available
        if state and state.topic:
            console.print(
                f"  [dim]Topic:[/dim] {state.topic} | "
                f"[dim]Platform:[/dim] {state.platform} | "
                f"[dim]Tone:[/dim] {state.tone}"
            )

        # Show metadata
        if metadata:
            meta_parts = []
            for k, v in metadata.items():
                meta_parts.append(f"[dim]{k}:[/dim] {v}")
            console.print("  " + " | ".join(meta_parts))

        console.print()

        # Show content (with syntax highlighting for JSON)
        if content.strip().startswith("{") or content.strip().startswith("["):
            try:
                formatted = json.dumps(json.loads(content), ensure_ascii=False, indent=2)
                console.print(Syntax(formatted, "json", theme="monokai", line_numbers=False))
            except (json.JSONDecodeError, ValueError):
                console.print(Panel(content[:2000], border_style="white"))
        else:
            # Text content — show in a panel
            console.print(Panel(content[:2000], border_style="white"))

        console.print()

        # Ask for decision
        decision = Prompt.ask(
            "[bold]Decisión[/bold]",
            choices=["y", "n", "e"],
            default="y",
        )

        if decision == "y":
            self._record(stage, Decision.APPROVE)
            console.print("[green]✅ Aprobado[/green]\n")
            return CheckpointResult(Decision.APPROVE, content)

        elif decision == "e":
            console.print("[yellow]✏️  Modo edición[/yellow]")
            notes = Prompt.ask("Instrucciones para regenerar")
            self._record(stage, Decision.EDIT, notes)
            console.print(f"[yellow]📝 Nota guardada: {notes}[/yellow]\n")
            return CheckpointResult(Decision.EDIT, content, notes)

        else:
            notes = Prompt.ask("¿Por qué rechazas?", default="No me convence")
            self._record(stage, Decision.REJECT, notes)
            console.print(f"[red]❌ Rechazado: {notes}[/red]\n")
            return CheckpointResult(Decision.REJECT, content, notes)

    def _interactive_scenes(
        self,
        scenes: list[SceneBlueprint],
        state: Optional[StoryState],
    ) -> CheckpointResult:
        """Table-based scene review."""
        console.print()
        console.print(Panel(
            "[bold cyan]🎬 CHECKPOINT: SCENE PLAN[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        ))

        table = Table(title="Escenas del Video", show_lines=True)
        table.add_column("#", style="bold", width=3)
        table.add_column("Texto", width=40)
        table.add_column("Mood", width=12)
        table.add_column("Duración", width=8)
        table.add_column("Cámara", width=20)
        table.add_column("Transición", width=10)

        for s in scenes:
            table.add_row(
                str(s.scene_number),
                s.text[:80] + ("..." if len(s.text) > 80 else ""),
                s.mood,
                f"{s.duration_seconds:.1f}s",
                s.camera_notes or "-",
                s.transition_out,
            )

        console.print(table)

        total = sum(s.duration_seconds for s in scenes)
        console.print(f"\n  [dim]Total duración:[/dim] {total:.1f}s | [dim]Escenas:[/dim] {len(scenes)}")
        console.print()

        decision = Prompt.ask(
            "[bold]Decisión[/bold]",
            choices=["y", "n", "e"],
            default="y",
        )

        scenes_json = json.dumps(
            [s.model_dump() for s in scenes], ensure_ascii=False, indent=2
        )

        if decision == "y":
            self._record("scenes", Decision.APPROVE)
            console.print("[green]✅ Escenas aprobadas[/green]\n")
            return CheckpointResult(Decision.APPROVE, scenes_json)

        elif decision == "e":
            notes = Prompt.ask("¿Qué cambiar en las escenas?")
            self._record("scenes", Decision.EDIT, notes)
            return CheckpointResult(Decision.EDIT, scenes_json, notes)

        else:
            notes = Prompt.ask("¿Por qué rechazas?", default="Escenas no coherentes")
            self._record("scenes", Decision.REJECT, notes)
            return CheckpointResult(Decision.REJECT, scenes_json, notes)

    # ----- Utility -----

    def _record(self, stage: str, decision: Decision, notes: str = "") -> None:
        self._checkpoint_history.append({
            "stage": stage,
            "decision": decision.value,
            "notes": notes,
        })

    @property
    def history(self) -> list[dict]:
        return self._checkpoint_history
