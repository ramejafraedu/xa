"""Video Factory V15 — Feedback Loop.

AI-powered quality review + intelligent re-generation.
If the ReviewerAgent rejects, the system can:
  1. Auto-regenerate with specific correction notes
  2. Re-run only the weak stage (not everything)
  3. Escalate to manual review after max iterations

MODULE CONTRACT:
  Input:  StoryState + quality scores
  Output: Decision (pass/retry/manual_review) + correction notes
"""
from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger

from config import settings
from core.state import StoryState
from models.config_models import NichoConfig
from services.http_client import request_with_retry


# ---------------------------------------------------------------------------
# Quality Reviewer — LLM-based content analysis
# ---------------------------------------------------------------------------

class ReviewResult:
    """Output of a quality review."""

    def __init__(
        self,
        overall_score: float = 0,
        hook_score: float = 0,
        coherence_score: float = 0,
        viral_potential: float = 0,
        issues: list[str] = None,
        suggestions: list[str] = None,
        should_retry: bool = False,
        retry_stage: str = "",          # "script", "scenes", "assets"
        correction_notes: str = "",
    ):
        self.overall_score = overall_score
        self.hook_score = hook_score
        self.coherence_score = coherence_score
        self.viral_potential = viral_potential
        self.issues = issues or []
        self.suggestions = suggestions or []
        self.should_retry = should_retry
        self.retry_stage = retry_stage
        self.correction_notes = correction_notes

    @property
    def passed(self) -> bool:
        return self.overall_score >= 7.0 and not self.should_retry


def review_content(state: StoryState, nicho: NichoConfig) -> ReviewResult:
    """AI-powered review of the generated content.

    Analyzes hook strength, script coherence, scene flow,
    and viral potential. Returns specific improvement suggestions.
    """
    if not state.script_full:
        return ReviewResult(issues=["No script generated"])

    system = """Eres un critico de contenido viral profesional.
Evalúa este contenido de video corto con objetividad BRUTAL.

Responde SOLO con JSON:
{
    "overall_score": 8.5,
    "hook_score": 9.0,
    "coherence_score": 8.0,
    "viral_potential": 8.5,
    "issues": ["lista de problemas específicos"],
    "suggestions": ["mejoras concretas"],
    "should_retry": false,
    "retry_stage": "",
    "correction_notes": ""
}

CRITERIOS:
- hook_score: ¿El gancho genera curiosidad INMEDIATA en <2s?
- coherence_score: ¿El script fluye naturalmente? ¿Las escenas conectan?
- viral_potential: ¿La gente compartiría esto? ¿Tiene tensión + payoff?
- Si any score < 7, should_retry=true y sugiere correcciones concretas
- retry_stage: "script" si el problema es narrativo, "scenes" si es visual"""

    scenes_summary = ""
    if state.scenes:
        scenes_summary = "\n".join(
            f"  Scene {s.scene_number}: [{s.mood}] {s.text[:60]}..."
            for s in state.scenes
        )

    user = f"""NICHO: {nicho.nombre}
TONE: {nicho.tono}
PLATFORM: {state.platform}

HOOK: {state.hook}

SCRIPT:
{state.script_full}

CTA: {state.cta}

SCENES:
{scenes_summary or 'No scene plan yet'}

Evalúa y devuelve JSON."""

    try:
        result = _call_llm(system, user, temperature=0.3)
        parsed = _parse_review_json(result)
        if parsed:
            return ReviewResult(**parsed)
    except Exception as e:
        logger.warning(f"Review LLM failed: {e}")

    # Fallback: use V14 heuristic scores from StoryState
    return ReviewResult(
        overall_score=state.overall_score or state.hook_score,
        hook_score=state.hook_score,
        coherence_score=7.0,
        viral_potential=state.script_score,
    )


# ---------------------------------------------------------------------------
# Feedback Loop Controller
# ---------------------------------------------------------------------------

MAX_FEEDBACK_ITERATIONS = 2


def should_iterate(
    state: StoryState,
    review: ReviewResult,
) -> tuple[bool, str, str]:
    """Decide if we should retry a stage.

    Returns:
        (should_retry, stage_to_retry, correction_notes)
    """
    if review.passed:
        return False, "", ""

    if state.feedback_iterations >= MAX_FEEDBACK_ITERATIONS:
        logger.warning(
            f"Max feedback iterations ({MAX_FEEDBACK_ITERATIONS}) reached. "
            f"Sending to manual review."
        )
        return False, "", ""

    stage = review.retry_stage or "script"
    notes = review.correction_notes

    if not notes and review.issues:
        notes = "PROBLEMAS DETECTADOS:\n" + "\n".join(f"- {i}" for i in review.issues)
        if review.suggestions:
            notes += "\n\nSUGERENCIAS:\n" + "\n".join(f"- {s}" for s in review.suggestions)

    return True, stage, notes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_llm(system: str, user: str, temperature: float = 0.3) -> str:
    """Call LLM for review."""
    payload = {
        "model": settings.inference_model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Content-Type": "application/json",
    }

    response = request_with_retry(
        "POST", settings.inference_api_url,
        json_data=payload, headers=headers,
        max_retries=1, timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"Review LLM HTTP {response.status_code}")

    data = response.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")


def _parse_review_json(raw: str) -> Optional[dict]:
    """Parse review JSON from LLM response."""
    text = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
    text = re.sub(r"```\s*", "", text).strip()
    text = re.sub(r"[\x00-\x1f]", " ", text)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            data = json.loads(text)
            # Validate expected fields
            return {
                "overall_score": float(data.get("overall_score", 0)),
                "hook_score": float(data.get("hook_score", 0)),
                "coherence_score": float(data.get("coherence_score", 0)),
                "viral_potential": float(data.get("viral_potential", 0)),
                "issues": data.get("issues", []),
                "suggestions": data.get("suggestions", []),
                "should_retry": bool(data.get("should_retry", False)),
                "retry_stage": str(data.get("retry_stage", "")),
                "correction_notes": str(data.get("correction_notes", "")),
            }
        except (json.JSONDecodeError, ValueError):
            pass

    return None
