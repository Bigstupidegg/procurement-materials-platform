# 國際原材料價格與採購分析平台

**Current development line: v2.3 — Project Foundation & Maintainability**

GitHub Pages：`https://bigstupidegg.github.io/procurement-materials-platform/`

這是一套以 GitHub Pages + GitHub Actions 運作的國際原材料價格與採購決策支援平台。平台整合市場價格、來源比對、趨勢訊號、Should-Cost、供應商漲價合理性與議價報告；最終決策仍由採購人員依供應商成本結構、庫存、匯率、運費與合約條件判斷。

## v2.3 目標

v2.3 不以增加大量新功能為優先，而是把現有成果整理成可長期開發與維護的正式基線：

- 統一專案版本、架構與文件。
- 明確區分 Real Data 與舊版 Demo Prototype。
- 將資料來源、公式、訊號規則與限制文件化。
- 建立 Supplier Case / TTP Radiator / Bushing 等後續擴充的資料基礎。
- 保持所有分析可追溯，避免「原料漲幅 = 成品漲幅」的錯誤推論。

## 目前實際狀態

正式資料層已運作，不再是單純的 v1.2.1 示範原型。

### Market Data

- `data/world-bank.json`：World Bank Pink Sheet，主要市場資料來源。
- `data/fred.json`：FRED，獨立交叉比較來源。
- `data/comparison.json`：World Bank / FRED 對照資料。
- `data/status.json`：同步狀態、最新月份與 freshness。

### Procurement Intelligence

- `data/signals.json`：趨勢、議價訊號與來源一致性。
- `data/should-cost-rules.json`：供應商漲價合理性判斷門檻與政策。
- `assets/source-comparison.js`：來源比較。
- `assets/trend-signals.js`：趨勢與議價訊號。
- `assets/supplier-rationality.js`：供應商調價合理性。
- `assets/negotiation-report.js`：議價報告。

### Production Real Data Path

- Repository 的 `assets/app.js` 保留舊版 Demo/UI fixture，方便回歸原型行為。
- 正式建置時 `scripts/prepare_site.py` 會從舊程式只抽出 Should-Cost 與導覽共用邏輯，產生 `_site/assets/app-core.js`。
- `_site/assets/app.js` 會被移除，正式頁面不會載入 Seeded Random Walk。
- 市場卡片、Chart、Tooltip、統計與 CSV 由 `assets/world-bank-live.js` 的已驗證 World Bank 資料路徑負責。
- CI 與 Pages 部署會拒絕任何 Demo engine 洩漏進 production core。

### Automation

- `.github/workflows/update-world-bank.yml`
- `.github/workflows/update-fred.yml`
- `.github/workflows/update-signals.yml`
- `.github/workflows/inspect-world-bank.yml`
- `.github/workflows/quality-check.yml`
- `.github/workflows/deploy-pages.yml`

Pull Request 會先執行 Quality Check；GitHub Pages 部署前也會執行採購分析相關測試，再由 `scripts/prepare_site.py` 組裝正式網站內容。

## 七項核心材料

目前主要涵蓋：

1. Zinc
2. Copper
3. Aluminium
4. Nickel
5. Iron Ore
6. Brent Crude Oil
7. Natural Gas

## 資料來源政策

- **World Bank Pink Sheet**：Primary market input / 趨勢主要判定來源。
- **FRED**：Independent comparison / corroboration only，不覆寫 World Bank 趨勢。
- 市場價格只代表市場方向，不代表供應商實際成本。
- 供應商合理成品調價必須另外考慮材料占比、採購落後期、庫存、匯率、能源、加工、運費與合約條件。
- 完整規範見 `docs/DATA_SOURCE_POLICY.md`。

## Should-Cost 核心原則

單一成本項目影響：

```text
Impact = Cost Share × Change Rate
```

總合理價格變動：

```text
Expected Price Change
= Raw Material Impact
+ Processing Impact
+ Energy Impact
+ Other Cost Impact
+ FX Exposure Impact
```

供應商要求與模型的差距：

```text
Negotiation Gap
= Supplier Requested Increase
- Expected Price Change
```

平台不自動接受或拒絕供應商調價。

## 專案結構

```text
/
├─ assets/          # 前端顯示、Demo fixture 與分析模組
├─ config/          # 材料、資料來源與 release metadata
├─ data/            # 市場資料、比較資料、訊號與規則
├─ scripts/         # 同步、驗證、衍生計算、production build
├─ tests/           # 解析、採購分析、production path 與 release identity 測試
├─ docs/            # 架構、知識底稿、資料政策、公式、Roadmap、Changelog
└─ .github/workflows/
```

## v2.3 文件

- `docs/PROJECT_KNOWLEDGE_BASE.md`：專案目的、歷史脈絡、實際採購案例與設計原則。
- `docs/DATA_DICTIONARY.md`：核心 JSON 與欄位定義。
- `docs/CALCULATION_RULES.md`：市場變化、Should-Cost、議價差距與訊號規則。
- `docs/DATA_SOURCE_POLICY.md`：資料來源角色、驗證、staleness、單位與安全政策。
- `docs/ROADMAP.md`：v2.3 與後續 Supplier Case / ERP 整合方向。
- `docs/CHANGELOG.md`：版本變更紀錄。
- `docs/github-pages-static-architecture.md`：原始靜態資料架構規劃。

## 目前技術債

Production runtime 已與舊 Demo market engine 分離，但 source tree 仍保留歷史名稱 `assets/app.js`。後續會再把這個 Demo fixture 正式改名／重構，並補瀏覽器層級的 cards / chart / tooltip / CSV 資料來源一致性測試。

## 開發原則

```text
Raw Data
↓
Validated Data
↓
Derived Metrics
↓
Market Signal
↓
Should-Cost
↓
Supplier Rationality
↓
Negotiation Report
↓
Human Decision
```

不要直接由 Raw Material Price 推導 Automatic Supplier Decision。
