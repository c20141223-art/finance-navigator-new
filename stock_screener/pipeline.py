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
                             lambda outcome: twse.parse_daily_all(outcome.text, date)),
    )
    loaders.upsert_daily_price(conn, twse_rows, source="twse")

    tpex_rows = _run_source(
        conn, date, "tpex_daily_all",
        lambda: _require_ok(tpex.fetch_daily_all_raw(client, config), "tpex_daily_all",
                             lambda outcome: tpex.parse_daily_all(outcome.text, date)),
    )
    loaders.upsert_daily_price(conn, tpex_rows, source="tpex")


def update_index_price(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    """發行量加權股價指數 (TAIEX) for the month containing `date`. One call
    covers a whole month (rows carry their own dates), so the daily job hits
    it once for the current month and backfill hits it once per calendar
    month spanned — see backfill()."""
    rows = _run_source(
        conn, date, "twse_index_history",
        lambda: _require_ok(twse.fetch_index_history_raw(client, config, date), "twse_index_history",
                             lambda outcome: twse.parse_index_history(outcome.text, date)),
    )
    loaders.upsert_index_price(conn, rows, source="twse")


def update_institutional_twse(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    """T86 takes a date parameter, so historical days are fetchable."""
    twse_rows = _run_source(
        conn, date, "twse_institutional",
        lambda: _require_ok(twse.fetch_institutional_raw(client, config, date), "twse_institutional",
                             lambda outcome: twse.parse_institutional(outcome.text, date)),
    )
    loaders.upsert_institutional(conn, twse_rows, source="twse")


def update_institutional_tpex(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    """The TPEx openapi report is a latest-day snapshot only (no history
    endpoint); rows carry their own embedded dates, so calling this can
    never mislabel data — but backfill calls it exactly once, since 90
    identical snapshot fetches would return the same rows."""
    tpex_rows = _run_source(
        conn, date, "tpex_institutional",
        lambda: _require_ok(tpex.fetch_institutional_raw(client, config), "tpex_institutional",
                             lambda outcome: tpex.parse_institutional(outcome.text, date)),
    )
    loaders.upsert_institutional(conn, tpex_rows, source="tpex")


def update_institutional(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    update_institutional_twse(conn, client, config, date)
    update_institutional_tpex(conn, client, config, date)


def update_risk_list_twse(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    disposition_rows = _run_source(
        conn, date, "twse_disposition",
        lambda: _require_ok(twse.fetch_disposition_raw(client, config, date), "twse_disposition",
                             lambda outcome: twse.parse_disposition(outcome.text, date)),
    )
    loaders.upsert_risk_list(conn, disposition_rows, source="twse")


def update_risk_list_tpex(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    """Current-snapshot endpoint (disposition periods filtered against
    `date`); one fetch covers the whole backfill window."""
    tpex_disposition_rows = _run_source(
        conn, date, "tpex_disposition",
        lambda: _require_ok(
            risk_list.fetch_tpex_disposition_raw(client, config, date), "tpex_disposition",
            lambda outcome: risk_list.parse_tpex_disposition(outcome.text, date, "tpex_disposition"),
        ),
    )
    loaders.upsert_risk_list(conn, tpex_disposition_rows, source="tpex")


def update_risk_list(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> None:
    update_risk_list_twse(conn, client, config, date)
    update_risk_list_tpex(conn, client, config, date)


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
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date | None = None
) -> None:
    """Only meaningful to run around the 10th-12th of each month (spec 1.5).
    The endpoints return the latest published month; each row's own
    資料年月 determines which month gets stored."""
    date = date or dt.date.today()
    for market in ("sii", "otc"):
        source = f"monthly_revenue_{market}"
        rows = _run_source(
            conn, date, source,
            lambda market=market, source=source: _require_ok(
                mops.fetch_monthly_revenue_raw(client, config, market), source,
                lambda outcome: mops.parse_monthly_revenue(outcome.text, source),
            ),
        )
        loaders.upsert_monthly_revenue(conn, rows, source=market)


def daily_update(
    conn: sqlite3.Connection, client: RateLimitedClient, config: SourcesConfig, date: dt.date | None = None
) -> None:
    date = date or dt.date.today()
    update_latest_daily_price(conn, client, config, date)
    update_index_price(conn, client, config, date)
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
    """Historical backfill. Per-day fetches are limited to the endpoints
    that genuinely take a date parameter (MI_INDEX, TPEx quotes, T86, TWSE
    disposition); snapshot-style endpoints (ex-rights pre-announcements,
    TPEx disposition/institutional, monthly revenue, ISIN meta) are hit
    exactly once — they can't return history, and hammering them per-day
    would only multiply load on the exchanges' WAF-guarded hosts.

    Known data limitation, documented rather than papered over: TPEx has
    no historical institutional endpoint, so 上櫃 chips factors only
    accumulate from daily runs going forward; right after a fresh backfill
    they reflect a single day of data."""
    end_date = end_date or dt.date.today()
    n_days = n_days or config.data.min_backfill_trading_days

    update_stock_meta(conn, client, config, end_date)
    update_monthly_revenue(conn, client, config, end_date)
    update_ex_rights(conn, client, config, end_date)
    update_risk_list_tpex(conn, client, config, end_date)
    update_institutional_tpex(conn, client, config, end_date)

    candidate_days = trading_day_candidates(end_date, n_days)

    # TAIEX index: one MI_5MINS_HIST call returns a whole month, so fetch once
    # per distinct calendar month spanned rather than per day.
    seen_months: set[tuple[int, int]] = set()
    for date in candidate_days:
        key = (date.year, date.month)
        if key not in seen_months:
            seen_months.add(key)
            update_index_price(conn, client, config, date)

    for date in candidate_days:
        update_daily_price_for_date(conn, client, config, date)
        update_institutional_twse(conn, client, config, date)
        update_risk_list_twse(conn, client, config, date)

    loaders.update_active_flags(conn, end_date, config.data.inactive_after_missing_days)


def _require_ok(outcome, source: str, parse_fn):
    if not outcome.ok:
        raise RuntimeError(f"[{source}] 請求失敗: {outcome.error}")
    return parse_fn(outcome)
