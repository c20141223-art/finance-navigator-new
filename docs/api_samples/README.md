# API 樣本狀態

**第一輪真實驗證已完成（2026-07-11，透過 GitHub Actions `Verify API
samples` workflow）。** 結果好壞參半：三個來源的欄位格式已確認並修正了
程式碼中原本猜錯的部分，五個來源目前打不通（原因分兩類，見下方），需要
再一輪才能全部確認。**Phase 1 尚未定稿。**

## 逐項結果

| 來源 | 狀態 | 說明 |
|---|---|---|
| `tpex_daily_all` | ✅ 已確認並修正 | 真實欄位是 `SecuritiesCompanyCode`/`CompanyName`/`TradingShares` 等，跟原本假設的 `Code`/`Name`/`TradingVolume` 不同。`stock_screener/fetchers/tpex.py` 已依樣本修正，10,093 筆資料完整解析成功。 |
| `tpex_daily_history` | ⚠️ 部分確認 | 回應格式跟原本假設的舊式 `{"aaData": [[...]]}` **完全不同**——TPEx 官網已改版（2024-10-27），現在回傳 `{"tables": [{"fields": [...], "data": [...]}], "stat": "ok"}`，欄位有名稱可用關鍵字比對（跟 TWSE 的作法一致）。已依此重寫 parser。但當次查詢日 `data` 為空陣列，只確認了欄位「名稱」，尚未確認實際數值 parsing 邏輯（型別、千分位逗號等）。 |
| `twse_institutional`（T86） | ⚠️ 部分確認 | 端點網址與 JSON envelope（`{"stat": ..., "fields": [...], "data": [...]}`）正常運作，**沒有被擋**。但查詢日剛好回「沒有符合條件的資料」，所以三大法人買賣超的實際欄位名稱和數值仍未見過真實範例。 |
| `twse_daily_all` | ❌ 被擋 | `openapi.twse.com.tw` 回傳「因為安全性考量，您所執行的頁面無法呈現」的攔截頁，判斷是該站的機器人防護規則擋下 GitHub Actions runner 的來源 IP。 |
| `isin_listed` / `isin_otc` | ❌ 被擋 | 同上，`isin.twse.com.tw` 也是同一種攔截頁。 |
| `mops_monthly_revenue_sii` / `_otc` | ❌ 被擋 | 同上，`mops.twse.com.tw` 也是。 |
| `twse_daily_history` / `twse_disposition` / `twse_ex_rights` | ❌ HTTP 307 | 同樣在 `www.twse.com.tw` 底下，但跟 T86 不同的攔截方式（307 轉址而非明顯的攔截頁）。同一網域下不同路徑的機器人防護規則顯然不一致。 |
| `tpex_institutional` | ❌ 網址錯誤 | 回應是 TPEx 官網首頁樣板（非 JSON），代表 `tpex_3insti_daily_trade` 這個 openapi 端點名稱本身就是錯的或已改名，不是網路問題。 |
| `tpex_disposition` | ❌ 404 | 舊網址在 TPEx 2024-10-27 改版後已下架。已依網路搜尋結果換成新版網址 `disposal_information.php`（未帶 `_print`），**下一輪需要驗證**。 |

## 已修正的程式碼

- `stock_screener/fetchers/tpex.py`：`parse_daily_all`、`parse_daily_history`
  已依真實樣本重寫。
- `config/sources.yaml`：`tpex_disposition` 改成新版網址（未驗證）。
- `scripts/verify_api_samples.py`：改用「往前推幾個交易日」而非「今天」
  作為 date 參數的探測日期，避免撞到當天報告還沒發布或忘記排除國定假日
  的情況；同時新增抓取 TWSE／TPEx 官方 Swagger 目錄
  （`_twse_openapi_swagger` / `_tpex_openapi_swagger`），下一輪執行後
  可直接查表找到 `tpex_institutional` 的正確端點名稱，不用再用搜尋引擎
  猜。

## 第二輪前已套用的修正（2026-07-12，基於 stock-report 的已驗證模式）

姊妹專案 c20141223-art/stock-report 每天從 GitHub Actions 成功抓取
openapi.twse.com.tw 與 www.twse.com.tw 已數月，證明 TWSE 對 Actions IP
並非全面封鎖——第一輪被擋的關鍵差異在 **headers**。已比對其成功配方並
移植：

1. **User-Agent**：第一輪用的 UA 帶自我識別 URL
   （`+https://github.com/...`，慣例上的 bot 自我介紹格式），改為
   `Mozilla/5.0 (compatible; TaiwanStockScreener/1.0)`（stock-report 用
   同格式的 `StockReport/3.0` 已驗證可通）。
2. **Referer**：所有請求補上指向來源網站本身的 Referer
   （twse→`https://www.twse.com.tw/`，tpex→`https://www.tpex.org.tw/`）。
3. **no-cache + 時間戳破快取**：www.twse.com.tw 的 rwd 系端點加
   `Cache-Control: no-cache, no-store`、`Pragma: no-cache` 與 `_=毫秒`
   參數（stock-report 的實戰配方）。
4. **Redirect 追蹤已排除嫌疑**：已用本機測試伺服器實測 `RateLimitedClient`
   會正常跟隨 307（requests 預設行為），第一輪記錄到的 HTTP 307 是 WAF
   回的終端回應（無有效 Location），不是 client 沒跟 redirect。
5. **verify_api_samples.py 改走 fetcher 本身的 fetch_*_raw**：確保驗證
   時送出的 headers 跟正式 pipeline 完全一致，不再各寫各的。

注意：**MOPS 與 isin.twse.com.tw 沒有已驗證配方**（stock-report 不使用
這兩個網域），目前只是套用同款 headers 當 best effort，第二輪若仍被擋，
需要換資料源策略（如 data.gov.tw 的月營收資料集）而非繼續調 headers。

## 第二輪觸發後的核對清單

1. **twse 系端點**：headers 修正後是否全數打通（特別是第一輪被擋的
   `twse_daily_all`／`twse_daily_history`／`twse_disposition`／
   `twse_ex_rights`）。
2. **`tpex_institutional`**：讀 `_tpex_openapi_swagger.json`，找三大法人
   買賣超相關的端點路徑，更新 `config/sources.yaml`。
3. **`tpex_disposition`**：確認新網址 `disposal_information.php` 回傳的
   HTML 表格結構是否跟 `risk_list.parse_tpex_disposition` 的假設相符。
4. **`twse_institutional`**：探測日已改為往前推 3 個交易日，確認抓到有
   資料的回應後，核對 `parse_institutional` 假設的欄位名稱。
5. **`tpex_daily_history`**：同上，確認有資料列後核對數值層級的 parsing。
6. **MOPS / isin**：若仍被擋，討論替代資料源。

若 headers 修正後 twse 系端點仍被擋，備用假設是 **執行時段**：
stock-report 固定在台灣清晨 06:30 執行，WAF 對不同時段的容忍度可能
不同，可實驗把 verify 排在同時段。
