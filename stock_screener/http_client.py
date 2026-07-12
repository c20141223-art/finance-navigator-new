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

# 2026-07-11 第一輪真實驗證發現：openapi.twse.com.tw / www.twse.com.tw 的部分
# 端點會擋下帶「自我標示為機器人」UA 的請求（UA 字串內含 "+https://github..."
# 這種慣例上用來自我介紹的 bot 格式），回傳攔截頁或無 Location 的 307。
# 已驗證能穩定打通同一批端點的設定（來自 c20141223-art/stock-report，該專案
# 用同一種請求方式在 GitHub Actions 上穩定運行數月）：一般瀏覽器格式的
# User-Agent（不帶自我識別 URL）＋ Referer 指到來源網站本身。因此預設值改為
# 這個較保守的格式；不同來源如需更精確的 Referer／no-cache 標頭，由呼叫端
# 透過 `get(..., headers=...)` 覆寫。
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)",
    "Accept": "application/json",
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

    def get(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> RequestOutcome:
        """`headers` are merged over the session defaults for this request
        only — used by fetchers to add per-source Referer / no-cache
        headers without leaking them onto other hosts."""
        host = urlparse(url).netloc
        last_error = None
        for attempt in range(1, self._config.max_retries + 1):
            self._throttle(host)
            self._last_request_at[host] = time.monotonic()
            try:
                resp = self._session.get(
                    url, params=params, headers=headers,
                    timeout=self._config.timeout_seconds,
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
