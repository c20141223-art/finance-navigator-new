"""排雷名單：TPEx 處置股. TWSE side lives in fetchers/twse.py.

Round-two verification found the old print-view HTML page 404s (TPEx's
2024-10-27 site relaunch) and the replacement page renders its table via
JS (no <table> in raw HTML). TPEx's swagger catalog
(docs/api_samples/_tpex_openapi_swagger.json) exposes the same report as
proper JSON at /tpex_disposal_information with properties: Date,
SecuritiesCompanyCode, CompanyName, DispositionPeriod, DispositionReasons,
DisposalCondition. Schema-verified only — no live sample captured yet.
"""

from __future__ import annotations

import datetime as dt
import json

from stock_screener.config import SourcesConfig
from stock_screener.dateutil_tw import parse_roc_date
from stock_screener.fetchers.tpex import REQ_HEADERS as TPEX_HEADERS
from stock_screener.http_client import RateLimitedClient, RequestOutcome
from stock_screener.schema_guard import SchemaMismatchError


def fetch_tpex_disposition_raw(
    client: RateLimitedClient, config: SourcesConfig, date: dt.date
) -> RequestOutcome:
    return client.get(config.url("tpex_disposition"), headers=TPEX_HEADERS)


def parse_tpex_disposition(raw_text: str, date: dt.date, source: str) -> list[dict]:
    """Same period-range semantics as the TWSE side: a stock counts as
    disposed for every day inside DispositionPeriod. Unparsable periods
    include the stock anyway — over-flagging beats letting a disposed
    stock through the 排雷 filter."""
    payload = json.loads(raw_text)
    if not isinstance(payload, list):
        raise SchemaMismatchError(source, expected={"<list>"}, actual={type(payload).__name__})

    required = {"SecuritiesCompanyCode", "DispositionPeriod"}
    if payload:
        actual = set(payload[0].keys())
        if not required.issubset(actual):
            raise SchemaMismatchError(source, expected=required, actual=actual)

    rows = []
    for rec in payload:
        stock_id = str(rec["SecuritiesCompanyCode"]).strip()
        if not stock_id:
            continue
        period = str(rec.get("DispositionPeriod") or "")
        bounds = [parse_roc_date(p) for p in period.replace("~", "～").split("～")]
        if len(bounds) == 2 and bounds[0] and bounds[1]:
            if not (bounds[0] <= date <= bounds[1]):
                continue
        rows.append({"stock_id": stock_id, "date": date.isoformat(), "reason": "處置股"})
    return rows
