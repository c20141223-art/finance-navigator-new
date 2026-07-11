"""Rate-limited, retrying HTTP client. Deliberately simple: a per-host
minimum interval between requests, plus fixed-backoff retries. No async, no
connection pooling tricks — the "笨但穩" option per spec section 0.

Every failure is caught and returned as a RequestOutcome rather than raised,
so a single flaky source can never crash the daily pipeline (spec section 9).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from stock_screener.config import HttpConfig

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; taiwan-stock-screener/1.0; "
        "+https://github.com/c20141223-art/finance-navigator-new)"
    )
}


@dataclass
class RequestOutcome:
    ok: bool
    status_code: int | None = None
    text: str | None = None
    content: bytes | None = None
    error: str | None = None
    attempts: int = 0


class RateLimitedClient:
    """One instance should be reused across a pipeline run so the
    per-host last-request timestamps actually throttle requests."""

    def __init__(self, config: HttpConfig, session: requests.Session | None = None):
        self._config = config
        self._session = session or requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)
        self._last_request_at: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is None:
            return
        elapsed = time.monotonic() - last
        wait = self._config.min_interval_seconds - elapsed
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, *, params: dict | None = None) -> RequestOutcome:
        host = urlparse(url).netloc
        last_error = None
        for attempt in range(1, self._config.max_retries + 1):
            self._throttle(host)
            self._last_request_at[host] = time.monotonic()
            try:
                resp = self._session.get(
                    url, params=params, timeout=self._config.timeout_seconds
                )
                if resp.status_code == 200:
                    return RequestOutcome(
                        ok=True,
                        status_code=resp.status_code,
                        text=resp.text,
                        content=resp.content,
                        attempts=attempt,
                    )
                last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "GET %s attempt %d/%d failed: %s",
                    url, attempt, self._config.max_retries, last_error,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "GET %s attempt %d/%d raised: %s",
                    url, attempt, self._config.max_retries, last_error,
                )

            if attempt < self._config.max_retries:
                time.sleep(self._config.retry_backoff_seconds * attempt)

        return RequestOutcome(ok=False, error=last_error, attempts=self._config.max_retries)
