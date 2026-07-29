# 3B-1：World Bank Excel結構診斷

這個附加包只會新增三個檔案：

- `.github/workflows/inspect-world-bank.yml`
- `scripts/inspect_world_bank.py`
- `scripts/requirements-inspection.txt`

它不會修改網站、不會覆蓋 `data/*.json`，也不需要FRED API Key。

## 執行方式

1. 將本附加包解壓縮後，把 `.github` 與 `scripts` 兩個資料夾上傳到Repository根目錄。
2. Commit message：`Add World Bank workbook inspection workflow`
3. 進入 `Actions` → `Inspect World Bank Workbook`。
4. 按 `Run workflow` → `Run workflow`。
5. 執行成功後，在該Workflow頁面下方的Artifacts下載 `world-bank-workbook-inspection`。
6. 解壓後會得到：
   - `world-bank-inspection.json`
   - `world-bank-inspection.txt`

## 安全與影響

- 只讀取World Bank官方XLSX。
- 只允許 `thedocs.worldbank.org` 與 `pubdocs.worldbank.org`。
- 最大下載50MB。
- 不提交任何資料回Repository。
- 不觸發正式資料更新。
- 不更改GitHub Pages網站內容。
