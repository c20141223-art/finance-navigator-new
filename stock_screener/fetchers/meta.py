"""Stock basic info (name / market / industry) from the ISIN公告 pages.
Big5-encoded HTML table. UNVERIFIED against a live response."""

from __future__ import annotations

import io

import pandas as pd

from stock_screener.config import SourcesConfig
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError


def fetch_isin_raw(client: RateLimitedClient, config: SourcesConfig, market: str) -> RequestOutcome:
    """market: 'listed' (上市) or 'otc' (上櫃)."""
    url = config.url(f"isin_{market}")
    return client.get(url)


def parse_isin(content: bytes, market_label: str, source: str) -> list[dict]:
    tables = pd.read_html(io.BytesIO(content), encoding="big5")
    if not tables:
        raise SchemaMismatchError(source, expected={"至少一個表格"}, actual=set())
    target = max(tables, key=len)  # the listing table is by far the largest
    cols = [str(c) for c in target.columns]

    # The ISIN page's first column typically packs "代號　名稱" together
    # separated by an ideographic space (U+3000).
    code_name_col = cols[0]
    industry_col = None
    for c in cols:
        if "產業別" in c:
            industry_col = c
            break
    if industry_col is None:
        raise SchemaMismatchError(source, expected={"產業別 欄位"}, actual=set(cols))

    rows = []
    for _, row in target.iterrows():
        raw = str(row[code_name_col])
        parts = raw.split("　")
        if len(parts) < 2:
            continue  # section header rows (e.g. "股票")
        stock_id, name = parts[0].strip(), parts[1].strip()
        if not stock_id.isalnum() or len(stock_id) > 6:
            continue
        rows.append({
            "stock_id": stock_id,
            "name": name,
            "market": market_label,
            "industry": str(row[industry_col]).strip() or None,
        })
    return rows
