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

# Verification support
_verification_agent: Optional["VerificationAgent"] = None  # Lazy import

def _get_verification_agent() -> Optional["VerificationAgent"]:
    """Lazy initialization of VerificationAgent."""
    global _verification_agent
    if _verification_agent is None:
        try:
            from agents.verification_agent import VerificationAgent
            _verification_agent = VerificationAgent()
        except Exception as e:
            logger.debug(f"VerificationAgent not available: {e}")
    return _verification_agent

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
        
        # Verification metadata (populated for script checkpoints)
        self.verification_score: Optional[float] = None
        self.verification_summary: Optional[str] = None
        self.verification_entities: list[dict] = []

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
            # Check if fact verification should block auto-approval
            if stage == "script" and state and state.script_full:
                from config import settings
                
                if settings.fact_verification_should_block(nicho_slug=getattr(state, 'nicho_slug', '')):
                    verifier = _get_verification_agent()
                    if verifier:
                        try:
                            report = verifier.run(state.script_full, state.hook)
                            
                            # If score below minimum and not in interactive mode, log warning but still approve
                            # (AUTO mode is meant to be hands-off, but we log for review)
                            if report.overall_score < settings.fact_verification_min_score:
                                logger.warning(
                                    f"⚠️  Verification score {report.overall_score:.0f}% below threshold "
                                    f"({settings.fact_verification_min_score}%). "
                                    f"Recommendation: {report.recommendation}. "
                                    f"Unverified: {report.unverified_count}, Contradictory: {report.contradictory_count}"
                                )
                            else:
                                logger.info(f"✅ Verification passed: {report.overall_score:.0f}%")
                                
                        except Exception as e:
                            logger.debug(f"Auto verification error: {e}")
                
                elif settings.fact_verification_should_warn():
                    # Just log that verification would have flagged issues
                    logger.info("🔍 Fact verification enabled in warning mode (AUTO)")
            
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
        
        # Add verification report for script stage
        if stage == "script" and state and state.script_full:
            verifier = _get_verification_agent()
            if verifier:
                try:
                    report = verifier.run(state.script_full, state.hook)
                    checkpoint_data["verification"] = {
                        "score": report.overall_score,
                        "recommendation": report.recommendation,
                        "summary": report.summary,
                        "entities": [
                            {
                                "type": e.entity_type,
                                "text": e.original_text,
                                "status": e.status.value,
                                "confidence": e.confidence,
                                "suggestion": e.suggestion
                            }
                            for e in report.entities
                        ]
                    }
                    logger.info(f"🔍 Web checkpoint verification: {report.overall_score:.0f}%")
                except Exception as e:
                    logger.debug(f"Web verification failed: {e}")
        
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

        # Verification for script stage
        verification_report = None
        if stage == "script" and state:
            verifier = _get_verification_agent()
            if verifier and state.script_full:
                try:
                    console.print("[dim]🔍 Verificando datos del guion...[/dim]")
                    verification_report = verifier.run(state.script_full, state.hook)
                    self._display_verification_report(verification_report)
                except Exception as e:
                    logger.debug(f"Verification failed: {e}")

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
            # Include verification notes in result if available
            result = CheckpointResult(Decision.APPROVE, content)
            if verification_report:
                result.verification_score = verification_report.overall_score
                result.verification_summary = verification_report.summary
            return result

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

    def _display_verification_report(self, report) -> None:
        """Display verification report in Rich format."""
        from rich.table import Table
        
        # Determine color based on score
        score = report.overall_score
        if score >= 80:
            score_color = "green"
            status = "✅ APROBADO"
        elif score >= 60:
            score_color = "yellow"
            status = "🟡 REVISAR"
        else:
            score_color = "red"
            status = "❌ RECHAZAR"
        
        # Summary panel
        console.print(Panel(
            f"[bold {score_color}]📊 Verificación Factual: {score:.0f}% - {status}[/bold {score_color}]\n"
            f"[dim]✓ {sum(1 for e in report.entities if e.status.value == 'verified')} verificados | "
            f"⚠ {sum(1 for e in report.entities if e.status.value == 'unverified')} no verificados | "
            f"✗ {report.contradictory_count} contradictorios[/dim]",
            border_style=score_color,
            padding=(0, 1),
        ))
        
        # Detailed table if there are issues
        unverified = [e for e in report.entities if e.status.value in ('unverified', 'contradictory', 'partial')]
        if unverified:
            table = Table(title="Entidades a Verificar", show_lines=True, title_style="dim")
            table.add_column("Tipo", style="cyan", width=12)
            table.add_column("Texto", width=30)
            table.add_column("Estado", width=10)
            table.add_column("Sugerencia", width=35)
            
            for entity in unverified[:5]:  # Show first 5
                status_icon = {
                    "unverified": "⚠️",
                    "contradictory": "❌",
                    "partial": "🟡",
                    "verified": "✅"
                }.get(entity.status.value, "❓")
                
                table.add_row(
                    entity.entity_type,
                    entity.original_text[:40],
                    f"{status_icon} {entity.status.value}",
                    entity.suggestion[:50] or "-"
                )
            
            console.print(table)
            console.print()

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
