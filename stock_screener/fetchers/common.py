"""Shared parsing helpers for TWSE/TPEx JSON responses.

These government endpoints have historically returned bulk quote data as
several parallel "fields{n}" / "data{n}" table pairs inside one JSON object
(e.g. MI_INDEX with type=ALLBUT0999 bundles multiple report tables in one
response), and header text has drifted over the years (extra whitespace,
full-width parentheses, wording tweaks). Rather than hardcode a table index
or an exact header string, we search by keyword so a minor header rewording
doesn't silently break parsing — an actual field-name mismatch still raises
SchemaMismatchError instead of producing wrong data.
"""

from __future__ import annotations

import re

from stock_screener.schema_guard import SchemaMismatchError


def normalize_header(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def find_column(
    fields: list[str],
    must_contain: tuple[str, ...],
    must_not_contain: tuple[str, ...] = (),
    *,
    source: str,
) -> int:
    normalized = [normalize_header(f) for f in fields]
    candidates = [
        i
        for i, f in enumerate(normalized)
        if all(kw in f for kw in must_contain)
        and not any(kw in f for kw in must_not_contain)
    ]
    if len(candidates) != 1:
        raise SchemaMismatchError(
            source,
            expected={f"欄位包含{must_contain}且不含{must_not_contain}"},
            actual=set(fields),
        )
    return candidates[0]


def find_price_table(payload: dict, source: str) -> tuple[list[str], list[list]]:
    """Multi-table responses: find the per-stock closing-price table (has a
    column mentioning 證券代號/股票代號 and one mentioning 收盤價).

    Handles both shapes seen in the wild (2026-07 live samples):
    - `{"tables": [{"fields": [...], "data": [...]}, ...]}` — what both
      www.twse.com.tw MI_INDEX and TPEx return after their site revamps.
    - legacy numbered `fields{n}`/`data{n}` pairs — kept as fallback in case
      some endpoint still serves it.
    """
    candidates: list[tuple[list[str], list[list]]] = []
    for table in payload.get("tables") or []:
        candidates.append((table.get("fields") or [], table.get("data") or []))
    n = 1
    while f"fields{n}" in payload:
        candidates.append((payload.get(f"fields{n}") or [], payload.get(f"data{n}") or []))
        n += 1

    for fields, data in candidates:
        normalized = [normalize_header(f) for f in fields]
        has_id = any("證券代號" in f or "股票代號" in f for f in normalized)
        has_close = any("收盤價" in f for f in normalized)
        if has_id and has_close:
            return fields, data

    raise SchemaMismatchError(
        source,
        expected={"一組同時包含 證券代號 與 收盤價 欄位的表格"},
        actual={str(f) for fields, _ in candidates for f in fields},
    )


def to_float(value: str | float | int | None) -> float | None:
    if value in (None, "", "--", "---", "X"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").strip()
    if cleaned in ("", "--", "---", "X"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def to_int(value: str | float | int | None) -> int | None:
    f = to_float(value)
    return None if f is None else int(round(f))
