"""Phase 4 web output: dashboard JSON shape + file writing (GitHub Pages)."""

import json

from stock_screener import momentum, report_email, web_export
from stock_screener.reversal import RadarResult
from stock_screener.watchlist import WatchlistEntry, WatchlistResult


def _ctx():
    scores = [
        momentum.StockScore("2330", "台積電", 88.0,
                            {"fundamental": 80, "chips": 90, "technical": 95}, {}, rank=1),
        momentum.StockScore("2317", "鴻海", 40.0,
                            {"fundamental": 30, "chips": 50, "technical": 40}, {},
                            rank=45, is_control_group=True),
    ]
    screen = momentum.ScreenResult(
        date="2026-07-07", universe_size=100, passed_filters=40, all_scored=40,
        scores=scores, market_regime={"bullish_alignment_pct": 55.0, "universe": 40},
        config_version="mom123")
    radar = RadarResult(date="2026-07-07", qualified=300,
                        triggers=[{"stock_id": "3008", "name": "大立光",
                                   "state": "confirmed", "invalidation_price": 614.6,
                                   "detail": {"drawdown": 0.32, "candle_quality": 0.79},
                                   "signals": {"volume_breakout": {"fired": True},
                                               "macd_converge": {"fired": False},
                                               "rsi_cross": {"fired": True}}}],
                        control_group=[])
    wl = WatchlistResult(date="2026-07-07", n_stocks=1, entries=[
        WatchlistEntry("2330", "台積電", "核心", 88.0,
                       {"fundamental": 80, "chips": 90, "technical": 95},
                       rank=1, prev_rank=2, rank_change=1, filter_ok=True,
                       reason=None, insufficient=False, bottom_alert=False)])
    return report_email.ReportContext(
        date="2026-07-07",
        market_status={"index_available": True, "index_close": 19000.0,
                       "index_60ma": 18500.0, "index_vs_60ma_pct": 2.7,
                       "ma60_direction": "up", "bullish_alignment_pct": 55.0,
                       "breadth_universe": 40},
        momentum=screen,
        momentum_new=[scores[0]],
        momentum_dropped=[{"stock_id": "1101", "name": "台泥", "prev_rank": 5}],
        reversal=radar, watchlist=wl,
        fetch_failures=[{"source": "tpex_institutional", "error": "HTTP 500"}],
        momentum_version="mom123", reversal_version="rev456", top_n=30)


def test_build_dashboard_data_shape_and_filters_control_group():
    data = web_export.build_dashboard_data(_ctx())
    assert data["schema_version"] == web_export.SCHEMA_VERSION
    assert data["date"] == "2026-07-07"
    assert data["versions"] == {"momentum": "mom123", "reversal": "rev456"}
    # control-group row excluded from the dashboard momentum table
    assert [r["stock_id"] for r in data["momentum"]["rows"]] == ["2330"]
    assert data["momentum"]["rows"][0]["is_new"] is True
    assert data["momentum"]["new_entrants"][0]["stock_id"] == "2330"
    assert data["momentum"]["dropped"][0]["stock_id"] == "1101"
    assert data["reversal"]["trigger_count"] == 1
    t = data["reversal"]["triggers"][0]
    assert t["state"] == "confirmed" and t["signals"]["volume_breakout"] is True
    assert "帶量突破" in t["signal_summary"] and "RSI" in t["signal_summary"]
    assert data["watchlist"]["entries"][0]["rank_change"] == 1
    # JSON must be serializable
    json.dumps(data, ensure_ascii=False)


def test_write_web_output_files_and_index(tmp_path):
    data = web_export.build_dashboard_data(_ctx())
    web_export.write_web_output(tmp_path, data)
    d = tmp_path / "data"
    assert (d / "latest.json").exists()
    assert (d / "2026-07-07.json").exists()
    idx = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert idx["latest"] == "2026-07-07" and "2026-07-07" in idx["dates"]
    # a second, later date accumulates and sorts newest-first
    data2 = dict(data, date="2026-07-08")
    web_export.write_web_output(tmp_path, data2)
    idx2 = json.loads((d / "index.json").read_text(encoding="utf-8"))
    assert idx2["dates"] == ["2026-07-08", "2026-07-07"]
    assert idx2["latest"] == "2026-07-08"
    assert json.loads((d / "latest.json").read_text(encoding="utf-8"))["date"] == "2026-07-08"
