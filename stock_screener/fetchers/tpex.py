"""TPEx (上櫃) fetchers.

Verification status (two live rounds, 2026-07-11/12, samples in
docs/api_samples/):

- `tpex_daily_all`: field names confirmed against a full live sample.
- `tpex_daily_history`: post-2024-revamp `tables` wrapper shape confirmed,
  including value-level parsing against a 1012-row sample.
- `tpex_institutional`: round two proved the guessed path
  tpex_3insti_daily_trade wrong (serves the homepage); the authoritative
  path from TPEx's own swagger catalog (docs/api_samples/
  _tpex_openapi_swagger.json) is /tpex_3insti_daily_trading, and
  `parse_institutional` uses that schema's property names. The swagger
  spells several of them with erratic spaces (e.g. "Dealers -TotalSell"),
  so keys are matched space-insensitively. Round three confirmed against
  a 921-row live sample, including internal consistency (foreign + trust
  + dealer == TotalDifference on spot-checked rows).
"""

from __future__ import annotations

import datetime as dt
import json

from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import parse_roc_date, to_roc_date
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


def parse_daily_all(raw_text: str, fallback_date: dt.date) -> list[dict]:
    """Confirmed field names from a live sample (docs/api_samples/tpex_daily_all.json):
    SecuritiesCompanyCode, CompanyName, Close, Open, High, Low, TradingShares,
    TransactionAmount. Rows dated by each record's own ROC `Date` field."""
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
        rec_date = parse_roc_date(str(rec.get("Date", ""))) or fallback_date
        rows.append({
            "stock_id": rec["SecuritiesCompanyCode"],
            "date": rec_date.isoformat(),
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
    """Post-revamp 'tables' wrapper; value-level parsing confirmed against
    a 1,012-row live sample (round two)."""
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


def _norm_key(key: str) -> str:
    return key.replace(" ", "")


def _pick(rec_normed: dict, target: str, source: str):
    try:
        return rec_normed[_norm_key(target)]
    except KeyError:
        raise SchemaMismatchError(source, expected={target}, actual=set(rec_normed)) from None


def parse_institutional(raw_text: str, fallback_date: dt.date) -> list[dict]:
    """/tpex_3insti_daily_trading per swagger schema. Foreign net uses the
    "(Foreign Dealers excluded)" variant to mirror TWSE T86's
    外陸資(不含外資自營商) convention. Values are 股 (shares); the loader
    converts to 張.

    The endpoint is a latest-day snapshot with each record carrying its own
    ROC `Date`; rows are stamped with that embedded date (fallback_date only
    when it's missing/unparsable) so callers can never mislabel the data —
    the original backfill loop would otherwise have stamped the same latest
    snapshot onto every historical date."""
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(
            "tpex_institutional", expected={"<list>"}, actual={type(payload).__name__}
        )

    rows = []
    for rec in payload:
        rec_normed = {_norm_key(k): v for k, v in rec.items()}
        stock_id = str(_pick(rec_normed, "SecuritiesCompanyCode", "tpex_institutional")).strip()
        if not stock_id:
            continue
        rec_date = parse_roc_date(str(rec_normed.get("Date", ""))) or fallback_date
        foreign = _pick(
            rec_normed,
            "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference",
            "tpex_institutional",
        )
        trust = _pick(rec_normed, "SecuritiesInvestmentTrustCompanies-Difference", "tpex_institutional")
        dealer = _pick(rec_normed, "Dealers-Difference", "tpex_institutional")
        rows.append({
            "stock_id": stock_id,
            "date": rec_date.isoformat(),
            "foreign_net": to_int(foreign),
            "trust_net": to_int(trust),
            "dealer_net": to_int(dealer),
        })
    return rows
