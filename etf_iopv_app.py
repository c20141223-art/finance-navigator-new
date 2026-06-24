"""
台股 ETF 即時估計淨值計算工具 (IOPV Calculator)
Data sources: Taiwan Stock Exchange (TWSE) APIs
"""

import re
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="台股 ETF 即時估計淨值",
    page_icon="📊",
    layout="wide",
)

# ─────────────────────────────────────────────────────────────────────────────
# HTTP headers — TWSE requires a matching Referer
# ─────────────────────────────────────────────────────────────────────────────

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_TWSE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
    "Referer": "https://www.twse.com.tw/",
}
_MIS_HEADERS = {**_TWSE_HEADERS, "Referer": "https://mis.twse.com.tw/"}



# ─────────────────────────────────────────────────────────────────────────────
# API — ETF Component Stocks
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_etf_components(
    etf_code: str,
) -> tuple[Optional[pd.DataFrame], Optional[float], str]:
    """
    Fetch ETF component stocks from TWSE ETFund API.
    Response JSON fields (example for 0050):
      stat, title, fields, data (list of rows), notes
    Returns (df, units_outstanding, etf_title).  Cached 5 min.
    """
    resp = requests.get(
        "https://www.twse.com.tw/fund/ETFund",
        params={
            "response": "json",
            "stockNo": etf_code.strip(),
            "_": int(time.time() * 1000),  # cache-bust
        },
        headers=_TWSE_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    js = resp.json()

    if js.get("stat") != "OK":
        raise ValueError(f"證交所回傳：{js.get('stat', '未知錯誤')}")

    fields: list[str] = js.get("fields", [])
    rows: list[list] = js.get("data", [])
    title: str = js.get("title", etf_code)

    if not rows:
        raise ValueError(f"{etf_code} 無成分股資料（請確認代號正確）")

    df = pd.DataFrame(rows, columns=fields)

    # Map varying column names to standard keys
    rename: dict[str, str] = {}
    for col in df.columns:
        s = re.sub(r"[（）()\s元%％]", "", col)
        if re.search(r"代號|代碼", s):
            rename[col] = "code"
        elif re.search(r"名稱", s):
            rename[col] = "name"
        elif re.search(r"股數", s):
            rename[col] = "shares"
        elif re.search(r"市值", s):
            rename[col] = "mkt_val"
        elif re.search(r"比例|比重|佔淨值", s):
            rename[col] = "weight"
    df = df.rename(columns=rename)

    for col in ["shares", "mkt_val", "weight"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", "").str.strip(),
                errors="coerce",
            )

    # Try to extract total units outstanding from notes
    # Typical note: "基金單位總數：3,671,000,000"
    units: Optional[float] = None
    for note in js.get("notes", []):
        m = re.search(r"單位[數總]*\s*[：:]\s*([\d,]+)", str(note))
        if m:
            units = float(m.group(1).replace(",", ""))
            break

    return df, units, title


# ─────────────────────────────────────────────────────────────────────────────
# API — Real-time Stock Prices (MIS)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_prices(codes: list[str]) -> dict[str, dict]:
    """
    Fetch real-time prices from TWSE MIS API.
    Queries TSE + OTC simultaneously; MIS returns data only for matching codes.

    MIS response fields used:
      c  = stock code
      n  = short Chinese name
      z  = last trade price (最近成交價); "-" when no trades yet
      y  = previous close (前日收盤價); used as fallback

    Returns {code: {price, name, is_prev_close}}.
    """
    if not codes:
        return {}

    result: dict[str, dict] = {}

    for i in range(0, len(codes), 100):
        chunk = codes[i : i + 100]
        tse = "|".join(f"tse_{c}.tw" for c in chunk)
        otc = "|".join(f"otc_{c}.tw" for c in chunk)

        try:
            resp = requests.get(
                "https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                params={"ex_ch": f"{tse}|{otc}", "json": "1", "delay": "0"},
                headers=_MIS_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()

            for item in resp.json().get("msgArray", []):
                code = item.get("c", "").strip()
                if not code or code in result:
                    continue

                name = item.get("n", code)
                z = item.get("z", "-")  # last trade
                y = item.get("y", "-")  # previous close

                if z not in ("-", "", None):
                    try:
                        result[code] = {
                            "price": float(z),
                            "name": name,
                            "is_prev_close": False,
                        }
                    except ValueError:
                        pass
                elif y not in ("-", "", None):
                    try:
                        result[code] = {
                            "price": float(y),
                            "name": name,
                            "is_prev_close": True,
                        }
                    except ValueError:
                        pass

        except Exception as exc:
            st.warning(f"行情批次 {i // 100 + 1} 取得失敗：{exc}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# IOPV Calculation
# ─────────────────────────────────────────────────────────────────────────────

def compute_iopv(etf_code: str) -> dict:
    """
    Compute IOPV for one ETF.
    Formula: Σ(component_real_time_price × shares_held) / units_outstanding
    """
    r: dict = {
        "code": etf_code,
        "name": etf_code,
        "iopv": None,
        "mkt_price": None,
        "premium_pct": None,
        "portfolio_val": None,
        "units": None,
        "components": None,
        "rt_coverage": None,  # % of components with live price
        "error": None,
        "ts": datetime.now().strftime("%H:%M:%S"),
    }

    try:
        df, units, title = fetch_etf_components(etf_code)
        r["name"] = title
        r["units"] = units

        if "code" not in df.columns:
            r["error"] = "無法識別成分股代號欄位（API 格式可能已變更）"
            return r

        stock_codes = df["code"].dropna().astype(str).str.strip().tolist()
        prices = fetch_prices(list(set(stock_codes + [etf_code])))

        # ETF's own market price (same MIS API)
        etf_info = prices.get(etf_code, {})
        r["mkt_price"] = etf_info.get("price")
        if title == etf_code:
            r["name"] = etf_info.get("name", etf_code)

        # Annotate each component with live price and contribution
        comp = df.copy()
        comp["rt_price"] = pd.NA
        comp["price_note"] = ""
        comp["contrib"] = pd.NA

        total_val = 0.0
        rt_count = 0
        has_shares = "shares" in comp.columns

        for idx, row in comp.iterrows():
            code = str(row["code"]).strip()
            info = prices.get(code, {})
            price = info.get("price")

            if price is not None:
                comp.at[idx, "rt_price"] = price
                if info.get("is_prev_close"):
                    comp.at[idx, "price_note"] = "前日收盤(估)"
                else:
                    rt_count += 1

                if has_shares and pd.notna(row.get("shares")):
                    val = price * float(row["shares"])
                    comp.at[idx, "contrib"] = val
                    total_val += val
            else:
                comp.at[idx, "price_note"] = "無法取得"

        r["components"] = comp
        r["portfolio_val"] = total_val or None
        r["rt_coverage"] = round(rt_count / len(stock_codes) * 100, 1) if stock_codes else None

        if units and units > 0 and total_val > 0:
            r["iopv"] = total_val / units
            if r["mkt_price"] and r["iopv"]:
                r["premium_pct"] = (r["mkt_price"] / r["iopv"] - 1) * 100

    except Exception as exc:
        r["error"] = str(exc)

    return r


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v, dec: int = 2, na: str = "N/A") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return na
    return f"{v:,.{dec}f}"


def render_result(r: dict) -> None:
    if r["error"]:
        st.error(f"**{r['code']}** 查詢失敗：{r['error']}")
        return

    st.markdown(f"### {r['code']} — {r['name']}")

    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
        st.metric("估計淨值 (IOPV)", _fmt(r["iopv"]))

    with c2:
        st.metric("即時市價", _fmt(r["mkt_price"]))

    with c3:
        pct = r["premium_pct"]
        if pct is not None:
            label = "溢價" if pct > 0 else "折價" if pct < 0 else "平價"
            st.metric("折溢價", f"{pct:+.3f}%", delta=label, delta_color="inverse")
        else:
            st.metric("折溢價", "N/A")

    with c4:
        u = r["units"]
        st.metric("流通單位數", f"{u / 1e8:.2f} 億" if u else "N/A")

    with c5:
        cov = r["rt_coverage"]
        st.metric(
            "即時報價覆蓋",
            f"{cov:.0f}%" if cov is not None else "N/A",
            help="有即時成交價（非前日收盤）的成分股比例",
        )

    if r["portfolio_val"] is not None and r["iopv"] is None:
        st.info(
            f"持股市值合計：**{r['portfolio_val']:,.0f} 元**　"
            f"（未取得流通單位數，無法換算每單位 IOPV）"
        )

    comp = r.get("components")
    if comp is not None and len(comp) > 0:
        with st.expander(f"成分股明細（共 {len(comp)} 檔）", expanded=False):
            disp: dict[str, pd.Series] = {}
            if "code" in comp.columns:
                disp["代號"] = comp["code"]
            if "name" in comp.columns:
                disp["名稱"] = comp["name"]
            if "rt_price" in comp.columns:
                disp["即時股價"] = comp["rt_price"].apply(
                    lambda x: _fmt(x) if pd.notna(x) else "N/A"
                )
            if "shares" in comp.columns:
                disp["持有股數"] = comp["shares"].apply(
                    lambda x: f"{int(x):,}" if pd.notna(x) else "N/A"
                )
            if "contrib" in comp.columns:
                disp["貢獻市值(元)"] = comp["contrib"].apply(
                    lambda x: f"{x:,.0f}" if pd.notna(x) else "N/A"
                )
            if "weight" in comp.columns:
                disp["佔比(%)"] = comp["weight"].apply(
                    lambda x: f"{x:.2f}" if pd.notna(x) else "N/A"
                )
            if "price_note" in comp.columns:
                disp["備注"] = comp["price_note"]

            st.dataframe(pd.DataFrame(disp), use_container_width=True, hide_index=True)

    st.caption(f"最後更新：{r['ts']}")


# ─────────────────────────────────────────────────────────────────────────────
# Main layout
# ─────────────────────────────────────────────────────────────────────────────

st.title("📊 台股 ETF 即時估計淨值")
st.caption(
    "資料來源：台灣證券交易所（TWSE）"
    "　｜　計算公式：Σ（成分股即時股價 × 持有股數）÷ 流通在外單位數"
)
st.markdown("---")

with st.form("query_form"):
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        etf_input = st.text_input(
            "etf_codes",
            value=st.session_state.get("etf_input", "0050"),
            placeholder="輸入 ETF 代號，多檔以逗號分隔，例：0050, 00878, 006208",
            label_visibility="collapsed",
        )
    with col_btn:
        submitted = st.form_submit_button("查 詢", use_container_width=True, type="primary")

auto_refresh = st.checkbox("每 30 秒自動更新", value=False)

# ── Handle manual submit ────────────────────────────────────────────────────
if submitted:
    raw = etf_input.replace("，", ",")
    codes = [c.strip() for c in re.split(r"[,\s]+", raw) if c.strip()]
    if not codes:
        st.warning("請輸入至少一個 ETF 代號")
    else:
        st.session_state["etf_codes"] = codes
        st.session_state["etf_input"] = etf_input
        fetch_etf_components.clear()  # Force fresh component data on manual query
        with st.spinner(f"正在抓取 {', '.join(codes)} 的資料…"):
            st.session_state["results"] = [compute_iopv(c) for c in codes]

# ── Display stored results ──────────────────────────────────────────────────
if "results" in st.session_state:
    for res in st.session_state["results"]:
        render_result(res)
        st.markdown("---")

# ── Auto-refresh countdown (runs after results are displayed) ───────────────
if auto_refresh and "etf_codes" in st.session_state:
    ph = st.empty()
    for remaining in range(30, 0, -1):
        ph.info(f"⏱ 下次自動更新：{remaining} 秒後（取消勾選可停止）")
        time.sleep(1)
    ph.empty()

    codes = st.session_state["etf_codes"]
    with st.spinner("正在更新資料…"):
        st.session_state["results"] = [compute_iopv(c) for c in codes]

    st.rerun()  # Re-render UI with fresh results; countdown restarts if still checked
