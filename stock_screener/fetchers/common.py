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
    """MI_INDEX-style responses bundle several fields{n}/data{n} table pairs.
    Find the one that looks like the per-stock closing-price table (has a
    column that mentions 證券代號 and one that mentions 收盤價)."""
    n = 1
    seen_field_sets: list[list[str]] = []
    while True:
        fields_key = f"fields{n}"
        data_key = f"data{n}"
        if fields_key not in payload:
            break
        fields = payload.get(fields_key) or []
        data = payload.get(data_key) or []
        seen_field_sets.append(fields)
        normalized = [normalize_header(f) for f in fields]
        has_id = any("證券代號" in f or "股票代號" in f for f in normalized)
        has_close = any("收盤價" in f for f in normalized)
        if has_id and has_close:
            return fields, data
        n += 1

    raise SchemaMismatchError(
        source,
        expected={"一組同時包含 證券代號 與 收盤價 欄位的 fields/data 表"},
        actual={str(f) for fs in seen_field_sets for f in fs},
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
