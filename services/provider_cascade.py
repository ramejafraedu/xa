"""Video Factory V16 PRO — Scored Provider Cascade.

Generic fallback engine for any external service (TTS, music, LLM, video stock).
Tries providers in descending score order, tracks health, and auto-cools-down
failing providers so the 3:00 AM scheduler never hangs.

Usage:
    cascade = ProviderCascade("tts")
    cascade.register("gemini", tier="freemium", callable_fn=_gemini_tts, base_score=90)
    cascade.register("edge-tts", tier="free", callable_fn=_edge_tts, base_score=70)
    result = cascade.execute(text=text, output=audio_path)  # tries best-scored first

Module Contract:
    - Thread-safe via Lock
    - Persists scores to JSON (survives process restart)
    - RAM overhead: negligible (~2KB per cascade instance)
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger


@dataclass
class ProviderEntry:
    """Runtime state for a single provider in the cascade."""

    name: str
    tier: str  # "free" | "freemium" | "premium"
    callable_fn: Callable[..., Any]
    base_score: float = 75.0

    # Dynamic state (updated at runtime)
    score: float = 75.0
    total_calls: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    last_latency_ms: int = 0
    cooldown_until: float = 0.0

    # Config
    enabled: bool = True


@dataclass
class CascadeResult:
    """Result of a cascade execution."""

    success: bool
    provider_name: str = ""
    result: Any = None
    error: str = ""
    attempts: list[dict] = field(default_factory=list)
    total_latency_ms: int = 0


class ProviderCascade:
    """Scored provider selector with automatic health tracking.

    Providers are tried in descending score order. Scores adjust dynamically
    based on success/failure/latency. Failing providers enter cooldown.
    """

    def __init__(
        self,
        name: str,
        state_dir: Optional[Path] = None,
        cooldown_seconds: int = 1800,
        max_consecutive_failures: int = 5,
        score_decay_on_failure: float = 8.0,
        score_boost_on_success: float = 3.0,
    ):
        self.name = name
        self._providers: dict[str, ProviderEntry] = {}
        self._lock = threading.Lock()
        self._cooldown_seconds = cooldown_seconds
        self._max_consecutive_failures = max_consecutive_failures
        self._score_decay = score_decay_on_failure
        self._score_boost = score_boost_on_success

        # Persistent state
        self._state_dir = state_dir
        if state_dir:
            self._state_file = state_dir / f"provider_cascade_{name}.json"
        else:
            self._state_file = None

        self._load_state()

    # ── Registration ─────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        callable_fn: Callable[..., Any],
        tier: str = "free",
        base_score: float = 75.0,
        enabled: bool = True,
    ) -> None:
        """Register a provider in the cascade.

        Args:
            name: Unique provider identifier.
            callable_fn: Function to call. Must return a truthy value on success,
                         or raise an exception / return falsy on failure.
            tier: "free" | "freemium" | "premium"
            base_score: Initial score (0-100). Higher = tried first.
            enabled: Whether this provider is currently enabled.
        """
        with self._lock:
            if name in self._providers:
                # Update callable but preserve runtime state
                existing = self._providers[name]
                existing.callable_fn = callable_fn
                existing.enabled = enabled
                existing.tier = tier
                return

            entry = ProviderEntry(
                name=name,
                tier=tier,
                callable_fn=callable_fn,
                base_score=base_score,
                score=base_score,
                enabled=enabled,
            )

            # Restore persisted state if available
            saved = self._saved_state.get(name)
            if saved:
                entry.score = float(saved.get("score", base_score))
                entry.total_calls = int(saved.get("total_calls", 0))
                entry.total_successes = int(saved.get("total_successes", 0))
                entry.total_failures = int(saved.get("total_failures", 0))
                entry.consecutive_failures = int(saved.get("consecutive_failures", 0))
                entry.last_success_ts = float(saved.get("last_success_ts", 0.0))
                entry.last_failure_ts = float(saved.get("last_failure_ts", 0.0))

            self._providers[name] = entry

    # ── Execution ────────────────────────────────────────────────────────

    def execute(self, provider_order: Optional[list[str]] = None, **kwargs: Any) -> CascadeResult:
        """Execute the cascade: try providers in score order until one succeeds.

        All keyword arguments are forwarded to each provider's callable_fn.

        Returns:
            CascadeResult with success status, provider name, and result.
        """
        ordered = self._get_ordered_providers(provider_order=provider_order)
        if not ordered:
            return CascadeResult(
                success=False,
                error=f"No providers registered for cascade '{self.name}'",
            )

        attempts: list[dict] = []
        total_start = time.time()

        for entry in ordered:
            if self._is_cooling_down(entry):
                attempts.append({
                    "provider": entry.name,
                    "status": "skipped_cooldown",
                    "cooldown_remaining_s": round(entry.cooldown_until - time.time(), 1),
                })
                continue

            t0 = time.time()
            try:
                result = entry.callable_fn(**kwargs)
                latency_ms = int(round((time.time() - t0) * 1000))

                if result:
                    self._record_success(entry, latency_ms)
                    attempts.append({
                        "provider": entry.name,
                        "status": "success",
                        "latency_ms": latency_ms,
                    })
                    self._persist_state()

                    return CascadeResult(
                        success=True,
                        provider_name=entry.name,
                        result=result,
                        attempts=attempts,
                        total_latency_ms=int((time.time() - total_start) * 1000),
                    )

                # Falsy return = soft failure
                latency_ms = int(round((time.time() - t0) * 1000))
                self._record_failure(entry, "returned falsy", latency_ms)
                attempts.append({
                    "provider": entry.name,
                    "status": "failed_falsy",
                    "latency_ms": latency_ms,
                })

            except Exception as exc:
                latency_ms = int(round((time.time() - t0) * 1000))
                error_msg = str(exc)[:200]
                self._record_failure(entry, error_msg, latency_ms)
                attempts.append({
                    "provider": entry.name,
                    "status": "failed_exception",
                    "error": error_msg,
                    "latency_ms": latency_ms,
                })

        self._persist_state()
        all_errors = "; ".join(
            f"{a['provider']}={a['status']}"
            for a in attempts
        )
        return CascadeResult(
            success=False,
            error=f"All providers failed for '{self.name}': {all_errors}",
            attempts=attempts,
            total_latency_ms=int((time.time() - total_start) * 1000),
        )

    # ── Scoring ──────────────────────────────────────────────────────────

    def _get_ordered_providers(self, provider_order: Optional[list[str]] = None) -> list[ProviderEntry]:
        """Return enabled providers sorted by preferred order or score."""
        with self._lock:
            active = [p for p in self._providers.values() if p.enabled]

        if provider_order:
            index = {
                str(name).strip().lower(): idx
                for idx, name in enumerate(provider_order)
                if str(name).strip()
            }

            def _sort_key(entry: ProviderEntry) -> tuple[int, float]:
                explicit = index.get(entry.name.lower(), 10_000)
                return (explicit, -entry.score)

            return sorted(active, key=_sort_key)

        return sorted(active, key=lambda p: p.score, reverse=True)

    def _is_cooling_down(self, entry: ProviderEntry) -> bool:
        return time.time() < entry.cooldown_until

    def _record_success(self, entry: ProviderEntry, latency_ms: int) -> None:
        with self._lock:
            entry.total_calls += 1
            entry.total_successes += 1
            entry.consecutive_failures = 0
            entry.last_success_ts = time.time()
            entry.last_latency_ms = latency_ms
            entry.cooldown_until = 0.0

            # Score boost (capped at 100)
            entry.score = min(100.0, entry.score + self._score_boost)

            # Latency penalty for slow providers (> 10s)
            if latency_ms > 10_000:
                latency_penalty = min(5.0, (latency_ms - 10_000) / 5_000)
                entry.score = max(10.0, entry.score - latency_penalty)

            logger.debug(
                f"Cascade '{self.name}': {entry.name} ✅ "
                f"(score={entry.score:.1f}, {latency_ms}ms)"
            )

    def _record_failure(self, entry: ProviderEntry, error: str, latency_ms: int) -> None:
        with self._lock:
            entry.total_calls += 1
            entry.total_failures += 1
            entry.consecutive_failures += 1
            entry.last_failure_ts = time.time()
            entry.last_latency_ms = latency_ms

            # Score decay (floor at 5)
            entry.score = max(5.0, entry.score - self._score_decay)

            # Auto-cooldown after too many consecutive failures
            if entry.consecutive_failures >= self._max_consecutive_failures:
                entry.cooldown_until = time.time() + self._cooldown_seconds
                logger.warning(
                    f"Cascade '{self.name}': {entry.name} entered cooldown "
                    f"({self._cooldown_seconds}s) after {entry.consecutive_failures} "
                    f"consecutive failures. Last error: {error[:120]}"
                )
            else:
                logger.debug(
                    f"Cascade '{self.name}': {entry.name} ❌ "
                    f"(score={entry.score:.1f}, failures={entry.consecutive_failures})"
                )

    # ── Persistence ──────────────────────────────────────────────────────

    _saved_state: dict = {}

    def _load_state(self) -> None:
        """Load persisted provider scores from disk."""
        if not self._state_file or not self._state_file.exists():
            self._saved_state = {}
            return
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._saved_state = data.get("providers", {})
        except Exception:
            self._saved_state = {}

    def _persist_state(self) -> None:
        """Save current provider scores to disk."""
        if not self._state_file:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "cascade": self.name,
                "updated_at": int(time.time()),
                "providers": {},
            }
            with self._lock:
                for name, entry in self._providers.items():
                    data["providers"][name] = {
                        "score": round(entry.score, 2),
                        "tier": entry.tier,
                        "total_calls": entry.total_calls,
                        "total_successes": entry.total_successes,
                        "total_failures": entry.total_failures,
                        "consecutive_failures": entry.consecutive_failures,
                        "last_success_ts": entry.last_success_ts,
                        "last_failure_ts": entry.last_failure_ts,
                        "last_latency_ms": entry.last_latency_ms,
                        "cooldown_until": entry.cooldown_until,
                    }
            self._state_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(f"Provider cascade state save failed: {exc}")

    # ── Introspection ────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return a summary dict suitable for dashboard display."""
        with self._lock:
            providers = []
            for entry in sorted(
                self._providers.values(), key=lambda p: p.score, reverse=True
            ):
                success_rate = (
                    round(entry.total_successes / max(1, entry.total_calls) * 100, 1)
                )
                providers.append({
                    "name": entry.name,
                    "tier": entry.tier,
                    "score": round(entry.score, 1),
                    "enabled": entry.enabled,
                    "success_rate": success_rate,
                    "total_calls": entry.total_calls,
                    "consecutive_failures": entry.consecutive_failures,
                    "cooling_down": self._is_cooling_down(entry),
                    "last_latency_ms": entry.last_latency_ms,
                })
            return {
                "cascade": self.name,
                "providers": providers,
            }

    def reset_provider(self, name: str) -> bool:
        """Reset a provider's score and cooldown (manual intervention)."""
        with self._lock:
            entry = self._providers.get(name)
            if not entry:
                return False
            entry.score = entry.base_score
            entry.consecutive_failures = 0
            entry.cooldown_until = 0.0
            logger.info(f"Cascade '{self.name}': {name} manually reset to score={entry.base_score}")
        self._persist_state()
        return True
