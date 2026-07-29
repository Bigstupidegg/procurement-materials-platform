# 國際原材料價格與採購分析平台 — GitHub Pages＋Actions 靜態資料架構規劃

版本基準：前端 v1.2.1（**本文件不重寫HTML，僅規劃架構與檔案**）
資料儲存：無資料庫，第一階段以 **版本化JSON（存於Git）** 作為資料儲存層
部署：GitHub Pages（Actions部署模式）＋GitHub Actions（排程同步）

---

## 1. 架構說明

### 1.1 核心原則
- **全靜態**：沒有後端伺服器、沒有資料庫。所有運算（下載、解析、驗證）發生在 GitHub Actions Runner 上，結果以 JSON 檔案形式提交回 Repository，再由 GitHub Pages 原樣發布。
- **瀏覽器只讀自家網域**：`index.html` 執行後只會 `fetch('./data/*.json')`，這些請求與頁面本身同源（同一個 `*.github.io` 網域），瀏覽器不會、也不能連到 World Bank 或 FRED。
- **零第三方CDN**：Google Fonts 與 cdnjs Chart.js 移除，字型改用作業系統內建中文字型（不送出任何字型下載請求），Chart.js 改為原始碼直接放入 repo 的 `assets/chart.umd.min.js`，由同網域載入。
- **金鑰只存在CI環境**：`FRED_API_KEY` 只存在於 GitHub Actions 的 Repository Secret，執行時以環境變數注入 Python 程式，**不會**寫入任何 JSON、程式碼、commit訊息或 Actions log。
- **先驗證、再落地、再部署**：任何一次同步，資料先在 Runner 的暫存工作目錄產生 → 通過驗證才寫入 `data/*.json` → 只有 `git diff` 顯示確有變化才 commit → commit 推上 `main` 才會觸發部署 workflow。若驗證失敗，Runner 上的暫存變更不會被提交，Repository（因此也是 Pages 上）的資料維持前一版本不變。

### 1.2 兩條 Workflow 的關係

```
┌─────────────────────────┐        push to main (data/**變更)       ┌──────────────────────┐
│  update-data.yml         │ ───────────────────────────────────▶ │  deploy-pages.yml       │
│  (排程 09:17 Asia/Taipei) │                                        │  (path-filter觸發)       │
│  1.下載WorldBank          │                                        │  1.checkout             │
│  2.驗證                   │                                        │  2.打包整個repo為靜態站台 │
│  3.解析                   │                                        │  3.deploy-pages          │
│  4.呼叫FRED               │                                        └──────────────────────┘
│  5.驗證                   │
│  6.產生/更新data/*.json    │
│  7.pytest驗證              │
│  8.有變化才commit+push      │
└─────────────────────────┘
```

- `update-data.yml` 是**唯一**會修改 `data/*.json` 的地方，且只在 GitHub Actions 的隔離環境內執行，無法被瀏覽器使用者觸發。
- `deploy-pages.yml` 完全不碰外部資料來源，只負責把目前 repo 內容（含最新 `data/*.json`）打包發布，因此「驗證成功才部署」是透過「只有驗證成功才會有新commit」自然達成，不需要額外的跨workflow狀態傳遞。

### 1.3 資料流向

```
World Bank (thedocs/pubdocs.worldbank.org)   FRED API (api.stlouisfed.org)
        │ (僅Actions Runner連線)                    │ (僅Actions Runner連線，帶FRED_API_KEY)
        ▼                                            ▼
 sync_world_bank.py                          sync_fred.py
        │ 產生                                        │ 產生
        ▼                                            ▼
 data/world-bank.json                        data/fred.json
        └───────────────┬────────────────────────────┘
                         ▼
                build_comparison.py
                         │ 產生
                         ▼
                data/comparison.json
                         │
                validate_data.py（讀取以上三者＋更新 data/status.json）
                         │ 全部通過
                         ▼
                 git commit & push（僅data有變化時）
                         │
                         ▼
              deploy-pages.yml 自動觸發 → GitHub Pages 更新
                         │
                         ▼
           使用者瀏覽器 fetch 同網域 /data/*.json（唯讀，無外部連線）
```

---

## 2. 完整檔案樹

```
procurement-materials-platform/
├── index.html                          # 沿用v1.2.1版面/配色/功能，改為fetch本地JSON、載入本地assets
├── assets/
│   ├── app.css                         # 現有v1.2.1樣式（移除Google Fonts @import，改用系統字型堆疊）
│   ├── app.js                          # 現有v1.2.1邏輯（資料來源從內建陣列改為fetch data/*.json）
│   └── chart.umd.min.js                # Chart.js原始碼（vendored，不由cdnjs載入）
│
├── data/
│   ├── world-bank.json                 # 主要資料（七項材料月資料）
│   ├── fred.json                       # 比較資料（七項FRED系列）
│   ├── comparison.json                 # World Bank + FRED 合併比較資料
│   └── status.json                     # 最近同步狀態、雜湊、時間戳
│
├── config/
│   ├── materials.json                  # 七項材料主檔（id/中英文名/幣別/單位/來源代碼/標示文字）
│   └── world-bank-columns.json         # Pink Sheet欄位對應（工作表名稱、標頭文字、日期欄位）
│
├── scripts/
│   ├── sync_world_bank.py              # 下載+驗證+解析+落地 world-bank.json
│   ├── sync_fred.py                    # 呼叫FRED+驗證+落地 fred.json
│   ├── build_comparison.py             # 合併產生 comparison.json
│   ├── validate_data.py                # 全資料集最終驗證（供CI擋下不合法結果）
│   ├── common/
│   │   ├── __init__.py
│   │   ├── http_guard.py               # SSRF防護、網域白名單、大小限制、雜湊計算（共用）
│   │   ├── json_io.py                  # 原子寫入（tmp檔+os.replace）、UTF-8、格式化
│   │   └── sanitize.py                 # 外部文字轉義/清理（escape_text）
│   └── requirements.txt
│
├── tests/
│   ├── fixtures/
│   │   ├── sample_world_bank.xlsx      # 測試用小型合法樣本
│   │   ├── sample_world_bank_missing_column.xlsx  # 缺欄位樣本（驗證失敗情境）
│   │   └── sample_fred_observations.json
│   ├── test_world_bank_parser.py
│   ├── test_fred_parser.py
│   └── test_validation.py
│
├── .github/
│   └── workflows/
│       ├── update-data.yml             # 排程同步（09:17 Asia/Taipei + workflow_dispatch）
│       └── deploy-pages.yml            # GitHub Pages 部署
│
├── README.md
└── .gitignore
```

---

## 3. 各 JSON Schema

所有 JSON 皆為 UTF-8、鍵值採 camelCase、日期一律 `YYYY-MM-DD`（代表當月）。

### 3.1 `config/materials.json`
```jsonc
[
  {
    "id": "copper",
    "nameZh": "銅", "nameEn": "Copper",
    "currency": "USD", "unit": "公噸(MT)",
    "worldBankColumn": "Copper",
    "fredSeriesCode": "PCOPPUSDM",
    "isLmeDerived": true,
    "attributionNote": "資料提供：World Bank Pink Sheet；價格基準說明：LME金屬價格之月度彙整資料。"
  },
  {
    "id": "zinc", "nameZh": "鋅", "nameEn": "Zinc",
    "currency": "USD", "unit": "公噸(MT)",
    "worldBankColumn": "Zinc", "fredSeriesCode": "PZINCUSDM",
    "isLmeDerived": true,
    "attributionNote": "資料提供：World Bank Pink Sheet；價格基準說明：LME金屬價格之月度彙整資料。"
  },
  {
    "id": "aluminium", "nameZh": "鋁", "nameEn": "Aluminium",
    "currency": "USD", "unit": "公噸(MT)",
    "worldBankColumn": "Aluminum", "fredSeriesCode": "PALUMUSDM",
    "isLmeDerived": true,
    "attributionNote": "資料提供：World Bank Pink Sheet；價格基準說明：LME金屬價格之月度彙整資料。"
  },
  {
    "id": "nickel", "nameZh": "鎳", "nameEn": "Nickel",
    "currency": "USD", "unit": "公噸(MT)",
    "worldBankColumn": "Nickel", "fredSeriesCode": "PNICKUSDM",
    "isLmeDerived": true,
    "attributionNote": "資料提供：World Bank Pink Sheet；價格基準說明：LME金屬價格之月度彙整資料。"
  },
  {
    "id": "iron_ore", "nameZh": "鐵礦砂", "nameEn": "Iron Ore",
    "currency": "USD", "unit": "公噸(MT)",
    "worldBankColumn": "Iron ore", "fredSeriesCode": "PIORECRUSDM",
    "isLmeDerived": false,
    "attributionNote": "資料提供：World Bank Pink Sheet（62%鐵含量到岸價格式）。"
  },
  {
    "id": "crude_oil", "nameZh": "原油", "nameEn": "Crude Oil, Brent",
    "currency": "USD", "unit": "桶(bbl)",
    "worldBankColumn": "Crude oil, Brent", "fredSeriesCode": "MCOILBRENTEU",
    "isLmeDerived": false,
    "attributionNote": "資料提供：World Bank Pink Sheet（布蘭特原油Brent格式）。"
  },
  {
    "id": "natural_gas", "nameZh": "天然氣", "nameEn": "Natural Gas, U.S.",
    "currency": "USD", "unit": "MMBtu",
    "worldBankColumn": "Natural gas, U.S.", "fredSeriesCode": "MHHNGSP",
    "isLmeDerived": false,
    "attributionNote": "資料提供：World Bank Pink Sheet（Henry Hub天然氣格式）。"
  }
]
```

### 3.2 `config/world-bank-columns.json`
```jsonc
{
  "sourceUrlAllowlistDomains": ["thedocs.worldbank.org", "pubdocs.worldbank.org"],
  "sheetName": "Monthly Prices",
  "headerRow": 6,
  "dateColumnHeader": "Unnamed: 0",
  "expectedColumns": [
    "Copper", "Zinc", "Aluminum", "Nickel", "Iron ore",
    "Crude oil, Brent", "Natural gas, U.S."
  ],
  "notes": "實際欄位標頭與列號需以官方檔案為準，於首次串接時人工核對一次並寫入此設定；若之後標頭消失或改名，視為SCHEMA_DRIFT，中止匯入。"
}
```

### 3.3 `data/world-bank.json`
```jsonc
{
  "generatedAt": "2026-07-29T01:17:32Z",
  "source": "WORLD_BANK",
  "sourceUrl": "https://thedocs.worldbank.org/.../CMO-Historical-Data-Monthly.xlsx",
  "fileHash": "sha256:9f2c1a...",
  "fileSizeBytes": 812345,
  "series": {
    "copper": {
      "nameZh": "銅", "nameEn": "Copper",
      "currency": "USD", "unit": "公噸(MT)",
      "attributionNote": "資料提供：World Bank Pink Sheet；價格基準說明：LME金屬價格之月度彙整資料。",
      "points": [
        { "date": "2026-05-01", "price": 9184.20 },
        { "date": "2026-06-01", "price": 9201.55 }
      ]
    }
    // ...其餘六項材料，結構相同
  }
}
```

### 3.4 `data/fred.json`
```jsonc
{
  "generatedAt": "2026-07-29T01:18:05Z",
  "source": "FRED",
  "label": "FRED備援／研究比較資料",
  "series": {
    "copper": {
      "seriesCode": "PCOPPUSDM",
      "unit": "USD", "frequency": "Monthly",
      "lastObservationDate": "2026-06-01",
      "points": [
        { "date": "2026-05-01", "price": 9150.10 },
        { "date": "2026-06-01", "price": 9188.40 }
      ]
    }
    // ...
  },
  "failedSeries": []  // 若某系列本次同步失敗，記錄代碼於此，points則沿用上次成功結果
}
```

### 3.5 `data/comparison.json`
```jsonc
{
  "generatedAt": "2026-07-29T01:18:40Z",
  "materials": {
    "copper": {
      "nameZh": "銅", "nameEn": "Copper",
      "worldBank": { "source": "WORLD_BANK", "points": [ /* 同上 */ ] },
      "fred": {
        "source": "FRED", "seriesCode": "PCOPPUSDM",
        "label": "FRED備援／研究比較資料",
        "points": [ /* 同上 */ ]
      }
    }
    // ...
  }
}
```

### 3.6 `data/status.json`
```jsonc
{
  "generatedAt": "2026-07-29T01:19:00Z",
  "worldBank": {
    "status": "SUCCESS",            // SUCCESS | SKIPPED_NO_CHANGE | FAILED
    "lastSuccessAt": "2026-07-29T01:17:50Z",
    "fileHash": "sha256:9f2c1a...",
    "rowsInserted": 0, "rowsRevised": 2,
    "errorReason": null
  },
  "fred": {
    "status": "PARTIAL_FAILURE",    // SUCCESS | PARTIAL_FAILURE | FAILED
    "lastSuccessAt": "2026-07-29T01:18:05Z",
    "failedSeries": ["PIORECRUSDM"],
    "errorReason": "PIORECRUSDM: HTTP 503"
  },
  "isStale": false
}
```

---

## 4. GitHub Actions 流程

### 4.1 `.github/workflows/update-data.yml`
```yaml
name: Update Commodity Data

on:
  schedule:
    # 09:17 Asia/Taipei = 01:17 UTC（GitHub Actions cron僅支援UTC）
    - cron: '17 1 * * *'
  workflow_dispatch: {}

permissions:
  contents: write

concurrency:
  group: update-data
  cancel-in-progress: false

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: pip install -r scripts/requirements.txt

      - name: Sync World Bank Pink Sheet
        run: python scripts/sync_world_bank.py

      - name: Sync FRED comparison series
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
        run: python scripts/sync_fred.py

      - name: Build comparison dataset
        run: python scripts/build_comparison.py

      - name: Validate all datasets
        run: python scripts/validate_data.py

      - name: Run unit tests
        run: pytest tests/ -q

      - name: Detect data changes
        id: diff
        run: |
          if git diff --quiet -- data/; then
            echo "changed=false" >> "$GITHUB_OUTPUT"
          else
            echo "changed=true" >> "$GITHUB_OUTPUT"
          fi

      - name: Commit and push updated data
        if: steps.diff.outputs.changed == 'true'
        run: |
          git config user.name "commodity-data-bot"
          git config user.email "actions@users.noreply.github.com"
          git add data/
          git commit -m "chore(data): scheduled sync $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
          git push
```

**設計重點：**
- `sync_*.py` 與 `build_comparison.py` 只把結果寫進工作目錄（尚未commit）；`validate_data.py` 與 `pytest` 任一失敗，workflow 即在該步驟中止，後續「Commit and push」步驟因 job 已失敗而不會執行 → **驗證失敗＝Repository內容完全不變**。
- 只有 `git diff` 偵測到 `data/` 目錄真的有差異才 commit，避免每天產生「無變化」的空提交。
- `contents: write` 權限僅授予此 workflow 用於推送 `data/` 更新，其餘workflow不需要。
- FRED_API_KEY 僅在此單一步驟以環境變數注入，執行結束即隨Runner銷毀，不落地於任何檔案。

### 4.2 `.github/workflows/deploy-pages.yml`
```yaml
name: Deploy Pages

on:
  push:
    branches: [main]
    paths:
      - 'index.html'
      - 'assets/**'
      - 'data/**'
      - 'config/**'
  workflow_dispatch: {}

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Configure Pages
        uses: actions/configure-pages@v5

      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: '.'

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

**設計重點：**
- 以 `paths` 過濾，只有 `index.html` / `assets/**` / `data/**` / `config/**` 變更時才觸發部署，避免 `README.md`、`tests/` 等變更也觸發不必要的重新部署。
- `update-data.yml` 成功 push 後，此 workflow 會被 GitHub 自動觸發，天然形成「驗證成功→commit→部署」的順序，不需要額外的 workflow_run 依賴設定。

---

## 5. Python 同步程式設計

### 5.1 共用模組

**`scripts/common/http_guard.py`**（SSRF防護與下載）
```python
import hashlib
import ipaddress
import socket
from urllib.parse import urlparse
import requests

ALLOWED_DOMAINS = {"thedocs.worldbank.org", "pubdocs.worldbank.org"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB上限
ALLOWED_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",  # 部分伺服器對xlsx回傳此值，需搭配副檔名再次確認
}

class SsrfBlockedError(Exception): pass
class DownloadValidationError(Exception): pass

def _resolve_and_check_public(hostname: str) -> None:
    """阻擋私網/迴圈位址，防止DNS重綁定造成的SSRF。"""
    infos = socket.getaddrinfo(hostname, None)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise SsrfBlockedError(f"目標位址不允許: {ip}")

def safe_download(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOMAINS:
        raise SsrfBlockedError(f"網域不在白名單: {parsed.hostname}")
    _resolve_and_check_public(parsed.hostname)

    with requests.get(url, stream=True, timeout=30, allow_redirects=False) as resp:
        if resp.status_code != 200:
            raise DownloadValidationError(f"HTTP狀態碼異常: {resp.status_code}")
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise DownloadValidationError(f"Content-Type不符預期: {content_type}")

        sha256 = hashlib.sha256()
        total = 0
        chunks = []
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            total += len(chunk)
            if total > MAX_FILE_SIZE_BYTES:
                raise DownloadValidationError("檔案超過大小上限，已中止下載")
            sha256.update(chunk)
            chunks.append(chunk)

    return {
        "content": b"".join(chunks),
        "sha256": f"sha256:{sha256.hexdigest()}",
        "size_bytes": total,
        "content_type": content_type,
        "http_status": 200,
    }
```
> 注意：程式**不追隨重導向**（`allow_redirects=False`），若官方連結改用重導向轉址，需人工更新設定檔中的直接下載網址，避免重導向被利用繞過網域白名單。

**`scripts/common/json_io.py`**（原子寫入）
```python
import json
import os
import tempfile

def atomic_write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)  # 同檔案系統內為原子操作
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
```
> 只有整份JSON成功寫入暫存檔後才 `os.replace` 覆蓋正式檔案，避免解析中途失敗留下半份壞檔。

**`scripts/common/sanitize.py`**
```python
import html
import re

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

def escape_text(value: str, max_length: int = 500) -> str:
    """供錯誤訊息/欄位描述等外部來源文字寫入JSON或訊息前使用。"""
    if value is None:
        return ""
    text = str(value)[:max_length]
    text = _CONTROL_CHARS.sub("", text)
    return html.escape(text, quote=True)
```

### 5.2 `scripts/sync_world_bank.py`（設計骨架）
```python
"""
下載World Bank Pink Sheet月資料，解析七項材料，驗證後產生 data/world-bank.json。
任何驗證失敗 → 拋出例外、非0結束，不寫入/不覆蓋既有JSON。
"""
import json
import sys
from datetime import datetime, timezone

import openpyxl

from common.http_guard import safe_download, SsrfBlockedError, DownloadValidationError
from common.json_io import atomic_write_json

WORLD_BANK_XLSX_URL = "https://thedocs.worldbank.org/.../CMO-Historical-Data-Monthly.xlsx"  # 設定檔管理，定期人工確認網址仍有效
STATUS_PATH = "data/status.json"
OUTPUT_PATH = "data/world-bank.json"

class SchemaDriftError(Exception): pass

def load_config():
    materials = json.load(open("config/materials.json", encoding="utf-8"))
    columns_cfg = json.load(open("config/world-bank-columns.json", encoding="utf-8"))
    return materials, columns_cfg

def already_synced(file_hash: str) -> bool:
    try:
        status = json.load(open(STATUS_PATH, encoding="utf-8"))
        return status.get("worldBank", {}).get("fileHash") == file_hash
    except FileNotFoundError:
        return False

def parse_workbook(raw_bytes: bytes, materials: list, columns_cfg: dict) -> dict:
    import io
    # keep_links=False 避免解析外部參照；openpyxl本身不會執行VBA巨集（僅.xlsm含巨集，官方檔為.xlsx不含巨集）
    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True, keep_links=False)
    sheet_name = columns_cfg["sheetName"]
    if sheet_name not in wb.sheetnames:
        raise SchemaDriftError(f"找不到工作表: {sheet_name}")
    ws = wb[sheet_name]

    header_row = columns_cfg["headerRow"]
    headers = [cell.value for cell in ws[header_row]]
    expected = set(columns_cfg["expectedColumns"])
    missing = expected - set(h for h in headers if h)
    if missing:
        raise SchemaDriftError(f"缺少必要欄位: {sorted(missing)}")

    col_index = {h: idx for idx, h in enumerate(headers) if h in expected}
    date_col_idx = 0  # 依實際檔案調整，通常為第一欄

    series_points = {m["worldBankColumn"]: [] for m in materials}
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        raw_date = row[date_col_idx]
        period_date = normalize_period_date(raw_date)  # 內部函式：轉為 'YYYY-MM-01'，失敗則跳過該列
        if period_date is None:
            continue
        for col_name, idx in col_index.items():
            value = row[idx]
            price = to_finite_float(value)  # 非有限數字回傳 None
            if price is not None:
                series_points[col_name].append({"date": period_date, "price": price})

    result = {}
    for m in materials:
        wb_col = m["worldBankColumn"]
        points = series_points.get(wb_col, [])
        if not points:
            raise SchemaDriftError(f"材料無有效資料列: {wb_col}")
        result[m["id"]] = {
            "nameZh": m["nameZh"], "nameEn": m["nameEn"],
            "currency": m["currency"], "unit": m["unit"],
            "attributionNote": m["attributionNote"],
            "points": points,
        }
    return result

def main() -> int:
    materials, columns_cfg = load_config()
    try:
        download = safe_download(WORLD_BANK_XLSX_URL)
    except (SsrfBlockedError, DownloadValidationError) as e:
        print(f"[FAILED] 下載失敗: {e}", file=sys.stderr)
        return 1

    if already_synced(download["sha256"]):
        print("[SKIPPED_NO_CHANGE] World Bank檔案雜湊未變更，略過本次匯入。")
        return 0

    try:
        series = parse_workbook(download["content"], materials, columns_cfg)
    except SchemaDriftError as e:
        print(f"[FAILED][SCHEMA_DRIFT] {e}", file=sys.stderr)
        return 1

    output = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "WORLD_BANK",
        "sourceUrl": WORLD_BANK_XLSX_URL,
        "fileHash": download["sha256"],
        "fileSizeBytes": download["size_bytes"],
        "series": series,
    }
    atomic_write_json(OUTPUT_PATH, output)
    print(f"[SUCCESS] 已更新 {OUTPUT_PATH}（{download['sha256']}）")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### 5.3 `scripts/sync_fred.py`（設計骨架）
```python
"""
呼叫FRED API取得七項比較系列，驗證後產生 data/fred.json。
單一系列失敗不影響其他系列；FRED_API_KEY 僅由環境變數讀取，絕不落地。
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

from common.json_io import atomic_write_json
from common.sanitize import escape_text

FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
OUTPUT_PATH = "data/fred.json"

def fetch_series(series_code: str, api_key: str) -> list:
    params = {
        "series_id": series_code,
        "api_key": api_key,
        "file_type": "json",
    }
    resp = requests.get(FRED_BASE_URL, params=params, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    payload = resp.json()
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise RuntimeError("回應缺少 observations 陣列")

    points = []
    for obs in observations:
        value = obs.get("value")
        date = obs.get("date")
        if value in (None, ".", ""):
            continue  # FRED缺值符號，跳過該筆但不視為整體錯誤
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if not (price == price and price not in (float("inf"), float("-inf"))):  # 排除NaN/Infinity
            continue
        points.append({"date": date[:8] + "01", "price": price})
    return points

def main() -> int:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("[FAILED][FRED_AUTH_ERROR] 未設定 FRED_API_KEY", file=sys.stderr)
        return 1

    materials = json.load(open("config/materials.json", encoding="utf-8"))
    series_out = {}
    failed = []

    for m in materials:
        code = m["fredSeriesCode"]
        try:
            points = fetch_series(code, api_key)
            if not points:
                raise RuntimeError("無有效觀測值")
            series_out[m["id"]] = {
                "seriesCode": code,
                "unit": m["currency"],
                "frequency": "Monthly",
                "lastObservationDate": points[-1]["date"],
                "points": points,
            }
        except Exception as e:  # noqa: BLE001 — 單一系列的任何錯誤都不應中止整體流程
            failed.append(code)
            print(f"[WARN][FRED_SERIES_ERROR] {code}: {escape_text(str(e))}", file=sys.stderr)

    output = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "FRED",
        "label": "FRED備援／研究比較資料",
        "series": series_out,
        "failedSeries": failed,
    }
    atomic_write_json(OUTPUT_PATH, output)

    if not series_out:
        print("[FAILED] 全部FRED系列皆失敗", file=sys.stderr)
        return 1
    print(f"[{'PARTIAL_FAILURE' if failed else 'SUCCESS'}] 已更新 {OUTPUT_PATH}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```
> **金鑰安全**：`api_key` 只存在於函式區域變數與 HTTP 請求參數中；程式**不**將 `api_key` 寫入任何 print、log 或輸出 JSON。GitHub Actions 本身也會自動於 log 輸出中遮罩符合 secret 值的字串，作為第二層防護。

### 5.4 `scripts/build_comparison.py`（設計骨架）
```python
"""合併 world-bank.json 與 fred.json，產生 comparison.json。不改動任一來源檔案。"""
import json
from datetime import datetime, timezone
from common.json_io import atomic_write_json

def main() -> int:
    wb = json.load(open("data/world-bank.json", encoding="utf-8"))
    fred = json.load(open("data/fred.json", encoding="utf-8"))
    materials = json.load(open("config/materials.json", encoding="utf-8"))

    out = {}
    for m in materials:
        mid = m["id"]
        wb_series = wb["series"].get(mid)
        fred_series = fred["series"].get(mid)
        out[mid] = {
            "nameZh": m["nameZh"], "nameEn": m["nameEn"],
            "worldBank": {"source": "WORLD_BANK", "points": wb_series["points"]} if wb_series else None,
            "fred": (
                {
                    "source": "FRED",
                    "seriesCode": fred_series["seriesCode"],
                    "label": "FRED備援／研究比較資料",
                    "points": fred_series["points"],
                }
                if fred_series else None
            ),
        }

    atomic_write_json("data/comparison.json", {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "materials": out,
    })
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

### 5.5 `scripts/validate_data.py`（設計骨架 — CI最後一道防線）
```python
"""
對 data/*.json 做結構與數值有效性最終檢查。任何一項失敗即 exit 1，
使update-data.yml在commit步驟之前失敗，Repository資料維持前一版本。
同時負責更新 data/status.json。
"""
import json
import math
import sys
from datetime import datetime, timezone

REQUIRED_MATERIAL_IDS = [
    "copper", "zinc", "aluminium", "nickel", "iron_ore", "crude_oil", "natural_gas"
]

def is_finite_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

def validate_world_bank(wb: dict) -> list:
    errors = []
    for mid in REQUIRED_MATERIAL_IDS:
        series = wb.get("series", {}).get(mid)
        if not series or not series.get("points"):
            errors.append(f"world-bank.json 缺少材料資料: {mid}")
            continue
        for p in series["points"]:
            if not is_finite_number(p.get("price")):
                errors.append(f"{mid} 存在非有限數字價格: {p}")
            if not isinstance(p.get("date"), str) or len(p["date"]) != 10:
                errors.append(f"{mid} 日期格式異常: {p}")
    return errors

def validate_fred(fred: dict) -> list:
    errors = []
    if not isinstance(fred.get("series"), dict):
        errors.append("fred.json 缺少 series 物件")
        return errors
    for mid, series in fred["series"].items():
        for p in series.get("points", []):
            if not is_finite_number(p.get("price")):
                errors.append(f"FRED {mid} 存在非有限數字價格: {p}")
    return errors

def main() -> int:
    wb = json.load(open("data/world-bank.json", encoding="utf-8"))
    fred = json.load(open("data/fred.json", encoding="utf-8"))

    errors = validate_world_bank(wb) + validate_fred(fred)

    status = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "worldBank": {
            "status": "FAILED" if any("world-bank" in e for e in errors) else "SUCCESS",
            "lastSuccessAt": wb.get("generatedAt"),
            "fileHash": wb.get("fileHash"),
        },
        "fred": {
            "status": "PARTIAL_FAILURE" if fred.get("failedSeries") else "SUCCESS",
            "lastSuccessAt": fred.get("generatedAt"),
            "failedSeries": fred.get("failedSeries", []),
        },
        "isStale": False,
    }

    if errors:
        status["worldBank"]["errorReason"] = errors[0][:300]
        # 驗證失敗時仍記錄本次嘗試，但不覆蓋data/status.json中「上一次成功」的資訊由人工排查
        print("[VALIDATION_FAILED]")
        for e in errors:
            print(f" - {e}", file=sys.stderr)
        return 1

    from common.json_io import atomic_write_json
    atomic_write_json("data/status.json", status)
    print("[VALIDATION_OK] status.json 已更新")
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

### 5.6 `scripts/requirements.txt`
```
requests==2.32.3
openpyxl==3.1.5
python-dateutil==2.9.0
pytest==8.3.3
```

---

## 6. 防火牆／第三方相依性清單

| 項目 | v1.2.1現況 | v1.3規劃 |
|---|---|---|
| 中文字型 | `fonts.googleapis.com` / `fonts.gstatic.com`（Noto Sans TC、Roboto Mono） | 移除，改用系統字型堆疊：`-apple-system, "PingFang TC", "Microsoft JhengHei", "Noto Sans CJK TC", sans-serif`；等寬數字改用 `ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace` |
| 圖表函式庫 | `cdnjs.cloudflare.com`（Chart.js UMD） | 移除CDN，改為將 Chart.js 原始碼下載後提交進 `assets/chart.umd.min.js`，由 `<script src="./assets/chart.umd.min.js">` 同網域載入 |
| 資料來源 | 前端內建JS陣列（示範資料） | `fetch('./data/world-bank.json')` 等同網域JSON，不連任何外部API |
| World Bank / FRED 連線 | 無（尚未串接） | **僅** GitHub Actions Runner連線，瀏覽器完全不觸及 |

**頁面載入後允許的網路請求（正式版）：**
- `GET /`（index.html，同網域）
- `GET /assets/app.css`、`/assets/app.js`、`/assets/chart.umd.min.js`（同網域）
- `GET /data/world-bank.json`、`/data/fred.json`、`/data/comparison.json`、`/data/status.json`（同網域）

**不允許出現的請求：** 任何 `fonts.googleapis.com`、`fonts.gstatic.com`、`cdnjs.cloudflare.com`、`thedocs.worldbank.org`、`pubdocs.worldbank.org`、`api.stlouisfed.org` 等第三方網域。

**建議在 `index.html` 加入 CSP，於瀏覽器端也強制此限制：**
```html
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self';">
```
（`style-src 'unsafe-inline'` 視v1.2.1內聯樣式使用情形保留；若後續改為外部CSS可再收緊。）

**試算器安全補充**：`assets/app.js` 中的試算器邏輯維持純前端計算，不對任何欄位輸入呼叫 `fetch`/`XMLHttpRequest`，滿足「網頁不得將使用者的試算輸入送到伺服器」。

---

## 7. GitHub Pages 發布步驟

1. **建立Repository**並將本文件規劃之檔案結構逐步加入（先以現有v1.2.1內容為基礎拆分為 `index.html` / `assets/app.css` / `assets/app.js`，本階段資料先以現有示範資料JSON手動放入 `data/` 作為種子，待Actions首次成功同步後自動取代）。
2. **新增Secret**：Repository → Settings → Secrets and variables → Actions → New repository secret → 名稱 `FRED_API_KEY`，值為FRED官方申請之API金鑰。
3. **設定Pages來源**：Repository → Settings → Pages → Build and deployment → Source 選擇 **GitHub Actions**（而非「Deploy from a branch」）。
4. **確認Workflow權限**：Repository → Settings → Actions → General → Workflow permissions → 選擇 **Read and write permissions**（供 `update-data.yml` 執行 `git push`）。
5. **推送初始commit**至 `main`：`deploy-pages.yml` 會自動觸發，發布目前版本（此時 `data/*.json` 為種子/示範資料）。
6. **手動觸發一次 `update-data.yml`**（Actions頁籤 → Update Commodity Data → Run workflow）取得第一份正式資料；成功後會自動commit並觸發 `deploy-pages.yml` 重新部署。
7. 之後每日 09:17 台灣時間自動執行，如需即時更新可隨時手動 `workflow_dispatch` 觸發。
8. 發布網址預設為 `https://<org或帳號>.github.io/<repo名稱>/`；如需自訂網域，於Settings → Pages → Custom domain設定，並同步更新CSP中的 `default-src` 若改用非 `.github.io` 網域。

---

## 8. 驗收測試案例

| 編號 | 情境 | 預期結果 |
|---|---|---|
| T1 | 對正式World Bank檔案執行 `sync_world_bank.py` | 產生合法 `data/world-bank.json`，`status.json.worldBank.status = SUCCESS` |
| T2 | 連續執行兩次且來源檔案未變更 | 第二次顯示 `SKIPPED_NO_CHANGE`，`data/world-bank.json` 內容與時間戳不變，`git diff` 無差異 |
| T3 | 使用 `tests/fixtures/sample_world_bank_missing_column.xlsx`（缺Nickel欄位）測試解析 | `parse_workbook` 拋出 `SchemaDriftError`，`sync_world_bank.py` 回傳非0，`data/world-bank.json` 維持修改前內容 |
| T4 | 模擬FRED其中一個series（如`PIORECRUSDM`）回傳HTTP 503 | 其餘六項系列正常寫入，`failedSeries` 含該代碼，整體流程status為 `PARTIAL_FAILURE`但不中止 |
| T5 | `validate_data.py` 偵測到某筆價格為 `NaN`/`Infinity` | 回傳非0並印出具體錯誤列，`update-data.yml` 於此步驟失敗，不進入commit步驟 |
| T6 | 於瀏覽器開發者工具Network面板載入首頁並操作走勢圖與試算器 | 僅出現同網域（`*.github.io`或自訂網域）請求，**不出現** `fonts.googleapis.com`、`cdnjs.cloudflare.com`、`thedocs.worldbank.org`、`api.stlouisfed.org` 等請求 |
| T7 | 檢查Repository原始碼、`data/*.json`、Actions執行log | 全文搜尋不含FRED API金鑰明文；Actions log中若曾輸出金鑰字串，應顯示為 `***`（GitHub自動遮罩） |
| T8 | 於試算器輸入任意數值並持續觀察Network面板 | 全程無任何XHR/fetch請求被觸發（試算完全在瀏覽器本地執行） |
| T9 | 人為讓 `validate_data.py` 失敗（如手動改壞暫存JSON）後重跑workflow | `main`分支 `data/*.json` 內容與失敗前一致，`deploy-pages.yml` 未被觸發（因無新commit） |
| T10 | 手動觸發 `workflow_dispatch` | 兩個workflow皆可在Actions頁籤手動執行成功，行為與排程觸發一致 |
| T11 | 檢查銅／鋁／鋅／鎳三項材料之 `attributionNote` | 內容固定為「資料提供：World Bank Pink Sheet；價格基準說明：LME金屬價格之月度彙整資料。」，**不**出現「LME官方直接資料」等字樣 |

---

## 附註：與現有 v1.2.1 前端的銜接方式

- `index.html` 版面、配色、導覽結構、圖表與試算器**公式**完全比照 v1.2.1；僅需將以下兩處改為fetch本地JSON、其餘互動邏輯（期間切換、指數化、CSV匯出、試算驗證等）不需更動：
  1. 目前寫死於 `assets/app.js` 的 `MATERIALS` 陣列與 `genSeries()` 模擬引擎 → 改為頁面載入時 `fetch('./data/world-bank.json')`（總覽/走勢圖）與 `fetch('./data/comparison.json')`（比較模式）。
  2. 「示範資料」標籤與免責聲明字樣，待正式資料上線後改為顯示 `attributionNote`、`generatedAt`／`status.json` 中的同步狀態；此為文案調整，非本文件範圍，待正式串接階段再與介面規格一併確認。
- CSV匯出、Tooltip、指數化比較邏輯、試算器五項成本影響公式**完全不變**，僅資料輸入源從記憶體陣列改為fetch結果。
