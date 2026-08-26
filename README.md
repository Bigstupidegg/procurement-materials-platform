# 國際原材料價格與採購分析平台

**Current development line: v2.3 — Project Foundation & Maintainability**

GitHub Pages：`https://bigstupidegg.github.io/procurement-materials-platform/`

這是一套以 GitHub Pages + GitHub Actions 運作的國際原材料價格與採購決策支援平台。平台整合市場價格、來源比對、趨勢訊號、Should-Cost、供應商漲價合理性與議價報告；最終決策仍由採購人員依供應商成本結構、庫存、匯率、運費與合約條件判斷。

## v2.3 目標

v2.3 不以增加大量新功能為優先，而是把現有成果整理成可長期開發與維護的正式基線：

- 統一專案版本、架構與文件。
- 明確區分 Real Data 與 Development Demo。
- 將資料來源、公式、訊號規則與限制文件化。
- 建立 Supplier Case / TTP Radiator / Bushing 等後續擴充的資料基礎。
- 保持所有分析可追溯，避免「原料漲幅 = 成品漲幅」的錯誤推論。

## 目前實際狀態

公開 GitHub Pages 採 World Bank Pink Sheet 月度資料作為主要市場基準，FRED 作為獨立交叉核對。正式 production runtime 不使用 Demo 隨機行情。

Phase C3 新增的是**公司私有的每日市場資料層**，與公開網站保持分離。其來源已重新依原始 `update_prices_v5.py` 核對：

- Google Sheet：`大宗材料 行情統計表`
- B/C/E-I：LME
- D：SMM
- J：yfinance `BZ=F`
- K：yfinance `SI=F`（原始 Python 邏輯 ×100，存為 cents/oz）
- L：yfinance `GC=F`
- 每日銅採購判斷的第一市場參考仍是 LME Copper Cash OFFER。
- `ALLOW_GOOGLE_SHEET_WRITE=0` 時為 Live Dry Run：真的抓資料、真的驗證 Sheet，但不寫入。

詳細資料治理與 C3.1 驗收規則請見：

- `docs/COMPANY_MARKET_DATA_POLICY.md`
- `docs/C3_1_PRACTICAL_ACCEPTANCE.md`
- `docs/ROADMAP.md`

## 安全原則

- GitHub Pages 保留公開市場分析，不保存公司庫存、需求、供應商、PO、買賣決策或 Google 憑證。
- Google Sheet 保留公司日常採購作業資料。
- Python collector 是兩者之間的資料工程層。
- 必要來源或版型無法證明時採 Fail-Closed，不猜資料、不寫錯列。
