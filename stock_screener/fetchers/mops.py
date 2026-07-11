"""MOPS (公開資訊觀測站) monthly revenue fetcher.

t21sc03_{roc_year}_{month}_0.html is a plain HTML table (Big5 encoding),
not JSON — this has been the stable public convention for years. We parse
it with pandas.read_html rather than hand-rolling an HTML parser.
UNVERIFIED against a live response; see docs/api_samples/README.md.
"""

from __future__ import annotations

import io

import pandas as pd

from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import to_roc_year_month
from stock_screener.fetchers.common import to_float, to_int
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError

import datetime as dt


def fetch_monthly_revenue_raw(
    client: RateLimitedClient, config: SourcesConfig, month: dt.date, market: str
) -> RequestOutcome:
    """market: 'sii' (上市) or 'otc' (上櫃)."""
    roc_year, mm = to_roc_year_month(month)
    url_key = f"mops_monthly_revenue_{market}"
    url = config.url(url_key).format(roc_year=roc_year, month=mm)
    return client.get(url)


def parse_monthly_revenue(content: bytes, year_month: str, source: str) -> list[dict]:
    tables = pd.read_html(io.BytesIO(content), encoding="big5")
    # The data table is the one with a 公司代號 column and enough columns
    # to hold current/prior-year revenue and YoY%.
    target = None
    for t in tables:
        cols = [str(c) for c in t.columns]
        if any("公司代號" in c for c in cols):
            target = t
            break
    if target is None:
        raise SchemaMismatchError(
            source, expected={"公司代號 欄位"}, actual={str(t.columns.tolist()) for t in tables}
        )

    cols = [str(c) for c in target.columns]

    def find_col(*keywords: str) -> str:
        matches = [c for c in cols if all(k in c for k in keywords)]
        if len(matches) != 1:
            raise SchemaMismatchError(source, expected={f"欄位含{keywords}"}, actual=set(cols))
        return matches[0]

    col_id = find_col("公司代號")
    col_revenue = find_col("營業收入", "當月")
    col_yoy = find_col("去年同月增減")
    col_mom = None
    for c in cols:
        if "上月比較" in c or "上月增減" in c:
            col_mom = c
            break

    rows = []
    for _, row in target.iterrows():
        stock_id = str(row[col_id]).strip()
        if not stock_id or not stock_id.isdigit():
            continue  # skip subtotal / header-repeat rows
        rows.append({
            "stock_id": stock_id,
            "year_month": year_month,
            "revenue": to_int(row[col_revenue]),
            "yoy": to_float(row[col_yoy]),
            "mom": to_float(row[col_mom]) if col_mom else None,
        })
    return rows
