#!/usr/bin/env python3
"""Run the reversal radar over the DB for one date and write a markdown
report to docs/screen_reports/. The headline number is the trigger COUNT:
the acceptance check is "does the whole market trigger a sane handful, or
dozens?" — dozens/day means the conditions are too loose. Each trigger row
carries its state, invalidation price, which of the 3 signals fired, and
the stage gates, plus the embedded reversal.yaml for re-verification.

Usage:
    python scripts/export_reversal_report.py [--date 2026-07-14] [--db-path ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener import db, reversal
from stock_screener.scoring import load_momentum_config

REPORTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "screen_reports"


def _sig_summary(signals: dict) -> str:
    names = {"volume_breakout": "帶量突破", "macd_converge": "MACD", "rsi_cross": "RSI"}
    fired = [names[k] for k, v in signals.items() if v.get("fired")]
    return "+".join(fired) if fired else "—"


def build_markdown(result: reversal.RadarResult, config_text: str) -> str:
    n_trig = len(result.triggers)
    lines = [
        f"# 反轉雷達報告 {result.date}",
        "",
        "> ⚠️ **高風險清單**。反轉為跌深後的搏底訊號，工具只呈現客觀狀態與",
        "> 證據，不構成任何買賣建議。",
        "",
        f"- 篩選日（實際交易日）: **{result.date}**",
        f"- config 版本 hash: `{result.config_version}`",
        f"- 通過排雷濾網（資格母體）: {result.qualified} 檔",
        f"- **本日觸發: {n_trig} 檔**　對照組（只差一個條件）: {len(result.control_group)} 檔",
        "",
        "## 觸發頻率健檢",
        "",
        f"全市場單日觸發 **{n_trig}** 檔。反轉是跌深轉強的少數事件——單日個位數"
        "至十餘檔屬合理，若動輒數十檔代表條件過鬆，需檢討 config（依版本紀律，"
        "調參需 ≥ 30 個觸發案例證據）。",
        "",
        "## 覆算與欄位說明",
        "",
        "觸發 = 通過排雷濾網 ∧ 跌深(距60日高≥25%) ∧ 打底(10日不破低＋量縮) ∧ "
        "轉強(帶量突破/MACD/RSI 三選二) ∧ K棒品質>0.6。全部以還原股價計算，",
        "RSI 用威爾德平滑、MACD 12-26-9。各窗口與訊號定義見 "
        "`stock_screener/reversal.py` 模組 docstring。",
        "失效判定價 = 觸發日 K 棒最低點（還原價），收盤跌破即訊號失敗。",
        "",
        "### 觸發清單",
        "",
    ]
    if result.triggers:
        lines += [
            "| 代號 | 名稱 | 狀態 | 失效判定價 | 觸發訊號 | 回檔% | K棒品質 | 三訊號明細 |",
            "|:--|:--|:--|--:|:--|--:|--:|:--|",
        ]
        for t in result.triggers:
            d = t["detail"]
            sig_detail = "; ".join(
                f"{k}={'✓' if v.get('fired') else '✗'}" for k, v in t["signals"].items()
            )
            lines.append(
                f"| {t['stock_id']} | {(t['name'] or '')[:8]} | {t['state']} "
                f"| {t['invalidation_price']} | {_sig_summary(t['signals'])} "
                f"| {d['drawdown'] * 100:.1f} | {d['candle_quality']:.2f} | {sig_detail} |"
            )
    else:
        lines.append("_本日無反轉觸發。_")

    lines += ["", "### 對照組（只差一個條件未過）", ""]
    if result.control_group:
        lines += ["| 代號 | 名稱 | 未過條件 | 回檔% |", "|:--|:--|:--|--:|"]
        for t in result.control_group[:50]:
            lines.append(
                f"| {t['stock_id']} | {(t['name'] or '')[:8]} | {t['failed_gate']} "
                f"| {t['detail']['drawdown'] * 100:.1f} |"
            )
        if len(result.control_group) > 50:
            lines.append(f"| … | 其餘 {len(result.control_group) - 50} 檔略 | | |")
    else:
        lines.append("_無對照組。_")

    lines += ["", "## 本次使用的 reversal.yaml（完整內容）", "", "```yaml",
              config_text.rstrip(), "```", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--db-path", type=str, default=str(db.DEFAULT_DB_PATH))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rcfg, version = reversal.load_reversal_config()
    mcfg = load_momentum_config()
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    with db.get_conn(args.db_path) as conn:
        result = reversal.run_reversal_scan(conn, rcfg, mcfg, date, version)
        reversal.persist_reversal(conn, result)

    config_text = Path(reversal.DEFAULT_REVERSAL_CONFIG_PATH).read_text(encoding="utf-8")
    markdown = build_markdown(result, config_text)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"reversal_{result.date}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"Report written: {out_path} ({len(result.triggers)} triggers, "
          f"{len(result.control_group)} control)")


if __name__ == "__main__":
    main()
