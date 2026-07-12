"""Monthly revenue fetcher.

The original MOPS static-file URLs (mops.twse.com.tw/nas/t21/...) returned
HTTP 404 in round-two verification — that publication path is gone. Both
exchanges now publish the same aggregate monthly-revenue report through
the openapi domains we have PROVEN reachable from GitHub Actions:

- 上市: https://openapi.twse.com.tw/v1/opendata/t187ap05_L
- 上櫃: https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O

Both return a flat JSON array with identical Chinese property names
(confirmed against both sites' swagger catalogs in docs/api_samples/):
出表日期, 資料年月, 公司代號, 公司名稱, 產業別, 營業收入-當月營收,
營業收入-上月比較增減(%), 營業收入-去年同月增減(%),
累計營業收入-前期比較增減(%), etc. 資料年月 is ROC "YYYMM".

These endpoints serve the LATEST available month (no date parameter),
which matches the spec's "篩選使用最新可得資料，並標注資料月份" — the
per-row 資料年月 is what gets stored, not the fetch date. Schema-verified
only; no live sample captured yet.
"""

from __future__ import annotations

import datetime as dt
import json

from stock_screener.config import SourcesConfig
from stock_screener.fetchers.common import to_float, to_int
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError

TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.twse.com.tw/",
}
TPEX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.tpex.org.tw/",
}


def fetch_monthly_revenue_raw(
    client: RateLimitedClient, config: SourcesConfig, market: str
) -> RequestOutcome:
    """market: 'sii' (上市) or 'otc' (上櫃). Returns the latest published
    month — the endpoints take no date parameter."""
    url = config.url(f"monthly_revenue_{market}")
    headers = TWSE_HEADERS if market == "sii" else TPEX_HEADERS
    return client.get(url, headers=headers)


def _roc_year_month_to_iso(raw: str) -> str | None:
    """'11506' (ROC YYYMM) -> '2026-06'. None if unrecognizable."""
    cleaned = str(raw).strip().replace("/", "")
    if not cleaned.isdigit() or len(cleaned) not in (4, 5):
        return None
    year, month = int(cleaned[:-2]) + 1911, int(cleaned[-2:])
    if not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def parse_monthly_revenue(raw_text: str, source: str) -> list[dict]:
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(source, expected={"<list>"}, actual={type(payload).__name__})

    required = {"資料年月", "公司代號", "營業收入-當月營收", "營業收入-去年同月增減(%)"}
    if payload:
        actual = set(payload[0].keys())
        if not required.issubset(actual):
            raise SchemaMismatchError(source, expected=required, actual=actual)

    rows = []
    for rec in payload:
        stock_id = str(rec["公司代號"]).strip()
        year_month = _roc_year_month_to_iso(rec["資料年月"])
        if not stock_id or not stock_id.isdigit() or year_month is None:
            continue
        rows.append({
            "stock_id": stock_id,
            "year_month": year_month,
            "revenue": to_int(rec.get("營業收入-當月營收")),
            "yoy": to_float(rec.get("營業收入-去年同月增減(%)")),
            "mom": to_float(rec.get("營業收入-上月比較增減(%)")),
            "cumulative_yoy": to_float(rec.get("累計營業收入-前期比較增減(%)")),
            "announced_date": None,
        })
    return rows
