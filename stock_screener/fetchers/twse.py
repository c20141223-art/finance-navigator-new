"""TWSE (上市) fetchers. Field layouts verified 2026-07-12 against live
samples captured by the second verify_api_samples.py run (see
docs/api_samples/) — the notable surprises the samples exposed:

- www.twse.com.tw MI_INDEX now returns the same `tables` wrapper shape as
  post-revamp TPEx, not the legacy fields{n}/data{n} pairs.
- T86's foreign-investor column is 外陸資買賣超股數(不含外資自營商) — the
  bare 外資買賣超股數 name no longer exists.
- TWT49U (ex-rights) responds with a date RANGE of upcoming events, each
  row carrying its own 資料日期; the query date is not the ex-date.
- The disposition report's rows carry a 處置起迄時間 period — a stock is
  "in disposition" for that whole range, not just the announcement day.

Header profile below is the one PROVEN to get through TWSE's bot
protection from GitHub Actions runners: the sibling stock-report project
(c20141223-art/stock-report) has hit openapi.twse.com.tw and
www.twse.com.tw daily for months with exactly this UA + Referer shape,
while our first verification round — identical endpoints, but a UA that
carried a self-identifying "+https://github.com/..." URL and no Referer —
got intercept pages and Location-less 307s. The `_` millisecond-timestamp
query param on rwd endpoints is that project's cache-buster, kept as-is.
"""

from __future__ import annotations

import datetime as dt
import json
import time

from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import parse_roc_date, to_yyyymmdd
from stock_screener.fetchers.common import find_column, find_price_table, to_float, to_int
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError

REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.twse.com.tw/",
}

NOCACHE_HEADERS = {
    **REQ_HEADERS,
    "Cache-Control": "no-cache, no-store",
    "Pragma": "no-cache",
}


def _cache_bust() -> dict:
    return {"_": int(time.time() * 1000)}


def fetch_daily_all_raw(client: RateLimitedClient, config: SourcesConfig) -> RequestOutcome:
    """Latest trading day, full market. No date parameter."""
    return client.get(config.url("twse_daily_all"), headers=REQ_HEADERS)


def fetch_daily_history_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    url = config.url("twse_daily_history").format(date=to_yyyymmdd(date))
    return client.get(url, headers=NOCACHE_HEADERS, params=_cache_bust())


def parse_daily_all(raw_text: str, fallback_date: dt.date) -> list[dict]:
    """openapi.twse.com.tw STOCK_DAY_ALL: flat JSON array, English keys.
    Rows are dated by each record's own ROC `Date` field (fallback_date only
    if missing), so a run on a holiday can't mislabel the latest snapshot."""
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(
            "twse_daily_all", expected={"<list>"}, actual={type(payload).__name__}
        )
    required = {
        "Code", "Name", "TradeVolume", "TradeValue",
        "OpeningPrice", "HighestPrice", "LowestPrice", "ClosingPrice",
    }
    if payload:
        actual = set(payload[0].keys())
        if not required.issubset(actual):
            raise SchemaMismatchError("twse_daily_all", expected=required, actual=actual)

    rows = []
    for rec in payload:
        rec_date = parse_roc_date(str(rec.get("Date", ""))) or fallback_date
        rows.append({
            "stock_id": rec["Code"],
            "date": rec_date.isoformat(),
            "name": rec.get("Name"),
            "open": to_float(rec.get("OpeningPrice")),
            "high": to_float(rec.get("HighestPrice")),
            "low": to_float(rec.get("LowestPrice")),
            "close": to_float(rec.get("ClosingPrice")),
            "volume": to_int(rec.get("TradeVolume")),  # 股, caller converts to 張
            "turnover": to_int(rec.get("TradeValue")),
        })
    return rows


def parse_daily_history(raw_text: str, date: dt.date) -> list[dict]:
    """MI_INDEX?type=ALLBUT0999: bundles multiple fields{n}/data{n} tables."""
    payload = json.loads(raw_text)
    if payload.get("stat") not in ("OK", "ok", None) and "fields" not in payload and "fields1" not in payload:
        # Common "no trading today" response shape; treat as empty, not an error.
        return []

    fields, data = find_price_table(payload, source="twse_daily_history")

    idx_id = find_column(fields, ("證券代號",), source="twse_daily_history")
    idx_close = find_column(fields, ("收盤價",), source="twse_daily_history")
    idx_open = find_column(fields, ("開盤價",), source="twse_daily_history")
    idx_high = find_column(fields, ("最高價",), source="twse_daily_history")
    idx_low = find_column(fields, ("最低價",), source="twse_daily_history")
    idx_volume = find_column(fields, ("成交股數",), source="twse_daily_history")
    idx_turnover = find_column(fields, ("成交金額",), source="twse_daily_history")

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
            "volume": to_int(row[idx_volume]),  # 股
            "turnover": to_int(row[idx_turnover]),
        })
    return rows


def fetch_institutional_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    url = config.url("twse_institutional").format(date=to_yyyymmdd(date))
    return client.get(url, headers=NOCACHE_HEADERS, params=_cache_bust())


def parse_institutional(raw_text: str, date: dt.date) -> list[dict]:
    payload = json.loads(raw_text)
    if payload.get("stat") not in ("OK", "ok"):
        return []

    fields = payload.get("fields") or []
    data = payload.get("data") or []
    if not fields or not data:
        return []

    idx_id = find_column(fields, ("證券代號",), source="twse_institutional")
    # Live T86 field is 外陸資買賣超股數(不含外資自營商) — 外資自營商 is
    # reported separately and deliberately not counted here, mirroring the
    # market convention for "外資買賣超".
    idx_foreign = find_column(fields, ("外陸資", "買賣超"), source="twse_institutional")
    idx_trust = find_column(fields, ("投信", "買賣超"), source="twse_institutional")
    idx_dealer = find_column(
        fields,
        ("自營商", "買賣超"),
        must_not_contain=("自行買賣", "避險", "外資"),
        source="twse_institutional",
    )

    rows = []
    for row in data:
        stock_id = str(row[idx_id]).strip()
        if not stock_id:
            continue
        rows.append({
            "stock_id": stock_id,
            "date": date.isoformat(),
            "foreign_net": to_int(row[idx_foreign]),
            "trust_net": to_int(row[idx_trust]),
            "dealer_net": to_int(row[idx_dealer]),
        })
    return rows


def fetch_ex_rights_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    url = config.url("twse_ex_rights").format(date=to_yyyymmdd(date))
    return client.get(url, headers=NOCACHE_HEADERS, params=_cache_bust())


def parse_ex_rights(raw_text: str, date: dt.date) -> list[dict]:
    """除權除息參考價 -> 用於 stock_screener.adjust 回推調整係數。

    TWT49U returns a range of upcoming pre-announced ex-rights events; each
    row's own 資料日期 (ROC format) is the actual ex-date. Rows whose date
    can't be parsed are skipped — a corporate action with an unknown
    ex-date is unusable for back-adjustment."""
    payload = json.loads(raw_text)
    if payload.get("stat") not in ("OK", "ok"):
        return []
    fields = payload.get("fields") or []
    data = payload.get("data") or []
    if not fields or not data:
        return []

    idx_id = find_column(fields, ("代號",), source="twse_ex_rights")
    idx_date = find_column(fields, ("資料日期",), source="twse_ex_rights")
    idx_ref = find_column(fields, ("除權息參考價",), source="twse_ex_rights")
    idx_prev_close = find_column(
        fields, ("收盤價",), must_not_contain=("參考",), source="twse_ex_rights"
    )

    rows = []
    for row in data:
        stock_id = str(row[idx_id]).strip()
        ex_date = parse_roc_date(str(row[idx_date]))
        if not stock_id or ex_date is None:
            continue
        rows.append({
            "stock_id": stock_id,
            "ex_date": ex_date.isoformat(),
            "reference_price": to_float(row[idx_ref]),
            "prev_close": to_float(row[idx_prev_close]),
        })
    return rows


def fetch_disposition_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    url = config.url("twse_disposition")
    return client.get(
        url,
        headers=NOCACHE_HEADERS,
        params={"date": to_yyyymmdd(date), "response": "json", **_cache_bust()},
    )


def parse_disposition(raw_text: str, date: dt.date) -> list[dict]:
    """A disposition applies for the row's whole 處置起迄時間 range (e.g.
    '115/07/03～115/07/16'), so only rows whose range covers `date` are
    emitted. If a range fails to parse we include the stock anyway —
    over-flagging risk beats silently letting a disposed stock through
    the 排雷 filter."""
    payload = json.loads(raw_text)
    if payload.get("stat") not in ("OK", "ok"):
        return []
    fields = payload.get("fields") or []
    data = payload.get("data") or []
    if not fields or not data:
        return []

    idx_id = find_column(fields, ("證券代號",), source="twse_disposition")
    idx_period = find_column(fields, ("處置起迄",), source="twse_disposition")

    rows = []
    for row in data:
        stock_id = str(row[idx_id]).strip()
        if not stock_id:
            continue
        period = str(row[idx_period])
        bounds = [parse_roc_date(p) for p in period.replace("~", "～").split("～")]
        if len(bounds) == 2 and bounds[0] and bounds[1]:
            if not (bounds[0] <= date <= bounds[1]):
                continue
        rows.append({"stock_id": stock_id, "date": date.isoformat(), "reason": "處置股"})
    return rows
