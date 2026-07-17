"""T+5/20/60 forward-return backfill for recorded triggers (規格書 5.1).

Every trigger row (momentum or reversal) gets its forward performance filled
in once enough trading days have elapsed. All returns are computed on the
back-adjusted close so a dividend between entry and exit can't masquerade as
a loss. Stored as PERCENT (e.g. 5.30 = +5.30%).

  entry            = adjusted close on the trigger date
  return_tN        = adjusted_close[N trading days later] / entry - 1
  mfe (最大有利)   = max adjusted_high over the next `MFE_MAE_WINDOW` bars / entry - 1
  mae (最大不利)   = min adjusted_low  over the same window / entry - 1

Backfill is fill-once: a column is written only when it is currently NULL and
the required future bar now exists, so re-running the daily job is idempotent
and never rewrites settled history. A trigger whose trigger date isn't in
daily_price (shouldn't happen, but defensive) is skipped.
"""

from __future__ import annotations

import logging
import sqlite3

from stock_screener.adjust import get_adjusted_prices

logger = logging.getLogger(__name__)

HORIZONS = {"return_t5": 5, "return_t20": 20, "return_t60": 60}
MFE_MAE_WINDOW = 20


def backfill_returns(conn: sqlite3.Connection, asof: str | None = None) -> int:
    """Fill NULL return/mfe/mae columns for every trigger whose future bars
    are now available (<= asof if given, else using all stored bars).
    Returns the number of trigger rows updated."""
    pending = conn.execute(
        "SELECT id, stock_id, date, return_t5, return_t20, return_t60, mfe, mae "
        "FROM triggers "
        "WHERE return_t5 IS NULL OR return_t20 IS NULL OR return_t60 IS NULL "
        "   OR mfe IS NULL OR mae IS NULL"
    ).fetchall()
    if not pending:
        return 0

    # group by stock so each adjusted-price frame is built once
    by_stock: dict[str, list] = {}
    for row in pending:
        by_stock.setdefault(row[1], []).append(row)

    updated = 0
    for stock_id, rows in by_stock.items():
        adj = get_adjusted_prices(conn, stock_id)
        if adj.empty:
            continue
        if asof is not None:
            adj = adj[adj["date"] <= asof]
        dates = adj["date"].tolist()
        date_to_pos = {d: i for i, d in enumerate(dates)}
        closes = adj["close"].tolist()
        highs = adj["high"].tolist()
        lows = adj["low"].tolist()

        for (tid, _sid, tdate, r5, r20, r60, mfe, mae) in rows:
            pos = date_to_pos.get(tdate)
            if pos is None:
                continue
            entry = closes[pos]
            if not entry:
                continue

            updates: dict[str, float] = {}
            current = {"return_t5": r5, "return_t20": r20, "return_t60": r60}
            for col, n in HORIZONS.items():
                if current[col] is None and pos + n < len(closes):
                    fut = closes[pos + n]
                    if fut is not None:
                        updates[col] = round((fut / entry - 1.0) * 100.0, 2)

            if (mfe is None or mae is None) and pos + MFE_MAE_WINDOW < len(closes):
                window_hi = [h for h in highs[pos + 1:pos + MFE_MAE_WINDOW + 1] if h is not None]
                window_lo = [l for l in lows[pos + 1:pos + MFE_MAE_WINDOW + 1] if l is not None]
                if window_hi and mfe is None:
                    updates["mfe"] = round((max(window_hi) / entry - 1.0) * 100.0, 2)
                if window_lo and mae is None:
                    updates["mae"] = round((min(window_lo) / entry - 1.0) * 100.0, 2)

            if updates:
                assignments = ", ".join(f"{c} = ?" for c in updates)
                conn.execute(
                    f"UPDATE triggers SET {assignments} WHERE id = ?",
                    (*updates.values(), tid),
                )
                updated += 1

    logger.info("Return backfill updated %d trigger rows", updated)
    return updated
