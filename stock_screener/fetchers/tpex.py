"""TPEx (上櫃) fetchers.

Verified 2026-07-11 against live responses captured by
scripts/verify_api_samples.py (see docs/api_samples/README.md for the
verification log). Confirmed so far:

- `tpex_daily_all` (openapi mainboard daily close quotes): flat JSON array,
  field names differ from TWSE's openapi convention — see `parse_daily_all`.
- `tpex_daily_history`: NOT the legacy `{"aaData": [[...]]}` shape we'd
  guessed. TPEx's site relaunched in Oct 2024 and the report now comes back
  as `{"tables": [{"fields": [...], "data": [...]}], "stat": "ok", ...}` —
  structurally like TWSE's fields/data pairs, just wrapped in a "tables"
  list instead of numbered keys. Column ORDER is still not to be trusted
  positionally (same class of risk as before), so we look columns up by
  keyword via `find_column`, same as the TWSE fetchers do.
  NOTE: the captured sample had `data: []` (zero rows) for the probed date,
  so the field *names* are confirmed but no actual data row has been
  checked yet — re-verify against a response with rows before fully
  trusting the value-level parsing.

Still UNVERIFIED / broken as of the same run — see docs/api_samples/README.md:
- `tpex_institutional`: the openapi.../tpex_3insti_daily_trade path resolved
  to the TPEx homepage template, not JSON. The correct openapi endpoint name
  for this report is not yet confirmed.
"""

from __future__ import annotations

import datetime as dt
import json

from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import to_roc_date
from stock_screener.fetchers.common import find_column, to_float, to_int
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError

# Same shape as the header profile proven to work against TWSE from GitHub
# Actions (see fetchers/twse.py docstring), with the Referer pointed at
# TPEx's own site.
REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.tpex.org.tw/",
}


def fetch_daily_all_raw(client: RateLimitedClient, config: SourcesConfig) -> RequestOutcome:
    return client.get(config.url("tpex_daily_all"), headers=REQ_HEADERS)


def fetch_daily_history_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    url = config.url("tpex_daily_history").format(roc_date=to_roc_date(date))
    return client.get(url, headers=REQ_HEADERS)


def parse_daily_all(raw_text: str) -> list[dict]:
    """Confirmed field names from a live sample (docs/api_samples/tpex_daily_all.json):
    SecuritiesCompanyCode, CompanyName, Close, Open, High, Low, TradingShares,
    TransactionAmount (NOT Code/Name/TradingVolume as originally guessed)."""
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(
            "tpex_daily_all", expected={"<list>"}, actual={type(payload).__name__}
        )
    required = {
        "SecuritiesCompanyCode", "CompanyName", "Close", "Open", "High", "Low",
        "TradingShares", "TransactionAmount",
    }
    if payload:
        actual = set(payload[0].keys())
        if not required.issubset(actual):
            raise SchemaMismatchError("tpex_daily_all", expected=required, actual=actual)

    rows = []
    for rec in payload:
        rows.append({
            "stock_id": rec["SecuritiesCompanyCode"],
            "name": rec.get("CompanyName"),
            "open": to_float(rec.get("Open")),
            "high": to_float(rec.get("High")),
            "low": to_float(rec.get("Low")),
            "close": to_float(rec.get("Close")),
            "volume": to_int(rec.get("TradingShares")),
            "turnover": to_int(rec.get("TransactionAmount")),
        })
    return rows


def parse_daily_history(raw_text: str, date: dt.date) -> list[dict]:
    """See module docstring: confirmed 'tables' shape, unconfirmed row values."""
    payload = json.loads(raw_text)
    tables = payload.get("tables")
    if not tables:
        return []

    fields = None
    data = None
    for table in tables:
        candidate_fields = table.get("fields") or []
        if any("代號" in f for f in candidate_fields) and any("收盤" in f for f in candidate_fields):
            fields = candidate_fields
            data = table.get("data") or []
            break

    if fields is None:
        raise SchemaMismatchError(
            "tpex_daily_history",
            expected={"一個同時含 代號 與 收盤 欄位的 table"},
            actual={str(t.get("fields")) for t in tables},
        )
    if not data:
        return []

    idx_id = find_column(fields, ("代號",), source="tpex_daily_history")
    idx_close = find_column(fields, ("收盤",), source="tpex_daily_history")
    idx_open = find_column(fields, ("開盤",), source="tpex_daily_history")
    idx_high = find_column(fields, ("最高",), source="tpex_daily_history")
    idx_low = find_column(fields, ("最低",), source="tpex_daily_history")
    idx_volume = find_column(fields, ("成交股數",), source="tpex_daily_history")
    idx_turnover = find_column(fields, ("成交金額",), source="tpex_daily_history")

    rows = []
    for row in data:
        stock_id = str(row[idx_id]).strip()
        if not stock_id:
            continue
        rows.append({
            "stock_id": stock_id,
            "date": date.isoformat(),
            "open": to_float(row[idx_open]),
            "high": to_float(row[idx_high]),
            "low": to_float(row[idx_low]),
            "close": to_float(row[idx_close]),
            "volume": to_int(row[idx_volume]),
            "turnover": to_int(row[idx_turnover]),
        })
    return rows


def fetch_institutional_raw(client: RateLimitedClient, config: SourcesConfig) -> RequestOutcome:
    return client.get(config.url("tpex_institutional"), headers=REQ_HEADERS)


def parse_institutional(raw_text: str, date: dt.date) -> list[dict]:
    """UNVERIFIED shape — assumed flat JSON array like tpex_daily_all.
    The live-verification run found the configured URL resolves to the
    TPEx homepage, not this report, so the endpoint path itself is still
    wrong. See docs/api_samples/README.md."""
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(
            "tpex_institutional", expected={"<list>"}, actual={type(payload).__name__}
        )
    required = {"Code", "ForeignInvestorsNetBuySell", "SecuritiesInvestorsNetBuySell", "DealersNetBuySell"}
    if payload:
        actual = set(payload[0].keys())
        if not required.issubset(actual):
            raise SchemaMismatchError("tpex_institutional", expected=required, actual=actual)

    rows = []
    for rec in payload:
        rows.append({
            "stock_id": rec["Code"],
            "date": date.isoformat(),
            "foreign_net": to_int(rec.get("ForeignInvestorsNetBuySell")),
            "trust_net": to_int(rec.get("SecuritiesInvestorsNetBuySell")),
            "dealer_net": to_int(rec.get("DealersNetBuySell")),
        })
    return rows
