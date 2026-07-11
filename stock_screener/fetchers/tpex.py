"""TPEx (上櫃) fetchers.

TPEx's legacy date-parameterized endpoints (otc_quotes_no1430 etc.) have
historically returned a DataTables-style `{"aaData": [[...], ...]}` shape —
a plain array-of-arrays with NO header names, unlike TWSE's fields/data
pairs. That means column order must be known in advance rather than looked
up by keyword. This is the single riskiest piece of the whole data layer to
have gotten from memory instead of a live sample: `_AADATA_COLUMNS` below is
a best-effort guess at column order and MUST be confirmed against a real
response (see docs/api_samples/README.md) before this is trusted. We at
least guard against a silently-wrong mapping by checking the row length.
"""

from __future__ import annotations

import datetime as dt
import json

from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import to_roc_date
from stock_screener.fetchers.common import to_float, to_int
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError

# UNVERIFIED — order per historical TPEx otc_quotes_no1430 convention.
_AADATA_COLUMNS = [
    "stock_id", "name", "close", "change", "open", "high", "low",
    "volume_shares", "turnover", "transactions", "last_bid", "last_bid_volume",
    "last_ask", "last_ask_volume", "shares_outstanding",
    "next_day_reference", "next_day_limit_up", "next_day_limit_down",
]


def fetch_daily_all_raw(client: RateLimitedClient, config: SourcesConfig) -> RequestOutcome:
    return client.get(config.url("tpex_daily_all"))


def fetch_daily_history_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    url = config.url("tpex_daily_history").format(roc_date=to_roc_date(date))
    return client.get(url)


def parse_daily_all(raw_text: str) -> list[dict]:
    """tpex openapi mainboard daily close quotes: assumed to follow the
    same flat-JSON-array-with-named-keys convention TWSE's openapi uses.
    UNVERIFIED — confirm actual key names once network access is available."""
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(
            "tpex_daily_all", expected={"<list>"}, actual={type(payload).__name__}
        )
    required = {"Code", "Name", "Close", "Open", "High", "Low", "TradingVolume"}
    if payload:
        actual = set(payload[0].keys())
        if not required.issubset(actual):
            raise SchemaMismatchError("tpex_daily_all", expected=required, actual=actual)

    rows = []
    for rec in payload:
        rows.append({
            "stock_id": rec["Code"],
            "name": rec.get("Name"),
            "open": to_float(rec.get("Open")),
            "high": to_float(rec.get("High")),
            "low": to_float(rec.get("Low")),
            "close": to_float(rec.get("Close")),
            "volume": to_int(rec.get("TradingVolume")),
            "turnover": to_int(rec.get("TransactionAmount")),
        })
    return rows


def parse_daily_history(raw_text: str, date: dt.date) -> list[dict]:
    payload = json.loads(raw_text)
    aa_data = payload.get("aaData")
    if aa_data is None:
        # No trading today / holiday response shape — treat as empty.
        return []

    rows = []
    for row in aa_data:
        if len(row) != len(_AADATA_COLUMNS):
            raise SchemaMismatchError(
                "tpex_daily_history",
                expected={f"{len(_AADATA_COLUMNS)} 欄"},
                actual={f"{len(row)} 欄: {row}"},
            )
        rec = dict(zip(_AADATA_COLUMNS, row))
        stock_id = str(rec["stock_id"]).strip()
        if not stock_id:
            continue
        rows.append({
            "stock_id": stock_id,
            "date": date.isoformat(),
            "open": to_float(rec["open"]),
            "high": to_float(rec["high"]),
            "low": to_float(rec["low"]),
            "close": to_float(rec["close"]),
            "volume": to_int(rec["volume_shares"]),
            "turnover": to_int(rec["turnover"]),
        })
    return rows


def fetch_institutional_raw(client: RateLimitedClient, config: SourcesConfig) -> RequestOutcome:
    return client.get(config.url("tpex_institutional"))


def parse_institutional(raw_text: str, date: dt.date) -> list[dict]:
    """UNVERIFIED shape — assumed flat JSON array like tpex_daily_all."""
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
