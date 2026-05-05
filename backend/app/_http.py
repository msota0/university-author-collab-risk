"""
Tiny HTTP helper used by the enrichment scripts.

Goals:
  - never burst past a per-host rate ceiling (RateLimiter)
  - retry transient failures with exponential backoff
  - honour the Retry-After header on 429s
  - keep the call sites readable

This is deliberately minimal — no aiohttp, no async — because the enrichment
scripts run as one-shot CLIs, not in the request path.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import requests


class RateLimiter:
    """Thread-safe minimum-interval gate.

    `wait()` blocks until at least `min_interval_seconds` has elapsed since
    the last call. Use one instance per upstream host.
    """

    def __init__(self, min_interval_seconds: float):
        self.min_interval = float(min_interval_seconds)
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


def polite_request(
    method: str,
    url: str,
    *,
    limiter: Optional[RateLimiter] = None,
    max_retries: int = 5,
    timeout: float = 60,
    backoff_cap_seconds: float = 60,
    **kwargs,
) -> requests.Response:
    """Issue an HTTP request, gated by `limiter` and retrying on 429 / 5xx.

    Returns the final Response. The caller is responsible for raising on
    non-2xx if it cares — this helper does NOT call raise_for_status, because
    Scopus / Dimensions sometimes use 4xx as legitimate "no result" signals.
    """
    last_exc: Optional[BaseException] = None
    response: Optional[requests.Response] = None
    for attempt in range(max_retries):
        if limiter:
            limiter.wait()
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as e:
            last_exc = e
            time.sleep(min(2 ** attempt, backoff_cap_seconds))
            continue

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else (2 ** attempt)
            except ValueError:
                wait = 2 ** attempt
            time.sleep(min(wait, backoff_cap_seconds))
            continue
        if 500 <= response.status_code < 600:
            time.sleep(min(2 ** attempt, backoff_cap_seconds))
            continue
        return response

    if response is not None:
        return response
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("polite_request exhausted retries without a response")
