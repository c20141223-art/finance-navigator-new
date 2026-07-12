#!/usr/bin/env python3
"""Fetches ONE real sample from every data source in config/sources.yaml and
saves the raw response to docs/api_samples/. Run it in an environment with
real network access (the "Verify API samples" GitHub Actions workflow, or
your own machine) whenever an endpoint or parser assumption needs
re-confirmation — see docs/api_samples/README.md for the running log of
what each round found.

This script does NOT validate or parse anything against the fetchers'
expected schema — it just captures ground truth. After running it, compare
each file's actual shape against the corresponding parse_* in
stock_screener/fetchers/ and fix any mismatch there.

Usage:
    python scripts/verify_api_samples.py
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener.config import load_config
from stock_screener.dateutil_tw import trading_day_candidates
from stock_screener.fetchers import meta, mops, risk_list, tpex, twse
from stock_screener.http_client import RateLimitedClient

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "docs" / "api_samples"


def save(name: str, outcome) -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    # Remove this source's artifacts from previous rounds first, so a
    # source that flips between ok/error doesn't leave a stale misleading
    # file behind.
    for stale in SAMPLES_DIR.glob(f"{name}.*"):
        stale.unlink()
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
    # Use a date a few trading days back, not "today" — this avoids the
    # false negative where "today" is a real trading day but the report
    # hasn't been published yet at whatever time this workflow happens to
    # run, and avoids unaccounted-for public holidays (trading_day_candidates
    # only skips weekends, not holidays). 3 trading days back is a cheap way
    # to sidestep both without a real trading-calendar lookup.
    probe_date = trading_day_candidates(today, 4)[0]

    # Every source below goes through its fetcher's own fetch_*_raw so the
    # verification exercises the exact header profile production will use —
    # a sample captured with different headers than the pipeline sends would
    # prove nothing about whether the pipeline works.

    # Swagger/OpenAPI catalogs — captured so the *authoritative* endpoint
    # names for institutional/disposition reports can be looked up directly
    # instead of guessed, for the sources still marked UNVERIFIED in
    # config/sources.yaml.
    save(
        "_twse_openapi_swagger",
        client.get("https://openapi.twse.com.tw/v1/swagger.json", headers=twse.REQ_HEADERS),
    )
    save(
        "_tpex_openapi_swagger",
        client.get("https://www.tpex.org.tw/openapi/swagger.json", headers=tpex.REQ_HEADERS),
    )

    save("twse_daily_all", twse.fetch_daily_all_raw(client, config))
    save("twse_daily_history", twse.fetch_daily_history_raw(client, config, probe_date))
    save("tpex_daily_all", tpex.fetch_daily_all_raw(client, config))
    save("tpex_daily_history", tpex.fetch_daily_history_raw(client, config, probe_date))
    save("twse_institutional", twse.fetch_institutional_raw(client, config, probe_date))
    save("tpex_institutional", tpex.fetch_institutional_raw(client, config))
    save("twse_ex_rights", twse.fetch_ex_rights_raw(client, config, probe_date))
    save("twse_disposition", twse.fetch_disposition_raw(client, config, probe_date))
    save("tpex_disposition", risk_list.fetch_tpex_disposition_raw(client, config, probe_date))
    save("isin_listed", meta.fetch_isin_raw(client, config, "listed"))
    save("isin_otc", meta.fetch_isin_raw(client, config, "otc"))

    save("monthly_revenue_sii", mops.fetch_monthly_revenue_raw(client, config, "sii"))
    save("monthly_revenue_otc", mops.fetch_monthly_revenue_raw(client, config, "otc"))

    print(f"\nSamples written to {SAMPLES_DIR}")


if __name__ == "__main__":
    main()
