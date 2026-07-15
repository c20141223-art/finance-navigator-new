#!/usr/bin/env python3
"""Run the momentum screen and write a self-contained markdown report to
docs/screen_reports/ — built for independent manual re-verification: the
report embeds the exact momentum.yaml the run used, and every row carries
each factor's raw value and its individual score, so a reviewer can replay
score = bins(raw) → dimension cap → weighted total without touching code.

Usage:
    python scripts/export_screen_report.py [--date 2026-07-14] [--db-path ...]
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_screener import db, momentum
from stock_screener.scoring import DEFAULT_MOMENTUM_CONFIG_PATH, load_momentum_config

REPORTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "screen_reports"


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1 else f"{v:.2f}"
    return str(v)


def _factor_cell(fd: dict) -> str:
    return f"{_fmt(fd['raw'])} → {fd['score']:.0f}"


def build_markdown(result: momentum.ScreenResult, config_text: str, top_n: int) -> str:
    lines = [
        f"# 順勢篩選報告 {result.date}",
        "",
        f"- 篩選日（實際交易日）: **{result.date}**",
        f"- config 版本 hash: `{result.config_version}`",
        f"- 母體 {result.universe_size} 檔 → 通過排雷濾網 {result.passed_filters} 檔"
        f" → 有效評分 {result.all_scored} 檔",
        f"- 大盤標記: 多頭排列佔比 {result.market_regime.get('bullish_alignment_pct')}%"
        f"（母體 {result.market_regime.get('universe')} 檔；加權指數 vs 60MA 為 Phase 4 缺項）",
        "",
        "## 覆算方式",
        "",
        "每格因子以「原始值 → 得分」呈現。對照文末 momentum.yaml：",
        "bins 由上而下第一個命中給分（`gt` 嚴格大於、`gte` 含等於、",
        "`range: [a, b]` 為 a ≤ 值 < b）；每維度兩因子得分相加、超過 100 以",
        "100 計；總分 = 0.30×基本面 + 0.35×籌碼 + 0.35×技術。",
        "原始值的窗口定義見 `stock_screener/momentum.py` 模組 docstring",
        "（60MA 用還原收盤價；量以張計、口徑為官方日成交資料）。",
        "",
        "> **同分檔位排序無鑑別意義**：排名以總分遞減排序，同分時僅以",
        "> 證券代號升冪做確定性 tie-break（讓同一天重跑結果可重現），",
        "> 代號順序不含任何資訊。目前 bins 較粗、維度雙因子封頂容易達標，",
        "> 前段出現同分群為現行參數的特性而非 bug；依回饋機制紀律，參數",
        "> 調整需 ≥ 30 個觸發案例的證據支撐，故不預先調整。",
        "",
        "> **已知資料限制一（上櫃籌碼）**：TPEx 三大法人 openapi 無歷史端",
        "> 點，剛回補完的資料庫中上櫃個股僅有最新一日法人資料，其「投信連",
        "> 買天數」與「外資佔比」原始值忠實反映資料庫現況（連買最多 1、佔",
        "> 比僅含單日），會隨每日排程累積而正常化。上市個股（T86 有歷史）",
        "> 不受影響。",
        ">",
        "> **已知資料限制二（營收趨勢）**：月營收來源為快照端點、無歷史可",
        "> 回補（已查證兩交易所 openapi 目錄內所有端點皆不帶參數），資料庫",
        "> 每月僅累積一個月份。`revenue_trend_3m` 需連續 3 個月資料，系統",
        "> 上線後約 3 個月才開始有意義；在此之前恆為 ✗→0，基本面分項實質",
        "> 上只剩 revenue_yoy 單因子（滿分上限仍為 100，不受影響）。",
        "",
        f"## Top {top_n} + 對照組",
        "",
        "| # | 代號 | 名稱 | 總分 | 基本面 | 籌碼 | 技術 | "
        "營收YoY% | 營收3月趨勢 | 投信連買(日) | 外資佔比5日 | 60MA乖離% | 量能比5/20 | 組別 |",
        "|--:|:--|:--|--:|--:|--:|--:|:--|:--|:--|:--|:--|:--|:--|",
    ]
    for s in result.scores:
        f = {k: v for dim in s.factor_detail.values() for k, v in dim.items()}
        group = "對照" if s.is_control_group else "入選"
        name = (s.name or "").replace("|", "\\|")
        lines.append(
            f"| {s.rank} | {s.stock_id} | {name} | {s.total:.2f} "
            f"| {s.dimension_scores.get('fundamental', 0):.0f} "
            f"| {s.dimension_scores.get('chips', 0):.0f} "
            f"| {s.dimension_scores.get('technical', 0):.0f} "
            f"| {_factor_cell(f['revenue_yoy'])} "
            f"| {_factor_cell(f['revenue_trend_3m'])} "
            f"| {_factor_cell(f['trust_buy_streak'])} "
            f"| {_factor_cell(f['foreign_net_ratio_5d'])} "
            f"| {_factor_cell(f['bias_60ma'])} "
            f"| {_factor_cell(f['volume_expansion'])} "
            f"| {group} |"
        )
    lines += [
        "",
        "## 本次使用的 momentum.yaml（完整內容）",
        "",
        "```yaml",
        config_text.rstrip(),
        "```",
        "",
    ]
    return "\n".join(lines)


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
        momentum.persist_triggers(conn, result)

    config_text = Path(DEFAULT_MOMENTUM_CONFIG_PATH).read_text(encoding="utf-8")
    markdown = build_markdown(result, config_text, mcfg.top_n)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"screen_{result.date}.md"
    out_path.write_text(markdown, encoding="utf-8")
    print(f"Report written: {out_path}")


if __name__ == "__main__":
    main()
