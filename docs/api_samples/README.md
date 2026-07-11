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

## 下一輪待辦

再次手動觸發 `Verify API samples` workflow 後：

1. **`tpex_institutional`**：讀 `_tpex_openapi_swagger.json`，找三大法人
   買賣超相關的端點路徑，更新 `config/sources.yaml`。
2. **`tpex_disposition`**：確認新網址 `disposal_information.php` 回傳的
   HTML 表格結構是否跟 `risk_list.parse_tpex_disposition` 的假設相符。
3. **`twse_institutional`**：確認新的探測日期是否抓到有資料的回應，核對
   `parse_institutional` 假設的欄位名稱（`外資買賣超股數`／`投信買賣超
   股數`／`自營商買賣超股數`）是否正確。
4. **twse.com.tw 系網域被擋**（`twse_daily_all`／`isin_*`／
   `mops_monthly_revenue_*`／`twse_daily_history`／`twse_disposition`／
   `twse_ex_rights`）：這是機器人防護規則層級的問題，不是單純的程式
   bug，兩輪都被擋的話就要跟使用者討論對策（例如：改變請求節奏、確認
   header、改用其他來源、或接受間歇性失敗並依賴 `fetch_log` 追蹤重試），
   不能靠程式碼修正就假裝解決。

在以上都確認之前，**這些來源在正式每日排程中很可能持續失敗**——好在
pipeline 的容錯設計（`fetch_log` 記錄 + 單一來源失敗不影響其他來源）
已經在跑，缺料會被明確記錄而不是靜默發生。
