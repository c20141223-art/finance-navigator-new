"""大盤狀態標記（規格書 6.1）. Markers only — NEVER scored, never fed back
into stock selection. Two pieces:

- Index side (加權指數 vs 60MA、60MA 方向): read from `index_price` (TAIEX).
  60MA is the simple mean of the last 60 index closes; direction compares
  today's 60MA against the 60MA `slope_lookback` trading days ago.
- Breadth side (全市場多頭排列佔比): computed by the momentum screen over
  the filtered common-stock universe (momentum.compute_market_regime) and
  passed in — this module only merges it into one status dict for the email.

Everything degrades gracefully: if the index history is short or missing
(e.g. the TAIEX fetch failed and was logged to fetch_log), the index fields
come back None with `index_available=False` so the report can say so
plainly instead of inventing a number.
"""

from __future__ import annotations

import sqlite3

MA_WINDOW = 60
SLOPE_LOOKBACK = 5  # 交易日；60MA 今天 vs N 日前決定方向
FLAT_EPS = 0.001    # |變動| < 0.1% 視為走平


def compute_index_status(conn: sqlite3.Connection, asof: str,
                         index_id: str = "TAIEX") -> dict:
    rows = conn.execute(
        "SELECT date, close FROM index_price WHERE index_id = ? AND date <= ? "
        "ORDER BY date DESC LIMIT ?",
        (index_id, asof, MA_WINDOW + SLOPE_LOOKBACK),
    ).fetchall()
    closes = [r[1] for r in rows][::-1]  # ascending

    if len(closes) < MA_WINDOW:
        return {
            "index_available": False,
            "index_id": index_id,
            "index_close": closes[-1] if closes else None,
            "index_60ma": None,
            "index_vs_60ma_pct": None,
            "ma60_direction": None,
            "history_len": len(closes),
        }

    ma60_now = sum(closes[-MA_WINDOW:]) / MA_WINDOW
    index_close = closes[-1]
    vs_pct = (index_close / ma60_now - 1.0) * 100.0

    direction = None
    if len(closes) >= MA_WINDOW + SLOPE_LOOKBACK:
        past = closes[-(MA_WINDOW + SLOPE_LOOKBACK):-SLOPE_LOOKBACK]
        ma60_past = sum(past) / MA_WINDOW
        change = ma60_now / ma60_past - 1.0 if ma60_past else 0.0
        if change > FLAT_EPS:
            direction = "up"
        elif change < -FLAT_EPS:
            direction = "down"
        else:
            direction = "flat"

    return {
        "index_available": True,
        "index_id": index_id,
        "index_close": round(index_close, 2),
        "index_60ma": round(ma60_now, 2),
        "index_vs_60ma_pct": round(vs_pct, 2),
        "ma60_direction": direction,
        "history_len": len(closes),
    }


def build_market_status(conn: sqlite3.Connection, asof: str, breadth: dict) -> dict:
    """Merge the index-side markers with the breadth marker (bullish
    alignment %) the momentum screen already computed."""
    status = compute_index_status(conn, asof)
    status["bullish_alignment_pct"] = breadth.get("bullish_alignment_pct")
    status["breadth_universe"] = breadth.get("universe")
    return status
