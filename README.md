# 國際原材料價格與採購分析平台

## 階段二：GitHub Pages靜態結構

本版本由已驗收的前端v1.2.1拆分而成，保留原材料總覽、走勢圖、CSV匯出及採購成本試算器。

### 目前狀態

- 網頁仍使用`assets/app.js`內建的示範資料。
- `data/*.json`是合法的種子檔，但明確標示為尚未串接，不會被前端讀取。
- 尚未加入World Bank、FRED或GitHub Actions。
- Google Fonts及cdnjs連線已移除，所有頁面資源由同一個GitHub Pages網站載入。

### 檔案

- `index.html`：頁面結構
- `assets/app.css`：樣式
- `assets/app.js`：v1.2.1互動與試算邏輯
- `assets/vendor/chart.umd.min.js`：目前專案專用的離線相容圖表層
- `data/`：第三階段預留資料檔
- `config/`：第三階段預留來源設定
- `docs/github-pages-static-architecture.md`：完整架構規劃

### 圖表元件說明

由於此交付環境無法直接取得官方Chart.js發行檔，`assets/vendor/chart.umd.min.js`目前是專案專用的離線相容層，支援本頁面使用到的折線、縮放、滑鼠Tooltip、`destroy()`與`resize()`。

正式上線前，如需完全等同Chart.js的行為，請以官方Chart.js UMD發行檔覆蓋該檔案；`index.html`不需要再修改。

### 上傳GitHub

將ZIP解壓縮後，把資料夾內全部檔案上傳到Repository根目錄。GitHub Pages繼續使用`main`分支與`/(root)`即可。

### 第二階段驗收

1. 首頁、三個導覽頁籤正常。
2. 圖表可以顯示、切換期間與多材料指數化。
3. CSV可以匯出。
4. 試算器正常案例顯示`+10.00%`及`11,000.00元`。
5. Chrome Network中不應出現Google Fonts或cdnjs請求。
