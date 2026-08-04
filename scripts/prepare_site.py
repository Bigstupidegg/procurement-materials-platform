from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "_site"
INDEX = ROOT / "index.html"
ASSETS = ROOT / "assets"
DATA = ROOT / "data"

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
)
SCRIPTS = (
    '<script src="./assets/world-bank-live.js"></script>',
    '<script src="./assets/source-comparison.js"></script>',
    '<script src="./assets/trend-signals.js"></script>',
    '<script src="./assets/supplier-rationality.js"></script>',
)
APP_SCRIPT = '<script src="./assets/app.js"></script>'


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

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    shutil.copy2(INDEX, SITE / "index.html")
    shutil.copytree(ASSETS, SITE / "assets")
    shutil.copytree(DATA, SITE / "data")
    (SITE / ".nojekyll").touch()

    site_index = SITE / "index.html"
    html = site_index.read_text(encoding="utf-8")
    site_index.write_text(inject_resources(html), encoding="utf-8")
    signal_state = "included" if (DATA / "signals.json").is_file() else "pending"
    print(f"Site preparation success: trend signals={signal_state}, supplier rationality=included")


if __name__ == "__main__":
    prepare_site()
