"""每日 HTML email 報告（規格書第 6 節）. Four blocks, in this order:

  1. 大盤狀態   markers only (加權指數 vs 60MA、60MA 方向、多頭排列佔比)
  2. 順勢 Top N momentum ranking with dimension scores + 新進榜/跌出榜 摘要
  3. 反轉雷達   reversal state-machine list + 失效判定價 + 高風險警語
  4. 觀察池雷達 watchlist objective scores + 排名變化 + 連續墊底警示

Design goals: mobile-readable (single 640px column, large touch-friendly
type, inline styles so Gmail/Outlook render it, a media query for phones),
and self-consistent with the spec's stance — the reversal and watchlist
blocks carry explicit "不構成買賣建議" framing, and any data source that
failed today is surfaced at the top rather than hidden (規格書 9:「缺料日在
報告中明確標注」).

This module only formats; it does no DB work. The caller assembles a
ReportContext (screen result, radar result, watchlist result, market status,
list of failed sources) and gets back (subject, html, text).
"""

from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass, field

from stock_screener.momentum import ScreenResult, StockScore
from stock_screener.reversal import RadarResult
from stock_screener.watchlist import WatchlistResult

# ── palette (inline; email clients ignore external CSS) ──────────────────
INK = "#1a1a2e"
MUTED = "#6b7280"
LINE = "#e5e7eb"
BG = "#f4f5f7"
CARD = "#ffffff"
GOOD = "#0f7b3f"
GOODBG = "#e6f4ea"
WARN = "#b42318"
WARNBG = "#fdecea"
NEUTRAL = "#1d4ed8"
HEAD = "#111827"


@dataclass
class ReportContext:
    date: str
    market_status: dict
    momentum: ScreenResult
    momentum_new: list = field(default_factory=list)      # list[StockScore] new to Top N
    momentum_dropped: list = field(default_factory=list)  # [{stock_id,name,prev_rank}]
    reversal: RadarResult | None = None
    watchlist: WatchlistResult | None = None
    fetch_failures: list = field(default_factory=list)    # [{source, error}]
    momentum_version: str = ""
    reversal_version: str = ""
    top_n: int = 30


def _esc(v) -> str:
    return html.escape("" if v is None else str(v))


def _sign(v, suffix="%") -> str:
    if v is None:
        return "—"
    color = GOOD if v > 0 else WARN if v < 0 else MUTED
    arrow = "▲" if v > 0 else "▼" if v < 0 else "＝"
    return f'<span style="color:{color};font-weight:600">{arrow}{abs(v):.2f}{suffix}</span>'


def _pill(text, fg, bg) -> str:
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
            f'font-size:12px;font-weight:600;color:{fg};background:{bg}">{_esc(text)}</span>')


def _section(title: str, subtitle: str, inner: str) -> str:
    sub = (f'<div style="font-size:13px;color:{MUTED};margin:2px 0 12px">{subtitle}</div>'
           if subtitle else "")
    return f"""
    <tr><td style="padding:20px 18px 8px">
      <div style="font-size:17px;font-weight:700;color:{HEAD}">{title}</div>
      {sub}
      {inner}
    </td></tr>"""


# ── block 1: 大盤狀態 ────────────────────────────────────────────────────

def _market_block(ms: dict) -> str:
    dir_map = {"up": ("↑ 走揚", GOOD, GOODBG), "down": ("↓ 走弱", WARN, WARNBG),
               "flat": ("→ 走平", MUTED, BG), None: ("資料不足", MUTED, BG)}
    if ms.get("index_available"):
        vs = ms.get("index_vs_60ma_pct")
        vs_txt = ("站上" if (vs or 0) > 0 else "跌破" if (vs or 0) < 0 else "貼近") + " 60MA"
        vs_fg, vs_bg = (GOOD, GOODBG) if (vs or 0) > 0 else (WARN, WARNBG) if (vs or 0) < 0 else (MUTED, BG)
        idx_rows = (
            _stat_row("加權指數", f'{ms["index_close"]:.2f}', _pill(vs_txt, vs_fg, vs_bg))
            + _stat_row("指數 vs 60MA", _sign(vs), f'60MA {ms["index_60ma"]:.2f}')
            + _stat_row("60MA 方向", _pill(*dir_map.get(ms.get("ma60_direction"), dir_map[None])), "")
        )
    else:
        idx_rows = _stat_row(
            "加權指數 vs 60MA", _pill("尚無足夠指數資料", MUTED, BG),
            f'（已抓 {ms.get("history_len", 0)} 日，需 60 日；若今日 TAIEX 抓取失敗見下方缺料標注）')
    bull = ms.get("bullish_alignment_pct")
    bull_txt = f"{bull:.2f}%" if bull is not None else "—"
    bull_fg = GOOD if (bull or 0) >= 50 else MUTED
    breadth = _stat_row(
        "全市場多頭排列佔比", f'<span style="color:{bull_fg};font-weight:700">{bull_txt}</span>',
        f'母體 {ms.get("breadth_universe", "—")} 檔（收盤 &gt; 20MA &gt; 60MA，還原價）')
    note = (f'<div style="font-size:12px;color:{MUTED};margin-top:8px">'
            f'大盤狀態僅為客觀標記，不計分、不影響選股與反轉判定。</div>')
    inner = f'<table width="100%" cellpadding="0" cellspacing="0">{idx_rows}{breadth}</table>{note}'
    return _section("① 大盤狀態", "僅標注方向，不作為進出訊號", inner)


def _stat_row(label, value, extra) -> str:
    extra_html = (f'<div style="font-size:12px;color:{MUTED}">{extra}</div>' if extra else "")
    return f"""
      <tr>
        <td style="padding:7px 0;border-bottom:1px solid {LINE};font-size:14px;color:{INK}">{label}</td>
        <td style="padding:7px 0;border-bottom:1px solid {LINE};text-align:right;font-size:14px;color:{INK}">
          {value}{extra_html}
        </td>
      </tr>"""


# ── block 2: 順勢 Top N ──────────────────────────────────────────────────

def _momentum_block(ctx: ReportContext) -> str:
    res = ctx.momentum
    new_ids = {s.stock_id for s in ctx.momentum_new}

    dropped = ""
    if ctx.momentum_dropped:
        items = "、".join(
            f'{_esc(d["stock_id"])} {_esc(d.get("name") or "")}（前 #{d.get("prev_rank")}）'
            for d in ctx.momentum_dropped)
        dropped = (f'<div style="margin:0 0 12px;padding:10px 12px;border-radius:8px;'
                   f'background:{WARNBG};border-left:4px solid {WARN}">'
                   f'<span style="font-weight:700;color:{WARN}">⚠ 跌出榜（{len(ctx.momentum_dropped)}）</span>'
                   f'<div style="font-size:13px;color:{INK};margin-top:4px">{items}</div></div>')

    new_summary = ""
    if new_ids:
        names = "、".join(f'{_esc(s.stock_id)} {_esc(s.name or "")}' for s in ctx.momentum_new)
        new_summary = (f'<div style="margin:0 0 12px;padding:10px 12px;border-radius:8px;'
                       f'background:{GOODBG};border-left:4px solid {GOOD}">'
                       f'<span style="font-weight:700;color:{GOOD}">✦ 新進榜（{len(new_ids)}）</span>'
                       f'<div style="font-size:13px;color:{INK};margin-top:4px">{names}</div></div>')

    header = (f'<tr style="background:{BG}">'
              f'<th style="{_TH}">#</th><th style="{_TH};text-align:left">代號 / 名稱</th>'
              f'<th style="{_TH}">總分</th><th style="{_TH}">基</th>'
              f'<th style="{_TH}">籌</th><th style="{_TH}">技</th></tr>')
    body = []
    for s in res.scores:
        if s.is_control_group:
            continue
        is_new = s.stock_id in new_ids
        name_cell = (f'{_esc(s.stock_id)} <span style="color:{MUTED}">{_esc(s.name or "")}</span>'
                     + (f' {_pill("新", GOOD, GOODBG)}' if is_new else ""))
        rowbg = GOODBG if is_new else (CARD if s.rank % 2 else "#fbfbfd")
        body.append(
            f'<tr style="background:{rowbg}">'
            f'<td style="{_TD};text-align:center;font-weight:600">{s.rank}</td>'
            f'<td style="{_TD}">{name_cell}</td>'
            f'<td style="{_TD};text-align:center;font-weight:700;color:{HEAD}">{s.total:.1f}</td>'
            f'<td style="{_TD};text-align:center">{s.dimension_scores.get("fundamental",0):.0f}</td>'
            f'<td style="{_TD};text-align:center">{s.dimension_scores.get("chips",0):.0f}</td>'
            f'<td style="{_TD};text-align:center">{s.dimension_scores.get("technical",0):.0f}</td>'
            f'</tr>')
    table = (f'<table width="100%" cellpadding="0" cellspacing="0" '
             f'style="border-collapse:collapse;font-size:13px">{header}{"".join(body)}</table>')
    foot = (f'<div style="font-size:12px;color:{MUTED};margin-top:8px">'
            f'基=基本面 籌=籌碼 技=技術（各維度滿分 100，總分 = 0.30/0.35/0.35 加權）。'
            f'母體 {res.universe_size} → 通過排雷 {res.passed_filters} → 評分 {res.all_scored}。'
            f'同分以代號升冪排序、不含鑑別意義。config `{_esc(ctx.momentum_version)}`。</div>')
    inner = new_summary + dropped + table + foot
    return _section(f"② 順勢 Top {ctx.top_n}", "分項得分與進出榜變動", inner)


# ── block 3: 反轉雷達 ────────────────────────────────────────────────────

_STATE_STYLE = {
    "confirmed": ("已確認", GOOD, GOODBG), "confirming": ("確認中", NEUTRAL, "#e8efff"),
    "triggered": ("初次觸發", NEUTRAL, "#e8efff"), "watch": ("觀察", MUTED, BG),
    "failed": ("訊號失敗", WARN, WARNBG),
}
_SIG_NAME = {"volume_breakout": "帶量突破", "macd_converge": "MACD", "rsi_cross": "RSI"}


def _reversal_block(radar: RadarResult | None, version: str) -> str:
    warn = (f'<div style="margin:0 0 12px;padding:10px 12px;border-radius:8px;'
            f'background:{WARNBG};border-left:4px solid {WARN};font-size:13px;color:{INK}">'
            f'<b style="color:{WARN}">⚠ 高風險清單</b>　反轉為跌深後的搏底訊號，'
            f'僅呈現客觀狀態與失效判定價，不構成任何買賣建議。</div>')
    if radar is None:
        return _section("③ 反轉雷達", "止跌打底後的轉強觸發", warn +
                        f'<div style="font-size:13px;color:{MUTED}">本日未執行反轉偵測。</div>')
    n = len(radar.triggers)
    head = (f'<div style="font-size:14px;color:{INK};margin-bottom:10px">'
            f'本日觸發 <b style="color:{HEAD};font-size:16px">{n}</b> 檔'
            f'（資格母體 {radar.qualified} 檔，對照組 {len(radar.control_group)} 檔）。'
            f'單日個位數至十餘檔屬合理，動輒數十檔代表條件過鬆。</div>')
    if not radar.triggers:
        inner = warn + head + f'<div style="font-size:13px;color:{MUTED}">本日無反轉觸發。</div>'
        return _section("③ 反轉雷達", "止跌打底後的轉強觸發", inner)

    header = (f'<tr style="background:{BG}">'
              f'<th style="{_TH};text-align:left">代號 / 名稱</th><th style="{_TH}">狀態</th>'
              f'<th style="{_TH}">失效判定價</th><th style="{_TH}">觸發訊號</th>'
              f'<th style="{_TH}">回檔</th></tr>')
    body = []
    for t in radar.triggers:
        label, fg, bg = _STATE_STYLE.get(t.get("state"), (t.get("state") or "—", MUTED, BG))
        sigs = "＋".join(_SIG_NAME[k] for k, v in t["signals"].items() if v.get("fired")) or "—"
        dd = t["detail"].get("drawdown")
        body.append(
            f'<tr>'
            f'<td style="{_TD}">{_esc(t["stock_id"])} '
            f'<span style="color:{MUTED}">{_esc((t.get("name") or "")[:8])}</span></td>'
            f'<td style="{_TD};text-align:center">{_pill(label, fg, bg)}</td>'
            f'<td style="{_TD};text-align:right;font-weight:600">{_fmt_num(t.get("invalidation_price"))}</td>'
            f'<td style="{_TD};text-align:center;font-size:12px">{sigs}</td>'
            f'<td style="{_TD};text-align:right">{(dd*100):.1f}%</td>'
            f'</tr>')
    table = (f'<table width="100%" cellpadding="0" cellspacing="0" '
             f'style="border-collapse:collapse;font-size:13px">{header}{"".join(body)}</table>')
    foot = (f'<div style="font-size:12px;color:{MUTED};margin-top:8px">'
            f'失效判定價 = 觸發日 K 棒最低點（還原價），收盤跌破即訊號失敗。'
            f'config `{_esc(version)}`。</div>')
    return _section("③ 反轉雷達", "止跌打底後的轉強觸發", warn + head + table + foot)


# ── block 4: 觀察池雷達 ──────────────────────────────────────────────────

def _watchlist_block(wl: WatchlistResult | None) -> str:
    note = (f'<div style="font-size:12px;color:{MUTED};margin-top:8px">'
            f'觀察池僅呈現你追蹤個股的客觀分數與排名變化，不加分、不影響選股。</div>')
    if wl is None or not wl.entries:
        empty = ('尚未設定觀察池，於 <code>config/watchlist.json</code> 的 '
                 '<code>stocks</code> 填入代號即可。' if (wl is None or not wl.entries) else "")
        return _section("④ 觀察池雷達", "客觀分數與排名變化",
                        f'<div style="font-size:13px;color:{MUTED}">{empty}</div>' + note)

    header = (f'<tr style="background:{BG}">'
              f'<th style="{_TH}">名次</th><th style="{_TH};text-align:left">代號 / 名稱</th>'
              f'<th style="{_TH}">分數</th><th style="{_TH}">排名變化</th>'
              f'<th style="{_TH}">狀態</th></tr>')
    body = []
    for e in wl.entries:
        if e.insufficient or e.total is None:
            rank_cell, score_cell = "—", "—"
            change_cell = "—"
            status = _pill("資料不足", MUTED, BG)
        else:
            rank_cell = str(e.rank)
            score_cell = f'<b style="color:{HEAD}">{e.total:.1f}</b>'
            if e.rank_change is None:
                change_cell = _pill("新追蹤", NEUTRAL, "#e8efff")
            elif e.rank_change > 0:
                change_cell = f'<span style="color:{GOOD};font-weight:600">▲{e.rank_change}</span>'
            elif e.rank_change < 0:
                change_cell = f'<span style="color:{WARN};font-weight:600">▼{abs(e.rank_change)}</span>'
            else:
                change_cell = f'<span style="color:{MUTED}">＝</span>'
            status = (_pill("連續墊底", WARN, WARNBG) if e.bottom_alert
                      else (_pill("已過排雷", GOOD, GOODBG) if e.filter_ok
                            else _pill("未過排雷", MUTED, BG)))
        note_html = (f'<div style="font-size:11px;color:{MUTED}">{_esc(e.note)}</div>'
                     if e.note else "")
        body.append(
            f'<tr>'
            f'<td style="{_TD};text-align:center">{rank_cell}</td>'
            f'<td style="{_TD}">{_esc(e.stock_id)} '
            f'<span style="color:{MUTED}">{_esc(e.name or "")}</span>{note_html}</td>'
            f'<td style="{_TD};text-align:center">{score_cell}</td>'
            f'<td style="{_TD};text-align:center">{change_cell}</td>'
            f'<td style="{_TD};text-align:center">{status}</td>'
            f'</tr>')
    table = (f'<table width="100%" cellpadding="0" cellspacing="0" '
             f'style="border-collapse:collapse;font-size:13px">{header}{"".join(body)}</table>')
    return _section("④ 觀察池雷達", "客觀分數與排名變化", table + note)


# ── failed-source banner (spec 9) ────────────────────────────────────────

def _failure_banner(failures: list) -> str:
    if not failures:
        return ""
    items = "".join(
        f'<li style="margin:2px 0">{_esc(f.get("source"))}：{_esc(f.get("error"))}</li>'
        for f in failures)
    return (f'<tr><td style="padding:14px 18px 0">'
            f'<div style="padding:10px 12px;border-radius:8px;background:{WARNBG};'
            f'border-left:4px solid {WARN}">'
            f'<b style="color:{WARN}">⚠ 本日缺料 / 環節失敗（{len(failures)}）</b>'
            f'<ul style="margin:6px 0 0;padding-left:18px;font-size:12px;color:{INK}">{items}</ul>'
            f'<div style="font-size:12px;color:{MUTED};margin-top:6px">'
            f'流程未中斷；受影響區塊的數字可能不完整，已如實標注。</div></div></td></tr>')


# ── assembly ─────────────────────────────────────────────────────────────

_TH = f"padding:8px 6px;font-size:12px;color:{MUTED};font-weight:600;border-bottom:2px solid {LINE}"
_TD = f"padding:8px 6px;border-bottom:1px solid {LINE};color:{INK};vertical-align:top"


def _fmt_num(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def build_report(ctx: ReportContext) -> tuple[str, str, str]:
    """Returns (subject, html, text)."""
    n_trig = len(ctx.reversal.triggers) if ctx.reversal else 0
    subject = (f"[台股選股] {ctx.date}　順勢Top{ctx.top_n}"
               f"｜反轉{n_trig}檔"
               + ("｜⚠缺料" if ctx.fetch_failures else ""))

    blocks = (
        _market_block(ctx.market_status)
        + _momentum_block(ctx)
        + _reversal_block(ctx.reversal, ctx.reversal_version)
        + _watchlist_block(ctx.watchlist)
    )
    generated = dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  @media only screen and (max-width:620px) {{
    .wrap {{ width:100% !important; }}
    .pad {{ padding-left:12px !important; padding-right:12px !important; }}
    th, td {{ font-size:12px !important; }}
  }}
  body {{ margin:0; padding:0; background:{BG}; }}
</style></head>
<body style="margin:0;padding:0;background:{BG};
  font-family:-apple-system,'Segoe UI','Noto Sans TC',Roboto,Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG}">
<tr><td align="center" style="padding:18px 10px">
  <table role="presentation" class="wrap" width="640" cellpadding="0" cellspacing="0"
    style="width:640px;max-width:640px;background:{CARD};border-radius:14px;overflow:hidden;
    box-shadow:0 1px 4px rgba(0,0,0,0.08)">
    <tr><td style="background:{HEAD};padding:22px 18px">
      <div style="color:#fff;font-size:20px;font-weight:800">台股選股工具 · 每日報告</div>
      <div style="color:#c7cbd1;font-size:13px;margin-top:4px">
        交易日 {ctx.date}　·　大盤／順勢／反轉／觀察池</div>
    </td></tr>
    {_failure_banner(ctx.fetch_failures)}
    {blocks}
    <tr><td style="padding:16px 18px 24px" class="pad">
      <div style="border-top:1px solid {LINE};padding-top:12px;font-size:12px;color:{MUTED};line-height:1.6">
        本報告為個人量化研究工具的自動輸出，僅呈現客觀數據與規則觸發狀態，
        <b>不構成任何投資建議或買賣邀約</b>；反轉與觀察池為高風險資訊，請自行判斷並自負風險。<br>
        產生時間 {generated}　·　momentum <code>{_esc(ctx.momentum_version)}</code>
        · reversal <code>{_esc(ctx.reversal_version)}</code>
      </div>
    </td></tr>
  </table>
</td></tr></table></body></html>"""

    text = _text_summary(ctx, n_trig)
    return subject, html_doc, text


def _text_summary(ctx: ReportContext, n_trig: int) -> str:
    ms = ctx.market_status
    lines = [f"台股選股工具 每日報告 — {ctx.date}", ""]
    if ctx.fetch_failures:
        lines.append(f"[缺料/失敗 {len(ctx.fetch_failures)}] "
                     + "; ".join(f"{f.get('source')}" for f in ctx.fetch_failures))
    if ms.get("index_available"):
        lines.append(f"大盤: 加權指數 {ms['index_close']:.2f}, vs60MA "
                     f"{ms.get('index_vs_60ma_pct')}%, 60MA {ms.get('ma60_direction')}, "
                     f"多頭排列 {ms.get('bullish_alignment_pct')}%")
    else:
        lines.append(f"大盤: 指數資料不足; 多頭排列 {ms.get('bullish_alignment_pct')}%")
    lines.append("")
    lines.append(f"順勢 Top {ctx.top_n}:")
    for s in ctx.momentum.scores:
        if s.is_control_group:
            continue
        lines.append(f"  #{s.rank} {s.stock_id} {s.name or ''} 總分{s.total:.1f}")
    if ctx.momentum_dropped:
        lines.append("  跌出榜: " + ", ".join(d["stock_id"] for d in ctx.momentum_dropped))
    lines.append("")
    lines.append(f"反轉雷達: 觸發 {n_trig} 檔")
    if ctx.reversal:
        for t in ctx.reversal.triggers:
            lines.append(f"  {t['stock_id']} {t.get('state')} 失效價{_fmt_num(t.get('invalidation_price'))}")
    lines.append("")
    if ctx.watchlist and ctx.watchlist.entries:
        lines.append("觀察池:")
        for e in ctx.watchlist.entries:
            if e.total is None:
                lines.append(f"  {e.stock_id} 資料不足")
            else:
                lines.append(f"  #{e.rank} {e.stock_id} 分數{e.total:.1f}"
                             + (" [連續墊底]" if e.bottom_alert else ""))
    lines.append("")
    lines.append("本報告不構成投資建議，風險自負。")
    return "\n".join(lines)
