#!/usr/bin/env python3
"""Fetches ONE real sample from every data source in config/sources.yaml and
saves the raw response to docs/api_samples/. Run this in an environment
with real network access (GitHub Actions runner, or your own machine) — the
sandbox this project was originally scaffolded in cannot reach twse.com.tw /
tpex.org.tw / mops.twse.com.tw at all (egress allowlist), so every endpoint
in config/sources.yaml is UNVERIFIED until this has been run once.

This script does NOT validate or parse anything against the fetchers'
expected schema — it just captures ground truth. After running it:
  1. Open each file in docs/api_samples/ and compare its actual field names
     against what stock_screener/fetchers/*.py expects.
  2. Fix any mismatches in the fetchers.
  3. Re-run this script's twin, the actual fetcher unit tests, against the
     saved samples (see tests/fixtures/ + tests/test_fetchers_parse.py).

Usage:
    python scripts/verify_api_samples.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener.config import load_config
from stock_screener.dateutil_tw import to_roc_date, to_roc_year_month, to_yyyymmdd
from stock_screener.http_client import RateLimitedClient

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "docs" / "api_samples"


def save(name: str, outcome) -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    status = "OK" if outcome.ok else f"FAILED ({outcome.error})"
    print(f"[{name}] {status}")
    if not outcome.ok:
        (SAMPLES_DIR / f"{name}.error.txt").write_text(str(outcome.error), encoding="utf-8")
        return
    suffix = ".json" if (outcome.text or "").lstrip().startswith(("{", "[")) else ".html"
    out_path = SAMPLES_DIR / f"{name}{suffix}"
    if outcome.text is not None:
        out_path.write_text(outcome.text, encoding="utf-8", errors="replace")
    else:
        out_path.write_bytes(outcome.content or b"")


def main() -> None:
    config = load_config()
    client = RateLimitedClient(config.http)

    today = dt.date.today()
    # pick a recent weekday as a plausible trading day for date-parameterized endpoints
    probe_date = today
    while probe_date.weekday() >= 5:
        probe_date -= dt.timedelta(days=1)

    save("twse_daily_all", client.get(config.url("twse_daily_all")))
    save(
        "twse_daily_history",
        client.get(config.url("twse_daily_history").format(date=to_yyyymmdd(probe_date))),
    )
    save("tpex_daily_all", client.get(config.url("tpex_daily_all")))
    save(
        "tpex_daily_history",
        client.get(config.url("tpex_daily_history").format(roc_date=to_roc_date(probe_date))),
    )
    save(
        "twse_institutional",
        client.get(config.url("twse_institutional").format(date=to_yyyymmdd(probe_date))),
    )
    save("tpex_institutional", client.get(config.url("tpex_institutional")))
    save(
        "twse_ex_rights",
        client.get(config.url("twse_ex_rights").format(date=to_yyyymmdd(probe_date))),
    )
    save(
        "twse_disposition",
        client.get(config.url("twse_disposition"), params={"date": to_yyyymmdd(probe_date), "response": "json"}),
    )
    save("tpex_disposition", client.get(config.url("tpex_disposition")))
    save("isin_listed", client.get(config.url("isin_listed")))
    save("isin_otc", client.get(config.url("isin_otc")))

    roc_year, month = to_roc_year_month(probe_date.replace(day=1) - dt.timedelta(days=1))
    save(
        "mops_monthly_revenue_sii",
        client.get(config.url("mops_monthly_revenue_sii").format(roc_year=roc_year, month=month)),
    )
    save(
        "mops_monthly_revenue_otc",
        client.get(config.url("mops_monthly_revenue_otc").format(roc_year=roc_year, month=month)),
    )

    print(f"\nSamples written to {SAMPLES_DIR}")


if __name__ == "__main__":
    main()
