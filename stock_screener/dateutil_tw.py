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


def parse_roc_date(text: str) -> dt.date | None:
    """Parses the ROC-calendar date formats seen in live TWSE/TPEx samples:
    '115/07/03', '115年07月13日', '1150703' (7 digits). Returns None on
    anything unrecognizable rather than raising — callers treat unparsable
    dates defensively."""
    if not text:
        return None
    cleaned = str(text).strip().replace("年", "/").replace("月", "/").replace("日", "")
    try:
        if "/" in cleaned:
            parts = cleaned.split("/")
            if len(parts) != 3:
                return None
            year, month, day = (int(p) for p in parts)
        elif len(cleaned) == 7 and cleaned.isdigit():
            year, month, day = int(cleaned[:3]), int(cleaned[3:5]), int(cleaned[5:7])
        else:
            return None
        return dt.date(year + 1911, month, day)
    except ValueError:
        return None


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
