"""HTTP client with retry, backoff, rate-limit handling, and circuit breaker.

Single httpx-based client used throughout the entire pipeline.
No mixing of requests/aiohttp/urllib — httpx everywhere.

Features:
  - Connection pooling (max 10 concurrent)
  - Exponential backoff with jitter
  - 429 rate-limit handling (Retry-After header)
  - Circuit breaker: after 3 consecutive failures to a domain,
    short-circuit for 60s to avoid hammering a dead API
  - Reusable ``with_api_retry`` decorator for high-level functions
  - Secrets never logged (Authorization headers masked)

MODULE CONTRACT:
  Input:  URL + method + optional body/headers
  Output: httpx.Response or raised exception after retries
"""
from __future__ import annotations

import functools
import random
import time
import threading
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

import httpx
from loguru import logger


T = TypeVar("T")


def with_api_retry(
    tries: int = 3,
    delay: float = 2.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
    label: str = "",
) -> Callable:
    """Decorator: retry a function with exponential backoff + jitter.

    Usage::

        @with_api_retry(tries=3, delay=2, backoff=2, label="Supabase")
        def save_to_supabase(...):
            ...
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            fn_label = label or fn.__name__
            last_exc: Exception | None = None
            current_delay = delay
            for attempt in range(1, tries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == tries:
                        break
                    jitter = random.uniform(0, current_delay * 0.3)
                    wait = current_delay + jitter
                    logger.warning(
                        f"{fn_label} attempt {attempt}/{tries} failed: {exc}. "
                        f"Retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    current_delay *= backoff
            logger.error(f"{fn_label} failed after {tries} attempts: {last_exc}")
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker — per-domain failure tracking
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Simple circuit breaker: if a domain fails N times in a row, open the
    circuit for `cooldown_seconds` to avoid hammering a dead API."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _domain(self, url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url[:40]

    def is_open(self, url: str) -> bool:
        domain = self._domain(url)
        with self._lock:
            deadline = self._open_until.get(domain, 0)
            if time.time() < deadline:
                return True
            if deadline > 0 and time.time() >= deadline:
                # Circuit half-open — allow one retry
                self._open_until.pop(domain, None)
                self._failures[domain] = 0
            return False

    def record_success(self, url: str) -> None:
        domain = self._domain(url)
        with self._lock:
            self._failures[domain] = 0
            self._open_until.pop(domain, None)

    def record_failure(self, url: str) -> None:
        domain = self._domain(url)
        with self._lock:
            self._failures[domain] = self._failures.get(domain, 0) + 1
            if self._failures[domain] >= self._threshold:
                self._open_until[domain] = time.time() + self._cooldown
                logger.warning(
                    f"🔴 Circuit OPEN for {domain} — "
                    f"{self._failures[domain]} consecutive failures, "
                    f"pausing for {self._cooldown}s"
                )


# Singleton circuit breaker
_breaker = CircuitBreaker()

# Shared client with connection pooling
_client: Optional[httpx.Client] = None


def get_client() -> httpx.Client:
    """Get or create the shared httpx client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


def close_client() -> None:
    """Close the shared client."""
    global _client
    if _client and not _client.is_closed:
        _client.close()
        _client = None


def _safe_log_url(url: str) -> str:
    """Mask API keys in URLs for safe logging."""
    import re
    masked = re.sub(r"(key=)[^&]+", r"\1***", url, flags=re.IGNORECASE)
    masked = re.sub(r"(token=)[^&]+", r"\1***", masked, flags=re.IGNORECASE)
    return masked[:120]


def request_with_retry(
    method: str,
    url: str,
    *,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    headers: Optional[dict] = None,
    json_data: Optional[dict] = None,
    data: Optional[Any] = None,
    params: Optional[dict] = None,
    timeout: float = 60.0,
) -> httpx.Response:
    """Make an HTTP request with exponential backoff retry.

    Handles 429 (rate limit), 5xx (server error), connection errors,
    and checks circuit breaker before each attempt.
    """
    max_retry_after_seconds = 20

    # Circuit breaker check
    if _breaker.is_open(url):
        raise httpx.ConnectError(
            f"Circuit breaker OPEN for {_safe_log_url(url)} — API temporarily disabled"
        )

    client = get_client()
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            response = client.request(
                method,
                url,
                headers=headers,
                json=json_data,
                content=data,
                params=params,
                timeout=timeout,
            )

            # Success
            if response.status_code < 400:
                _breaker.record_success(url)
                return response

            # Rate limited — wait and retry
            if response.status_code == 429:
                header_value = str(response.headers.get("Retry-After", "")).strip()
                try:
                    retry_after = int(float(header_value)) if header_value else int(backoff_base ** attempt)
                except ValueError:
                    retry_after = int(backoff_base ** attempt)

                wait_seconds = max(1, min(retry_after, max_retry_after_seconds))
                if retry_after > wait_seconds:
                    logger.warning(
                        f"Rate limited (429). Retry-After={retry_after}s capped to {wait_seconds}s. "
                        f"[{attempt}/{max_retries}]"
                    )
                else:
                    logger.warning(
                        f"Rate limited (429). Waiting {wait_seconds}s. [{attempt}/{max_retries}]"
                    )
                _breaker.record_failure(url)
                time.sleep(wait_seconds)
                continue

            # Server error — retry with backoff
            if response.status_code >= 500:
                wait = backoff_base ** attempt + random.uniform(0, 1)
                logger.warning(f"Server error {response.status_code}. Waiting {wait:.1f}s. [{attempt}/{max_retries}]")
                _breaker.record_failure(url)
                time.sleep(wait)
                continue

            # Client error (4xx except 429) — don't retry
            logger.error(f"Client error {response.status_code}: {_safe_log_url(url)}")
            _breaker.record_failure(url)
            return response

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
            last_error = e
            wait = backoff_base ** attempt + random.uniform(0, 1)
            logger.warning(f"Connection error: {e}. Waiting {wait:.1f}s. [{attempt}/{max_retries}]")
            _breaker.record_failure(url)
            time.sleep(wait)

    # All retries exhausted
    if last_error:
        raise last_error
    return response  # type: ignore


def download_file(url: str, dest: Path, *, max_retries: int = 3, timeout: float = 90.0) -> bool:
    """Download a file to disk. Returns True if successful."""
    if _breaker.is_open(url):
        logger.debug(f"Download skipped — circuit open for {_safe_log_url(url)}")
        return False

    try:
        client = get_client()
        with client.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
            if response.status_code >= 400:
                logger.warning(f"Download failed ({response.status_code}): {_safe_log_url(url)}")
                _breaker.record_failure(url)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        if dest.stat().st_size < 1000:
            logger.warning(f"Downloaded file too small ({dest.stat().st_size}B): {dest.name}")
            dest.unlink(missing_ok=True)
            return False
        _breaker.record_success(url)
        return True
    except Exception as e:
        logger.warning(f"Download error: {_safe_log_url(url)}: {e}")
        _breaker.record_failure(url)
        dest.unlink(missing_ok=True)
        return False


def post_json(
    url: str,
    json_data: dict,
    *,
    headers: Optional[dict] = None,
    max_retries: int = 3,
) -> dict:
    """POST JSON and return parsed response. Raises on failure."""
    response = request_with_retry(
        "POST", url,
        json_data=json_data,
        headers=headers,
        max_retries=max_retries,
    )
    response.raise_for_status()
    return response.json()


def get_json(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    max_retries: int = 3,
) -> dict:
    """GET and return parsed JSON."""
    response = request_with_retry(
        "GET", url,
        headers=headers,
        params=params,
        max_retries=max_retries,
    )
    response.raise_for_status()
    return response.json()
