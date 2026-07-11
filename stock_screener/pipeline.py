"""Orchestrates fetch -> parse -> load for one day, and the initial
multi-day backfill. Every source is isolated: one source failing is logged
to fetch_log and skipped, never allowed to abort the rest of the run
(spec section 9: "任一資料源失敗不可讓整個 pipeline 崩潰")."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from typing import Callable

from stock_screener import loaders
from stock_screener.adjust import upsert_corporate_action
from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import trading_day_candidates
from stock_screener.fetchers import meta, mops, risk_list, tpex, twse
from stock_screener.http_client import RateLimitedClient

logger = logging.getLogger(__name__)


def _run_source(
    conn: sqlite3.Connection,
    date: dt.date,
    source: str,
    fetch_and_parse: Callable[[], list[dict]],
) -> list[dict]:
    """Runs a fetch+parse callable, logs the outcome, never raises."""
    try:
        rows = fetch_and_parse()
        loaders.log_fetch_result(conn, date, source, "success", len(rows), None)
        return rows
    except Exception as exc:  # noqa: BLE001 - deliberate: one bad source must not kill the run
        logger.exception("Source %s failed on %s", source, date)
        loaders.log_fetch_result(conn, date, source, "failure", None, str(exc))
        return []


def update_daily_price_for_date(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    twse_rows = _run_source(
        conn, date, "twse_daily_history",
        lambda: _require_ok(twse.fetch_daily_history_raw(client, config, date), "twse_daily_history",
                             lambda outcome: twse.parse_daily_history(outcome.text, date)),
    )
    loaders.upsert_daily_price(conn, twse_rows, source="twse")

    tpex_rows = _run_source(
        conn, date, "tpex_daily_history",
        lambda: _require_ok(tpex.fetch_daily_history_raw(client, config, date), "tpex_daily_history",
                             lambda outcome: tpex.parse_daily_history(outcome.text, date)),
    )
    loaders.upsert_daily_price(conn, tpex_rows, source="tpex")


def update_latest_daily_price(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    """Used by the 17:30 daily job: today's data is already the 'latest'
    snapshot, so the no-date-param endpoints are enough and cheaper."""
    twse_rows = _run_source(
        conn, date, "twse_daily_all",
        lambda: _require_ok(twse.fetch_daily_all_raw(client, config), "twse_daily_all",
                             lambda outcome: twse.parse_daily_all(outcome.text)),
    )
    for r in twse_rows:
        r["date"] = date.isoformat()
    loaders.upsert_daily_price(conn, twse_rows, source="twse")

    tpex_rows = _run_source(
        conn, date, "tpex_daily_all",
        lambda: _require_ok(tpex.fetch_daily_all_raw(client, config), "tpex_daily_all",
                             lambda outcome: tpex.parse_daily_all(outcome.text)),
    )
    for r in tpex_rows:
        r["date"] = date.isoformat()
    loaders.upsert_daily_price(conn, tpex_rows, source="tpex")


def update_institutional(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    twse_rows = _run_source(
        conn, date, "twse_institutional",
        lambda: _require_ok(twse.fetch_institutional_raw(client, config, date), "twse_institutional",
                             lambda outcome: twse.parse_institutional(outcome.text, date)),
    )
    loaders.upsert_institutional(conn, twse_rows, source="twse")

    tpex_rows = _run_source(
        conn, date, "tpex_institutional",
        lambda: _require_ok(tpex.fetch_institutional_raw(client, config), "tpex_institutional",
                             lambda outcome: tpex.parse_institutional(outcome.text, date)),
    )
    loaders.upsert_institutional(conn, tpex_rows, source="tpex")


def update_risk_list(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    disposition_rows = _run_source(
        conn, date, "twse_disposition",
        lambda: _require_ok(twse.fetch_disposition_raw(client, config, date), "twse_disposition",
                             lambda outcome: twse.parse_disposition(outcome.text, date)),
    )
    loaders.upsert_risk_list(conn, disposition_rows, source="twse")

    tpex_disposition_rows = _run_source(
        conn, date, "tpex_disposition",
        lambda: _require_ok(
            risk_list.fetch_tpex_disposition_raw(client, config, date), "tpex_disposition",
            lambda outcome: risk_list.parse_tpex_disposition(outcome.content, date, "tpex_disposition"),
        ),
    )
    loaders.upsert_risk_list(conn, tpex_disposition_rows, source="tpex")


def update_ex_rights(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    rows = _run_source(
        conn, date, "twse_ex_rights",
        lambda: _require_ok(twse.fetch_ex_rights_raw(client, config, date), "twse_ex_rights",
                             lambda outcome: twse.parse_ex_rights(outcome.text, date)),
    )
    for r in rows:
        if r.get("reference_price") and r.get("prev_close"):
            upsert_corporate_action(
                conn, r["stock_id"], r["ex_date"], r["reference_price"], r["prev_close"],
                source="twse",
            )


def update_stock_meta(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    listed_rows = _run_source(
        conn, date, "isin_listed",
        lambda: _require_ok(meta.fetch_isin_raw(client, config, "listed"), "isin_listed",
                             lambda outcome: meta.parse_isin(outcome.content, "上市", "isin_listed")),
    )
    loaders.upsert_stock_meta(conn, listed_rows)

    otc_rows = _run_source(
        conn, date, "isin_otc",
        lambda: _require_ok(meta.fetch_isin_raw(client, config, "otc"), "isin_otc",
                             lambda outcome: meta.parse_isin(outcome.content, "上櫃", "isin_otc")),
    )
    loaders.upsert_stock_meta(conn, otc_rows)


def update_monthly_revenue(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, month: dt.date
) -> None:
    """Only meaningful to run around the 10th-12th of each month (spec 1.5)."""
    year_month = f"{month.year:04d}-{month.month:02d}"
    for market in ("sii", "otc"):
        source = f"mops_monthly_revenue_{market}"
        rows = _run_source(
            conn, month, source,
            lambda market=market, source=source: _require_ok(
                mops.fetch_monthly_revenue_raw(client, config, month, market), source,
                lambda outcome: mops.parse_monthly_revenue(outcome.content, year_month, source),
            ),
        )
        loaders.upsert_monthly_revenue(conn, rows, source=market)


def daily_update(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date | None = None
) -> None:
    date = date or dt.date.today()
    update_latest_daily_price(conn, client, config, date)
    update_institutional(conn, client, config, date)
    update_risk_list(conn, client, config, date)
    update_ex_rights(conn, client, config, date)
    loaders.update_active_flags(conn, date, config.data.inactive_after_missing_days)


def backfill(
    conn: sqlite3.Connection,
    client: RateLimitedClient,
    config: SourcesConfig,
    end_date: dt.date | None = None,
    n_days: int | None = None,
) -> None:
    end_date = end_date or dt.date.today()
    n_days = n_days or config.data.min_backfill_trading_days
    update_stock_meta(conn, client, config, end_date)
    for date in trading_day_candidates(end_date, n_days):
        update_daily_price_for_date(conn, client, config, date)
        update_institutional(conn, client, config, date)
        update_ex_rights(conn, client, config, date)
        update_risk_list(conn, client, config, date)
    loaders.update_active_flags(conn, end_date, config.data.inactive_after_missing_days)


def _require_ok(outcome, source: str, parse_fn):
    if not outcome.ok:
        raise RuntimeError(f"[{source}] 請求失敗: {outcome.error}")
    return parse_fn(outcome)
