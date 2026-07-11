"""Date helpers for Taiwan data sources (ROC calendar, trading-day lists)."""

from __future__ import annotations

import datetime as dt


def to_roc_date(date: dt.date, sep: str = "/") -> str:
    """2024-03-05 -> '113/03/05'"""
    roc_year = date.year - 1911
    return f"{roc_year}{sep}{date.month:02d}{sep}{date.day:02d}"


def to_roc_year_month(date: dt.date) -> tuple[str, str]:
    """Returns (roc_year, month) as zero-padded strings, e.g. ('113', '03')."""
    return str(date.year - 1911), f"{date.month:02d}"


def to_yyyymmdd(date: dt.date) -> str:
    return date.strftime("%Y%m%d")


def is_weekend(date: dt.date) -> bool:
    return date.weekday() >= 5


def trading_day_candidates(end_date: dt.date, n_days: int) -> list[dt.date]:
    """Best-effort list of the n_days calendar dates before (and including)
    end_date, excluding weekends. Taiwan public holidays are NOT filtered
    here — fetchers must tolerate a 'no data' response for holidays rather
    than treat it as a source failure. A precise TWSE trading-calendar
    fetch is left for a later phase; this is the "笨但穩" placeholder.
    """
    days: list[dt.date] = []
    cursor = end_date
    while len(days) < n_days:
        if not is_weekend(cursor):
            days.append(cursor)
        cursor -= dt.timedelta(days=1)
    return list(reversed(days))
