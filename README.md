# 國際原材料價格與採購分析平台

**Current development line: v2.3 — Project Foundation & Maintainability**

GitHub Pages：`https://bigstupidegg.github.io/procurement-materials-platform/`

公開網站使用 World Bank Pink Sheet 月度資料為主要市場基準，FRED 為獨立核對；公司每日採購作業資料則維持在私人 Google Sheet。

## C3.1 每日市場資料來源

已依原始 `update_prices_v5.py` 再次核對：

- B/C/E-I：LME
- D：SMM
- J：yfinance `BZ=F`
- K：yfinance `SI=F`（×100，cents/oz）
- L：yfinance `GC=F`

LME Copper Cash OFFER 是每日銅採購分析的第一市場參考。Google Sheet 寫入預設為 Live Dry Run：真的抓 LME/SMM/yfinance、真的驗證 Sheet，但 `ALLOW_GOOGLE_SHEET_WRITE=0` 時不修改任何儲存格。

詳細規則：

- `docs/COMPANY_MARKET_DATA_POLICY.md`
- `docs/C3_1_PRACTICAL_ACCEPTANCE.md`
- `docs/ROADMAP.md`

## 安全原則

- 公開 GitHub Pages 不保存公司庫存、需求、PO、供應商或買賣決策。
- Google Sheet 保留公司日常作業資料。
- Python collector 是資料工程層。
- 必要來源、欄位或日期無法證明時採 Fail-Closed。
