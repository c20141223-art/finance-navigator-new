"""Upsert helpers: parsed fetcher rows -> SQLite. All idempotent (safe to
re-run the same date), which is what makes "每日增量更新，不重複回補已有
資料" (spec 1.1) actually true in practice: the pipeline can always just
INSERT OR REPLACE for the target date range without checking existence
first.
"""

from __future__ import annotations

import datetime as dt
import sqlite3


def upsert_daily_price(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    """rows: dicts with stock_id, date, open, high, low, close, volume (股),
    turnover. Volume is stored in 張 (1000 股) per spec 1.2, so divide here
    at the loader boundary, not scattered across fetchers."""
    payload = [
        (
            r["stock_id"],
            r["date"],
            r.get("open"),
            r.get("high"),
            r.get("low"),
            r["close"],
            None if r.get("volume") is None else round(r["volume"] / 1000),
            r.get("turnover"),
            source,
        )
        for r in rows
        if r.get("close") is not None
    ]
    conn.executemany(
        """
        INSERT INTO daily_price
            (stock_id, date, open, high, low, close, volume, turnover, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_id, date) DO UPDATE SET
            open = excluded.open, high = excluded.high, low = excluded.low,
            close = excluded.close, volume = excluded.volume,
            turnover = excluded.turnover, source = excluded.source
        """,
        payload,
    )
    return len(payload)


def upsert_institutional(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    """Fetcher rows carry 股 (shares, per live T86/TPEx samples); stored as
    張 per spec 1.2 — same boundary conversion as upsert_daily_price."""
    def to_lots(v):
        return None if v is None else round(v / 1000)

    payload = [
        (r["stock_id"], r["date"], to_lots(r.get("foreign_net")),
         to_lots(r.get("trust_net")), to_lots(r.get("dealer_net")), source)
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO institutional (stock_id, date, foreign_net, trust_net, dealer_net, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_id, date) DO UPDATE SET
            foreign_net = excluded.foreign_net, trust_net = excluded.trust_net,
            dealer_net = excluded.dealer_net, source = excluded.source
        """,
        payload,
    )
    return len(payload)


def upsert_monthly_revenue(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    payload = [
        (
            r["stock_id"], r["year_month"], r.get("revenue"), r.get("yoy"),
            r.get("mom"), r.get("cumulative_yoy"), r.get("announced_date"), source,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO monthly_revenue
            (stock_id, year_month, revenue, yoy, mom, cumulative_yoy, announced_date, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_id, year_month) DO UPDATE SET
            revenue = excluded.revenue, yoy = excluded.yoy, mom = excluded.mom,
            cumulative_yoy = excluded.cumulative_yoy,
            announced_date = excluded.announced_date, source = excluded.source
        """,
        payload,
    )
    return len(payload)


def upsert_risk_list(conn: sqlite3.Connection, rows: list[dict], source: str) -> int:
    payload = [(r["date"], r["stock_id"], r["reason"], source) for r in rows]
    conn.executemany(
        """
        INSERT INTO risk_list (date, stock_id, reason, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date, stock_id, reason) DO UPDATE SET source = excluded.source
        """,
        payload,
    )
    return len(payload)


def upsert_stock_meta(conn: sqlite3.Connection, rows: list[dict]) -> int:
    payload = [(r["stock_id"], r.get("name"), r.get("market"), r.get("industry")) for r in rows]
    conn.executemany(
        """
        INSERT INTO stock_meta (stock_id, name, market, industry, is_active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(stock_id) DO UPDATE SET
            name = excluded.name, market = excluded.market, industry = excluded.industry
        """,
        payload,
    )
    return len(payload)


def log_fetch_result(
    conn: sqlite3.Connection,
    date: dt.date,
    source: str,
    status: str,
    record_count: int | None,
    error_message: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log (date, source, status, record_count, error_message, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            date.isoformat(),
            source,
            status,
            record_count,
            error_message,
            dt.datetime.now(dt.timezone.utc).isoformat(),
        ),
    )


def update_active_flags(conn: sqlite3.Connection, date: dt.date, inactive_after_missing_days: int) -> None:
    """Mark stocks that had no daily_price row today as one day more
    'missing'; reset the counter for stocks that did. Flip is_active off
    once missing_days crosses the configured threshold (spec 1.4)."""
    date_str = date.isoformat()
    seen_today = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT stock_id FROM daily_price WHERE date = ?", (date_str,)
        )
    }
    known_stocks = [row[0] for row in conn.execute("SELECT stock_id FROM stock_meta")]

    for stock_id in known_stocks:
        if stock_id in seen_today:
            conn.execute(
                "UPDATE stock_meta SET last_seen_date = ?, missing_days = 0, is_active = 1 "
                "WHERE stock_id = ?",
                (date_str, stock_id),
            )
        else:
            conn.execute(
                "UPDATE stock_meta SET missing_days = missing_days + 1 WHERE stock_id = ?",
                (stock_id,),
            )
            conn.execute(
                "UPDATE stock_meta SET is_active = 0 "
                "WHERE stock_id = ? AND missing_days >= ?",
                (stock_id, inactive_after_missing_days),
            )
