"""Cost governance helpers for staged budget control in V15/V16 rollout."""
from __future__ import annotations

import json
from datetime import date

from loguru import logger

from config import settings
from models.content import JobManifest


class CostGovernance:
    """Estimate/reserve/reconcile stage costs with optional daily budget caps."""

    def __init__(self, manifest: JobManifest):
        self.manifest = manifest
        settings.ensure_dirs()
        self.manifest.budget_daily_usd = float(settings.daily_budget_usd)

    def reserve_stage(self, stage: str, provider: str = "") -> tuple[bool, str, float]:
        """Reserve a stage budget if governance is enabled.

        Returns (allowed, reason, estimated_cost).
        """
        estimated = round(settings.stage_estimated_cost_usd(stage), 4)
        self.manifest.cost_estimate_usd = round(self.manifest.cost_estimate_usd + estimated, 4)

        if not settings.enable_cost_governance:
            return True, "", estimated

        if provider and not settings.provider_allowed(provider):
            self.manifest.budget_blocked = True
            reason = (
                f"Provider '{provider}' blocked by provider policy "
                f"(mode={settings.execution_mode_label()})"
            )
            return False, reason, estimated

        if estimated <= 0:
            return True, "", estimated

        daily_budget = float(settings.daily_budget_usd)
        daily_spend = self.get_today_spend_usd()

        # Budget <= 0 with governance enabled means "track only", not hard-stop.
        if daily_budget > 0:
            projected = round(daily_spend + self.manifest.cost_actual_usd + estimated, 4)
            if projected > daily_budget:
                self.manifest.budget_blocked = True
                reason = (
                    f"Daily budget exceeded: projected ${projected:.4f} > "
                    f"limit ${daily_budget:.4f}"
                )
                return False, reason, estimated

        self.manifest.cost_reserved_usd = round(self.manifest.cost_reserved_usd + estimated, 4)
        return True, "", estimated

    def record_stage_actual(self, stage: str, actual_usd: float | None = None) -> float:
        """Record actual stage spend and persist to daily spend tracker."""
        if actual_usd is None:
            actual_usd = settings.stage_estimated_cost_usd(stage)

        actual = round(max(0.0, float(actual_usd)), 4)
        if actual <= 0:
            return 0.0

        self.manifest.cost_actual_usd = round(self.manifest.cost_actual_usd + actual, 4)
        self.manifest.cost_breakdown[stage] = round(
            self.manifest.cost_breakdown.get(stage, 0.0) + actual,
            4,
        )

        if settings.enable_cost_governance:
            state = self._read_budget_state()
            today = date.today().isoformat()
            state[today] = round(float(state.get(today, 0.0)) + actual, 4)
            self._write_budget_state(state)

        return actual

    def get_today_spend_usd(self) -> float:
        state = self._read_budget_state()
        return float(state.get(date.today().isoformat(), 0.0))

    def _read_budget_state(self) -> dict[str, float]:
        path = settings.budget_state_path
        if not path.exists():
            return {}

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception as exc:
            logger.debug(f"Cost governance: failed reading budget state: {exc}")

        return {}

    def _write_budget_state(self, data: dict[str, float]) -> None:
        path = settings.budget_state_path
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"Cost governance: failed writing budget state: {exc}")
