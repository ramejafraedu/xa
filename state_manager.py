"""State Manager — Job manifests + checkpointing + idempotency.

Each video job gets a `job_manifest_{job_id}.json` that contains:
  - job_id, nicho, stage, retry_count
  - input_hash (for idempotency)
  - all artifact_paths
  - error_type + error_code
  - quality_scores
  - model_version
  - timings per stage

Supports:
  - Crash recovery (resume from last completed stage)
  - Idempotency (skip stages whose input hasn't changed)
  - `--resume JOB_ID` from CLI
  - Full audit trail per video

MODULE CONTRACT:
  Input:  JobManifest (in-memory)
  Output: JSON file on disk, list of resumable jobs
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from models.content import JobManifest, JobStatus

try:
    from cost_tracker import CostTracker, BudgetMode
except ImportError:
    try:
        from tools.cost_tracker import CostTracker, BudgetMode
    except ImportError:
        CostTracker = None
        BudgetMode = None

try:
    from lib.scoring import QualityGate
except ImportError:
    QualityGate = None


class StateManager:
    """Manages pipeline state persistence via job manifest files."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        
        self.checkpoints_root = self.temp_dir / "checkpoints"
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)
        self.cost_tracker = None
        self.quality_gate = QualityGate() if QualityGate else None

    def initialize_cost_tracker(self, budget: float, mode_str: str) -> None:
        if CostTracker:
            mode = BudgetMode.WARN if mode_str != "strict" else BudgetMode.CAP
            log_path = self.temp_dir / "cost_log.json"
            self.cost_tracker = CostTracker(
                budget_total_usd=budget,
                mode=mode,
                cost_log_path=log_path
            )

    def _manifest_path(self, job_id: str) -> Path:
        return self.temp_dir / f"job_manifest_{job_id}.json"

    def _checkpoint_dir(self, job_id: str) -> Path:
        return self.checkpoints_root / job_id

    def _checkpoint_path(self, job_id: str, stage: str) -> Path:
        return self._checkpoint_dir(job_id) / f"checkpoint_{stage}.json"

    def _decision_log_path(self, job_id: str) -> Path:
        return self._checkpoint_dir(job_id) / "decision_log.json"

    # ----- Save / Load -----

    def save(self, manifest: JobManifest) -> None:
        """Persist current job manifest."""
        path = self._manifest_path(manifest.job_id)
        if manifest.stage_checkpoints is None:
            manifest.stage_checkpoints = {}
        if manifest.stage_artifacts is None:
            manifest.stage_artifacts = {}
        data = manifest.model_dump(mode="json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug(f"Manifest saved: {manifest.job_id} status={manifest.status}")

    def load(self, job_id: str) -> Optional[JobManifest]:
        """Load saved manifest if it exists."""
        path = self._manifest_path(job_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            manifest = JobManifest(**data)
            logger.info(f"Resuming job {job_id} from status: {manifest.status}")
            return manifest
        except Exception as e:
            logger.warning(f"Failed to load manifest for {job_id}: {e}")
            return None

    # ----- Stage Management -----

    def mark_stage(self, manifest: JobManifest, stage: str, elapsed: float = 0) -> None:
        """Mark a stage as completed, record timing, and save."""
        status_value = f"completed_{stage}"
        manifest.status = status_value
        if elapsed > 0:
            manifest.timings[stage] = round(elapsed, 2)
        self.write_stage_checkpoint(
            manifest,
            stage=stage,
            status="completed",
            elapsed=elapsed,
            metadata={"manifest_status": status_value},
        )
        self.save(manifest)

    def write_stage_checkpoint(
        self,
        manifest: JobManifest,
        stage: str,
        status: str = "completed",
        artifacts: Optional[dict] = None,
        metadata: Optional[dict] = None,
        elapsed: float = 0.0,
    ) -> Path:
        """Persist a stage-level checkpoint in parallel to the manifest file.

        This enables a safe dual-write migration towards stage-aware governance
        while preserving existing `save/load/mark_stage` semantics.
        """
        artifacts = artifacts or {}
        metadata = metadata or {}

        checkpoint = {
            "version": "1.0",
            "project_id": manifest.job_id,
            "pipeline_type": manifest.pipeline_type or "v15",
            "stage": stage,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checkpoint_policy": manifest.checkpoint_policy or "guided",
            "human_approval_required": bool(manifest.human_approval_required),
            "human_approved": bool(manifest.human_approved),
            "style_playbook": manifest.style_playbook or "",
            "artifacts": artifacts,
            "metadata": {
                **metadata,
                "elapsed_seconds": round(float(elapsed or 0.0), 2),
            },
        }

        decision_log_ref = self._merge_decision_log(manifest, stage)
        if decision_log_ref:
            checkpoint["decision_log_ref"] = decision_log_ref
            manifest.decision_log_ref = decision_log_ref

        cp_path = self._checkpoint_path(manifest.job_id, stage)
        cp_path.parent.mkdir(parents=True, exist_ok=True)
        cp_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest.stage_checkpoints[stage] = {
            "status": status,
            "timestamp": checkpoint["timestamp"],
            "path": str(cp_path),
            "elapsed_seconds": round(float(elapsed or 0.0), 2),
        }

    # ----- Quality Gates & QA -----

    def evaluate_quality_gate(self, stage: str, artifacts: dict) -> dict:
        """Run standard OpenMontage quality gate for a stage."""
        logger.info(f"Evaluating quality gate for stage: {stage}")
        try:
            from lib.scoring import evaluate_stage
            score_data = evaluate_stage(stage, artifacts)
            if score_data.get("score", 0) < 0.7:
                logger.warning(f"Quality gate failed for {stage}: {score_data}")
            return score_data
        except ImportError:
            logger.debug("lib.scoring not found. Skipping quality gate.")
            return {"score": 1.0, "status": "skipped"}

    def run_post_render_qa(self, video_path: str) -> dict:
        """Run post-render QA analysis to check subtitles, sync, and artifacts."""
        logger.info(f"Running Post-Render QA on {video_path}")
        try:
            from lib.source_media_review import analyze_final_render
            return analyze_final_render(video_path)
        except ImportError:
            logger.debug("lib.source_media_review not found. Skipping QA.")
            return {"qa_passed": True, "notes": "skipped"}

    # ----- V15 Voodoo: Decision Merging -----

    def _merge_decision_log(self, manifest: JobManifest, stage: str) -> Optional[str]:
        """Merge stage decisions into a project-level decision log file."""
        decisions_for_stage = [
            d for d in (manifest.decision_trail or [])
            if str(d.get("stage", "")).strip().lower() == stage.strip().lower()
        ]
        if not decisions_for_stage:
            return ""

        path = self._decision_log_path(manifest.job_id)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        else:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("version", "1.0")
        payload.setdefault("project_id", manifest.job_id)
        payload.setdefault("decisions", [])

        existing_ids = {
            str(item.get("decision_id", ""))
            for item in payload.get("decisions", [])
            if isinstance(item, dict)
        }

        for idx, item in enumerate(decisions_for_stage):
            if not isinstance(item, dict):
                continue
            base = (
                f"{manifest.job_id}|{stage}|{item.get('timestamp', 0)}|"
                f"{item.get('label', '')}|{idx}"
            )
            decision_id = str(item.get("decision_id") or hashlib.sha1(base.encode()).hexdigest()[:16])
            if decision_id in existing_ids:
                continue
            existing_ids.add(decision_id)
            payload["decisions"].append({
                "decision_id": decision_id,
                "stage": stage,
                **item,
            })

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def is_stage_done(self, manifest: JobManifest, stage: str) -> bool:
        """Check if a stage was already completed (for idempotent re-runs)."""
        current = manifest.status
        order = JobStatus.order()
        target = f"completed_{stage}"
        try:
            current_idx = order.index(current)
            target_idx = order.index(target)
            return current_idx >= target_idx
        except ValueError:
            return False

    # ----- Idempotency -----

    def check_artifact_valid(self, path: Path, min_size: int = 1000) -> bool:
        """Check if an artifact file exists and is non-trivial."""
        return path.exists() and path.stat().st_size >= min_size

    def compute_input_hash(self, *inputs: str) -> str:
        """Compute deterministic hash of inputs for idempotency."""
        payload = "|".join(str(i) for i in inputs)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def should_skip_stage(
        self,
        manifest: JobManifest,
        stage: str,
        output_path: Optional[Path] = None,
        input_hash: str = "",
    ) -> bool:
        """Determine if a stage can be skipped.

        A stage is skippable if:
          1. The manifest shows it was already completed, AND
          2. The output artifact exists and is valid, AND
          3. The input hash matches (content hasn't changed)
        """
        if not self.is_stage_done(manifest, stage):
            return False

        if output_path and not self.check_artifact_valid(output_path):
            logger.debug(f"Stage {stage}: artifact missing/invalid, re-running")
            return False

        if input_hash and manifest.input_hash and input_hash != manifest.input_hash:
            logger.debug(f"Stage {stage}: input changed, re-running")
            return False

        logger.info(f"⏭️  Skipping stage '{stage}' — already completed with valid output")
        return True

    # ----- Job Discovery -----

    def list_resumable_jobs(self) -> list[dict]:
        """List all incomplete jobs that can be resumed."""
        jobs = []
        for f in self.temp_dir.glob("job_manifest_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                status = data.get("status", "")
                if status not in ("success", "error", "manual_review"):
                    jobs.append({
                        "job_id": data.get("job_id", ""),
                        "nicho": data.get("nicho_slug", ""),
                        "status": status,
                        "titulo": data.get("titulo", "")[:40],
                        "timestamp": data.get("timestamp", 0),
                    })
            except Exception:
                pass
        return sorted(jobs, key=lambda x: x.get("timestamp", 0), reverse=True)

    def find_latest_job(self, nicho_slug: str) -> Optional[str]:
        """Find the most recent incomplete job for a niche."""
        jobs = [j for j in self.list_resumable_jobs() if j["nicho"] == nicho_slug]
        return jobs[0]["job_id"] if jobs else None

    # ----- Cleanup -----

    def cleanup(self, job_id: str) -> None:
        """Remove manifest after successful completion."""
        path = self._manifest_path(job_id)
        if path.exists():
            path.unlink()
            logger.debug(f"Manifest cleaned: {job_id}")
        cp_dir = self._checkpoint_dir(job_id)
        if cp_dir.exists():
            import shutil
            shutil.rmtree(cp_dir, ignore_errors=True)

    def archive_manifest(self, manifest: JobManifest, output_dir: Path) -> None:
        """Move finished manifest to output dir for audit trail."""
        src = self._manifest_path(manifest.job_id)
        if not src.exists():
            self.save(manifest)
            src = self._manifest_path(manifest.job_id)
        dest = output_dir / f"job_manifest_{manifest.job_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = manifest.model_dump(mode="json")
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        src.unlink(missing_ok=True)

        # Dual-write migration support: archive stage checkpoints together.
        cp_src_dir = self._checkpoint_dir(manifest.job_id)
        if cp_src_dir.exists():
            import shutil

            cp_dest_dir = output_dir / "checkpoints" / manifest.job_id
            cp_dest_dir.mkdir(parents=True, exist_ok=True)
            for cp_file in cp_src_dir.glob("*.json"):
                shutil.copy2(str(cp_file), str(cp_dest_dir / cp_file.name))
            shutil.rmtree(cp_src_dir, ignore_errors=True)

        logger.info(f"Manifest archived: {dest.name}")

    @staticmethod
    def generate_job_id(nicho_slug: str) -> str:
        """Generate a unique job ID."""
        ts = int(time.time() * 1000)
        return f"{nicho_slug}_{ts}"
