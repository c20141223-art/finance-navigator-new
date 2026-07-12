"""Stock basic info (name / market / industry) from the ISIN公告 pages.
Big5-encoded HTML table. UNVERIFIED against a live response."""

from __future__ import annotations

import io

import pandas as pd

from stock_screener.config import SourcesConfig
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError

# isin.twse.com.tw has no proven header recipe either (see mops.py) —
# same best-effort profile.
REQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Referer": "https://isin.twse.com.tw/",
}


def fetch_isin_raw(client: RateLimitedClient, config: SourcesConfig, market: str) -> RequestOutcome:
    """market: 'listed' (上市) or 'otc' (上櫃)."""
    url = config.url(f"isin_{market}")
    return client.get(url, headers=REQ_HEADERS)


def parse_isin(content: bytes, market_label: str, source: str) -> list[dict]:
    # Round-two live sample decodes as UTF-8 (the site's legacy Big5 days
    # are over), but keep Big5 as fallback in case some variant still
    # serves it. The page's ancient markup also defeats lxml — pandas
    # falls back to the bs4/html5lib parser, hence those requirements.
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")
    tables = pd.read_html(io.StringIO(text))
    if not tables:
        raise SchemaMismatchError(source, expected={"至少一個表格"}, actual=set())
    target = max(tables, key=len)  # the listing table is by far the largest

    # The page's markup predates <th>: pandas reads the header as data
    # row 0 ('有價證券代號及名稱', '產業別', ...). Promote it to header.
    header = [str(v) for v in target.iloc[0].tolist()]
    target = target.iloc[1:]
    target.columns = header

    code_name_col = next((c for c in header if "代號及名稱" in c), None)
    industry_col = next((c for c in header if "產業別" in c), None)
    if code_name_col is None or industry_col is None:
        raise SchemaMismatchError(
            source, expected={"有價證券代號及名稱", "產業別"}, actual=set(header)
        )

    rows = []
    for _, row in target.iterrows():
        # "1101　台泥" — code and name packed together, separated by an
        # ideographic space (U+3000). Section rows (e.g. "股票") lack it.
        raw = str(row[code_name_col])
        parts = raw.split("　")
        if len(parts) < 2:
            continue
        stock_id, name = parts[0].strip(), parts[1].strip()
        if not stock_id.isalnum() or len(stock_id) > 6:
            continue
        industry = str(row[industry_col]).strip()
        rows.append({
            "stock_id": stock_id,
            "name": name,
            "market": market_label,
            "industry": industry if industry and industry != "nan" else None,
        })
    return rows
