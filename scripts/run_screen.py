#!/usr/bin/env python3
"""Run the momentum screen (排雷濾網 + 順勢評分) for one date and persist
Top-N plus the control group to `triggers`. Prints the full ranked table
with per-dimension scores and every factor's raw value so any row can be
re-derived by hand against config/momentum.yaml.

Usage:
    python scripts/run_screen.py [--date 2026-07-11] [--db-path data/market.db]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener import db, momentum
from stock_screener.scoring import load_momentum_config


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD, defaults to today")
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    mcfg = load_momentum_config()
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    with db.get_conn(args.db_path) as conn:
        result = momentum.run_screen(conn, mcfg, date)
        momentum.record_config_version(conn, mcfg, result.date)
        n = momentum.persist_triggers(conn, result)

    print(f"\n篩選日: {result.date}  config: {result.config_version}")
    print(f"母體 {result.universe_size} 檔 → 通過排雷濾網 {result.passed_filters} 檔"
          f" → 落庫 {n} 筆（Top {mcfg.top_n} + 對照組）")
    print(f"大盤標記: {result.market_regime}")

    header = (f"{'#':>3} {'代號':<6} {'名稱':<8} {'總分':>7} "
              f"{'基本面':>6} {'籌碼':>6} {'技術':>6}  因子原始值")
    print("\n" + header)
    print("-" * 110)
    for s in result.scores:
        tag = " (對照)" if s.is_control_group else ""
        raws = []
        for dim_detail in s.factor_detail.values():
            for fname, fd in dim_detail.items():
                raws.append(f"{fname}={_fmt(fd['raw'])}")
        dims = s.dimension_scores
        print(f"{s.rank:>3} {s.stock_id:<6} {(s.name or '')[:8]:<8} {s.total:>7.2f} "
              f"{dims.get('fundamental', 0):>6.0f} {dims.get('chips', 0):>6.0f} "
              f"{dims.get('technical', 0):>6.0f}  {' '.join(raws)}{tag}")


if __name__ == "__main__":
    main()
