"""排雷名單：處置股 / 全額交割股 / 注意股. Combines TWSE + TPEx sources.
TPEx side is HTML (print-view page), UNVERIFIED shape."""

from __future__ import annotations

import datetime as dt
import io

import pandas as pd

from stock_screener.config import SourcesConfig
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError


def fetch_tpex_disposition_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    return client.get(config.url("tpex_disposition"))


def parse_tpex_disposition(content: bytes, date: dt.date, source: str) -> list[dict]:
    tables = pd.read_html(io.BytesIO(content), encoding="utf-8")
    if not tables:
        return []
    target = max(tables, key=len)
    cols = [str(c) for c in target.columns]
    id_col = None
    for c in cols:
        if "代號" in c:
            id_col = c
            break
    if id_col is None:
        raise SchemaMismatchError(source, expected={"代號 欄位"}, actual=set(cols))

    rows = []
    for _, row in target.iterrows():
        stock_id = str(row[id_col]).strip()
        if not stock_id or not stock_id[0].isdigit():
            continue
        rows.append({"stock_id": stock_id, "date": date.isoformat(), "reason": "處置股"})
    return rows
