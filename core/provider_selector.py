"""Provider selection with lightweight scoring and health-based ordering."""
from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

from config import settings


class ProviderSelector:
    """Ranks providers by policy + static quality + recent health."""

    BASE_SCORES = {
        "stock_video": {
            "pexels": 0.92,
            "pixabay": 0.80,
            "coverr": 0.64,
        },
        "image_generation": {
            "google_imagen": 1.95,
            "pexels": 0.93,
            "pixabay": 0.86,
            "leonardo": 0.90,
            "pollinations": 0.72,
        },
        "music_generation": {
            "suno": 0.90,
            "lyria": 0.88,
            "pixabay": 0.76,
            "jamendo": 0.68,
        },
    }

    def __init__(self) -> None:
        settings.ensure_dirs()
        self._state_path = settings.temp_dir / "provider_health.json"
        self._state = self._read_state()

    def get_provider_order(self, resource_type: str, candidates: list[str]) -> list[str]:
        """Return providers sorted by policy and health score."""
        scored: list[tuple[float, str]] = []
        for provider in candidates:
            score, allowed = self._score_provider(resource_type, provider)
            if allowed:
                scored.append((score, provider))

        if not scored:
            # Hard fallback: return only providers allowed by current policy.
            allowed_candidates = [p for p in candidates if settings.provider_allowed(p)]
            return allowed_candidates or candidates

        scored.sort(key=lambda x: x[0], reverse=True)
        order = [provider for _, provider in scored]

        logger.debug(
            f"Provider order [{resource_type}]: "
            + ", ".join(f"{provider}={score:.2f}" for score, provider in scored)
        )
        return order

    def mark_result(self, resource_type: str, provider: str, success: bool, error: str = "") -> None:
        """Update provider health state with latest execution result."""
        pstate = self._provider_state(resource_type, provider)
        if success:
            pstate["success"] = int(pstate.get("success", 0)) + 1
            pstate["consecutive_failures"] = 0
        else:
            pstate["failure"] = int(pstate.get("failure", 0)) + 1
            pstate["consecutive_failures"] = int(pstate.get("consecutive_failures", 0)) + 1
            if error:
                pstate["last_error"] = error[:300]

        pstate["updated_at"] = int(time.time())
        self._write_state()

    def _score_provider(self, resource_type: str, provider: str) -> tuple[float, bool]:
        base = self.BASE_SCORES.get(resource_type, {}).get(provider, 0.50)
        allowed = settings.provider_allowed(provider)
        if not allowed:
            return -10.0, False

        pstate = self._provider_state(resource_type, provider)
        success = float(pstate.get("success", 0))
        failure = float(pstate.get("failure", 0))
        attempts = success + failure

        if attempts > 0:
            success_rate = success / attempts
            health_bonus = (success_rate - 0.5) * 0.30
        else:
            health_bonus = 0.0

        consecutive_failures = int(pstate.get("consecutive_failures", 0))
        penalty = min(consecutive_failures * 0.08, 0.32)

        score = base + health_bonus - penalty
        return score, True

    def _provider_state(self, resource_type: str, provider: str) -> dict:
        if resource_type not in self._state:
            self._state[resource_type] = {}
        if provider not in self._state[resource_type]:
            self._state[resource_type][provider] = {
                "success": 0,
                "failure": 0,
                "consecutive_failures": 0,
                "last_error": "",
                "updated_at": 0,
            }
        return self._state[resource_type][provider]

    def _read_state(self) -> dict:
        if not self._state_path.exists():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.debug(f"Provider selector: failed reading state: {exc}")
        return {}

    def _write_state(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(f"Provider selector: failed writing state: {exc}")
