from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"
INDEX = ROOT / "index.html"
ASSETS = ROOT / "assets"
DATA = ROOT / "data"
RELEASE = ROOT / "config" / "release.json"

REQUIRED_DATA = (
    "world-bank.json",
    "fred.json",
    "comparison.json",
    "status.json",
    "should-cost-rules.json",
)
STYLESHEETS = (
    '  <link rel="icon" href="./assets/favicon.svg" type="image/svg+xml">',
    '  <link rel="stylesheet" href="./assets/source-comparison.css">',
    '  <link rel="stylesheet" href="./assets/trend-signals.css">',
    '  <link rel="stylesheet" href="./assets/supplier-rationality.css">',
    '  <link rel="stylesheet" href="./assets/negotiation-report.css">',
)
SCRIPTS = (
    '<script src="./assets/world-bank-live.js"></script>',
    '<script src="./assets/source-comparison.js"></script>',
    '<script src="./assets/trend-signals.js"></script>',
    '<script src="./assets/supplier-rationality.js"></script>',
    '<script src="./assets/negotiation-report.js"></script>',
)
APP_SCRIPT = '<script src="./assets/app.js"></script>'


def load_release() -> dict:
    if not RELEASE.is_file():
        raise RuntimeError("缺少 config/release.json，無法識別正式版本")
    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    version = str(release.get("version", "")).strip()
    if not version:
        raise RuntimeError("config/release.json 缺少 version")
    return release


def normalize_index_identity(html: str, version: str) -> str:
    replacements = {
        '<title>國際原材料價格與採購分析平台（原型・示範資料）v1.2.1</title>':
            f'<title>國際原材料價格與採購分析平台｜v{version}</title>',
        'International Raw Materials Procurement Analytics（介面原型 v1.2.1）':
            f'International Raw Materials Procurement Analytics（v{version}）',
        '<span class="demo-pill">示範資料原型・非真實市場行情</span>':
            '<span class="demo-pill">正式市場資料・採購決策支援</span>',
        '本平台目前為<b>介面原型（Prototype）v1.2.1</b>，所有價格、走勢與計算結果均為<b>示範資料</b>，未連接任何外部市場資料來源，<b>不得作為採購、財務或投資決策依據</b>。':
            f'本平台目前為 <b>v{version} 採購決策支援版本</b>；市場資料以 <b>World Bank Pink Sheet</b> 為主要來源，FRED 僅作獨立交叉核對。分析結果不代表供應商實際成本，也不會自動接受或拒絕供應商調價。',
        '七項核心原材料之示範價格卡片，供採購人員快速掌握變動趨勢。所有數值皆為示範資料，僅供介面展示。':
            '七項核心原材料市場價格卡片，供採購人員快速掌握變動趨勢；正式載入後以 World Bank Pink Sheet 月度資料為主。',
        '選擇單一或多項材料進行比較，並切換觀察期間與顯示模式。滑鼠移至圖上可查看該日期詳細示範資料。':
            '選擇單一或多項材料進行比較，並切換觀察期間與顯示模式；正式載入後圖表與 CSV 使用同站的市場資料。',
        '原材料示範價格走勢圖，詳細數值請參考下方統計卡片與圖例':
            '原材料市場價格走勢圖，詳細數值請參考下方統計卡片與圖例',
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


def normalize_live_asset_identity(version: str) -> None:
    live_asset = SITE / "assets" / "world-bank-live.js"
    if not live_asset.is_file():
        raise RuntimeError("找不到 world-bank-live.js，無法套用 v2.3 正式版本識別")
    text = live_asset.read_text(encoding="utf-8")
    text = text.replace(
        "International Raw Materials Procurement Analytics（World Bank 月度資料版 v1.3.0）",
        f"International Raw Materials Procurement Analytics（World Bank 月度資料版 v{version}）",
    )
    text = text.replace(
        "document.title='國際原材料價格與採購分析平台｜World Bank 月度資料';",
        f"document.title='國際原材料價格與採購分析平台｜World Bank 月度資料 v{version}';",
    )
    live_asset.write_text(text, encoding="utf-8")


def inject_resources(html: str) -> str:
    if "</head>" not in html:
        raise RuntimeError("找不到head結尾，無法加入網站資源")
    if APP_SCRIPT not in html:
        raise RuntimeError("找不到app.js引用，無法加入正式前端")

    for marker in STYLESHEETS:
        href = marker.split('href="', 1)[1].split('"', 1)[0]
        if href not in html:
            html = html.replace("</head>", marker + "\n</head>", 1)

    anchor = APP_SCRIPT
    for script in SCRIPTS:
        if script not in html:
            html = html.replace(anchor, anchor + "\n" + script, 1)
        anchor = script
    return html


def prepare_site() -> None:
    if not INDEX.is_file() or not ASSETS.is_dir() or not DATA.is_dir():
        raise RuntimeError("網站必要檔案或資料夾不存在")
    missing = [name for name in REQUIRED_DATA if not (DATA / name).is_file()]
    if missing:
        raise RuntimeError("缺少正式網站資料檔：" + ", ".join(missing))

    release = load_release()
    version = str(release["version"])

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    shutil.copy2(INDEX, SITE / "index.html")
    shutil.copytree(ASSETS, SITE / "assets")
    shutil.copytree(DATA, SITE / "data")
    (SITE / ".nojekyll").touch()

    site_index = SITE / "index.html"
    html = site_index.read_text(encoding="utf-8")
    html = normalize_index_identity(html, version)
    html = inject_resources(html)
    site_index.write_text(html, encoding="utf-8")
    normalize_live_asset_identity(version)

    signal_state = "included" if (DATA / "signals.json").is_file() else "pending"
    print(
        f"Site preparation success: version={version}, trend signals={signal_state}, "
        "supplier rationality=included, negotiation report=included"
    )


if __name__ == "__main__":
    prepare_site()
