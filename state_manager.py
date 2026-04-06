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
from pathlib import Path
from typing import Optional

from loguru import logger

from models.content import JobManifest, JobStatus


class StateManager:
    """Manages pipeline state persistence via job manifest files."""

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self, job_id: str) -> Path:
        return self.temp_dir / f"job_manifest_{job_id}.json"

    # ----- Save / Load -----

    def save(self, manifest: JobManifest) -> None:
        """Persist current job manifest."""
        path = self._manifest_path(manifest.job_id)
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
        self.save(manifest)

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
        logger.info(f"Manifest archived: {dest.name}")

    @staticmethod
    def generate_job_id(nicho_slug: str) -> str:
        """Generate a unique job ID."""
        ts = int(time.time() * 1000)
        return f"{nicho_slug}_{ts}"
