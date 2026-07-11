"""還原股價 (back-adjusted price) computation.

Approach (chosen per spec 1.4 — "實作方式由 CC 提案，但必須明確處理"):

`daily_price` always stores the raw, as-reported OHLC. We never mutate it.
Instead, every ex-rights/ex-dividend event is recorded in `corporate_action`
with the TWSE/TPEx-published reference price for that date:

    adj_factor = prev_close (raw, day before ex-date) / reference_price

This factor is the ratio by which the pre-ex-date raw price series must be
multiplied to make it continuous with the post-ex-date series. Adjusted
price for any historical date is the raw price multiplied by the product of
every corporate action's adj_factor whose ex_date is strictly after that
date (each later ex-event shrinks all prior history a bit more) — i.e.
standard "向前復權" back-adjustment, computed on demand rather than baked
into storage, so it stays reproducible as new corporate actions arrive.

Technical indicators (MA, RSI, MACD, drawdown-from-high) must be computed
on the adjusted series returned here, never on raw `daily_price` directly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PriceBar:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


def get_adjusted_prices(conn: sqlite3.Connection, stock_id: str) -> pd.DataFrame:
    """Returns a DataFrame indexed by date with adjusted open/high/low/close
    and raw volume, sorted ascending by date."""
    raw = pd.read_sql_query(
        "SELECT date, open, high, low, close, volume FROM daily_price "
        "WHERE stock_id = ? ORDER BY date ASC",
        conn,
        params=(stock_id,),
    )
    if raw.empty:
        return raw

    actions = pd.read_sql_query(
        "SELECT ex_date, adj_factor FROM corporate_action "
        "WHERE stock_id = ? ORDER BY ex_date ASC",
        conn,
        params=(stock_id,),
    )

    raw = raw.set_index("date")
    multiplier = pd.Series(1.0, index=raw.index)

    for _, action in actions.iterrows():
        ex_date = action["ex_date"]
        factor = action["adj_factor"]
        mask = raw.index < ex_date
        multiplier.loc[mask] *= factor

    adjusted = raw.copy()
    for col in ("open", "high", "low", "close"):
        adjusted[col] = raw[col] * multiplier

    adjusted = adjusted.reset_index()
    return adjusted


def compute_adj_factor(prev_close: float, reference_price: float) -> float:
    if reference_price <= 0:
        raise ValueError(f"reference_price 必須為正數，收到 {reference_price}")
    return prev_close / reference_price


def upsert_corporate_action(
    conn: sqlite3.Connection,
    stock_id: str,
    ex_date: str,
    reference_price: float,
    prev_close: float,
    source: str,
) -> None:
    adj_factor = compute_adj_factor(prev_close, reference_price)
    conn.execute(
        """
        INSERT INTO corporate_action
            (stock_id, ex_date, reference_price, prev_close, adj_factor, source)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_id, ex_date) DO UPDATE SET
            reference_price = excluded.reference_price,
            prev_close = excluded.prev_close,
            adj_factor = excluded.adj_factor,
            source = excluded.source
        """,
        (stock_id, ex_date, reference_price, prev_close, adj_factor, source),
    )
