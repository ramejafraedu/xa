"""Lightweight A/B experiment tracking for Video Factory.

Each pipeline run can be tagged with an `experiment_id` and a deterministic
`variant` (A/B/...). Outcomes are appended to `experiments_log.jsonl` for later
aggregation by the dashboard.

MODULE CONTRACT:
  assign_variant(experiment_id, job_id, variants) → str
  record_outcome(manifest) → None
  summary() → dict  # for `/api/experiments/summary`
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from loguru import logger

from config import settings


_DEFAULT_VARIANTS: tuple[str, ...] = ("A", "B")


def _experiments_dir() -> Path:
    base = Path(settings.output_dir) / "experiments"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _log_path() -> Path:
    return _experiments_dir() / "experiments_log.jsonl"


def assign_variant(
    experiment_id: str,
    job_id: str,
    variants: Sequence[str] = _DEFAULT_VARIANTS,
) -> str:
    """Deterministically pick a variant for a job.

    Uses sha1(experiment_id + job_id) to keep the choice reproducible across
    resumes and debugging sessions.
    """
    if not variants:
        return "A"
    seed = f"{experiment_id}:{job_id}".encode("utf-8")
    digest = hashlib.sha1(seed).digest()
    idx = int.from_bytes(digest[:4], byteorder="big") % len(variants)
    return variants[idx]


def stable_style_seed(job_id: str) -> int:
    """Derive a 31-bit style seed from the job id."""
    digest = hashlib.sha1(job_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big") & 0x7FFFFFFF


@dataclass
class ExperimentOutcome:
    job_id: str
    nicho_slug: str
    experiment_id: str
    variant: str
    render_profile: str
    duration_seconds: float
    quality_score: float
    viral_score: float
    hook_score: float
    success: bool
    cost_usd: float
    recorded_at: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "nicho_slug": self.nicho_slug,
            "experiment_id": self.experiment_id,
            "variant": self.variant,
            "render_profile": self.render_profile,
            "duration_seconds": round(float(self.duration_seconds or 0.0), 2),
            "quality_score": round(float(self.quality_score or 0.0), 2),
            "viral_score": round(float(self.viral_score or 0.0), 2),
            "hook_score": round(float(self.hook_score or 0.0), 2),
            "success": bool(self.success),
            "cost_usd": round(float(self.cost_usd or 0.0), 4),
            "recorded_at": int(self.recorded_at),
        }


def record_outcome(manifest: Any) -> bool:
    """Append an outcome row for `manifest` to the experiments JSONL log."""
    experiment_id = str(getattr(manifest, "experiment_id", "") or "")
    if not experiment_id:
        return False

    try:
        outcome = ExperimentOutcome(
            job_id=str(getattr(manifest, "job_id", "") or ""),
            nicho_slug=str(getattr(manifest, "nicho_slug", "") or ""),
            experiment_id=experiment_id,
            variant=str(getattr(manifest, "variant", "") or getattr(manifest, "ab_variant", "") or ""),
            render_profile=str(getattr(manifest, "render_profile", "") or getattr(manifest, "render_backend", "") or ""),
            duration_seconds=float(getattr(manifest, "duration_seconds", 0.0) or 0.0),
            quality_score=float(getattr(manifest, "quality_score", 0.0) or 0.0),
            viral_score=float(getattr(manifest, "viral_score", 0.0) or 0.0),
            hook_score=float(getattr(manifest, "hook_score", 0.0) or 0.0),
            success=str(getattr(manifest, "status", "")) == "success",
            cost_usd=float(getattr(manifest, "cost_actual_usd", 0.0) or 0.0),
            recorded_at=int(time.time()),
        )
    except Exception as exc:
        logger.warning(f"experiments: could not build outcome ({exc})")
        return False

    try:
        path = _log_path()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
        return True
    except Exception as exc:
        logger.warning(f"experiments: could not persist outcome ({exc})")
        return False


def _iter_outcomes() -> Iterable[dict[str, Any]]:
    path = _log_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.debug(f"experiments: could not read log ({exc})")
    return out


def summary() -> dict[str, Any]:
    """Aggregate experiment outcomes for dashboards."""
    outcomes = list(_iter_outcomes())
    if not outcomes:
        return {"experiments": [], "total_outcomes": 0}

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in outcomes:
        key = (str(row.get("experiment_id") or ""), str(row.get("variant") or ""))
        bucket = grouped.setdefault(
            key,
            {
                "experiment_id": key[0],
                "variant": key[1],
                "runs": 0,
                "successes": 0,
                "failures": 0,
                "sum_quality": 0.0,
                "sum_viral": 0.0,
                "sum_hook": 0.0,
                "sum_duration": 0.0,
                "sum_cost": 0.0,
                "last_recorded_at": 0,
                "render_profile_breakdown": {},
            },
        )
        bucket["runs"] += 1
        if row.get("success"):
            bucket["successes"] += 1
        else:
            bucket["failures"] += 1
        bucket["sum_quality"] += float(row.get("quality_score", 0) or 0)
        bucket["sum_viral"] += float(row.get("viral_score", 0) or 0)
        bucket["sum_hook"] += float(row.get("hook_score", 0) or 0)
        bucket["sum_duration"] += float(row.get("duration_seconds", 0) or 0)
        bucket["sum_cost"] += float(row.get("cost_usd", 0) or 0)
        bucket["last_recorded_at"] = max(
            int(bucket["last_recorded_at"] or 0),
            int(row.get("recorded_at", 0) or 0),
        )
        profile = str(row.get("render_profile") or "unknown")
        bucket["render_profile_breakdown"][profile] = (
            bucket["render_profile_breakdown"].get(profile, 0) + 1
        )

    experiments: list[dict[str, Any]] = []
    for bucket in grouped.values():
        runs = max(1, bucket["runs"])
        experiments.append(
            {
                "experiment_id": bucket["experiment_id"],
                "variant": bucket["variant"],
                "runs": bucket["runs"],
                "successes": bucket["successes"],
                "failures": bucket["failures"],
                "success_rate": round(bucket["successes"] / runs, 3),
                "avg_quality": round(bucket["sum_quality"] / runs, 2),
                "avg_viral": round(bucket["sum_viral"] / runs, 2),
                "avg_hook": round(bucket["sum_hook"] / runs, 2),
                "avg_duration_seconds": round(bucket["sum_duration"] / runs, 2),
                "avg_cost_usd": round(bucket["sum_cost"] / runs, 4),
                "last_recorded_at": bucket["last_recorded_at"],
                "render_profile_breakdown": bucket["render_profile_breakdown"],
            }
        )

    experiments.sort(key=lambda item: (item["experiment_id"], item["variant"]))
    return {"experiments": experiments, "total_outcomes": len(outcomes)}
