from __future__ import annotations

import csv
import io
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
MATERIALS_PATH = ROOT / "config" / "materials.json"
FRED_OUTPUT_PATH = ROOT / "data" / "fred.json"
STATUS_OUTPUT_PATH = ROOT / "data" / "status.json"

FRED_GRAPH_CSV_ENDPOINT = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_ALLOWED_HOST = "fred.stlouisfed.org"
SERIES_ID_PATTERN = re.compile(r"^[A-Z0-9_]+$")
DATE_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])$")
MIN_OBSERVATIONS_PER_SERIES = 60
MAX_CSV_BYTES = 10 * 1024 * 1024
MISSING_VALUES = {"", ".", "..", "...", "na", "n/a", "null"}

# FRED公開圖表CSV只提供日期與觀測值，不含系列中繼資料。
# 單位在此明確保存，並由比較程式再次執行相容性檢查。
SERIES_UNIT_METADATA = {
    "zinc": {"units": "U.S. Dollars per Metric Ton", "unitsShort": "USD/mt"},
    "copper": {"units": "U.S. Dollars per Metric Ton", "unitsShort": "USD/mt"},
    "aluminium": {"units": "U.S. Dollars per Metric Ton", "unitsShort": "USD/mt"},
    "nickel": {"units": "U.S. Dollars per Metric Ton", "unitsShort": "USD/mt"},
    "iron_ore": {"units": "U.S. Dollars per Metric Ton", "unitsShort": "USD/mt"},
    "crude_oil": {"units": "U.S. Dollars per Barrel", "unitsShort": "USD/bbl"},
    "natural_gas": {"units": "U.S. Dollars per Million BTU", "unitsShort": "USD/MMBtu"},
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def validate_series_id(value: str) -> str:
    series_id = value.strip()
    if not SERIES_ID_PATTERN.fullmatch(series_id):
        raise RuntimeError(f"FRED系列代碼格式不正確：{value!r}")
    return series_id


def validate_endpoint(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != FRED_ALLOWED_HOST:
        raise RuntimeError(f"FRED下載端點不在允許清單：{url}")


def safe_request_csv(session: requests.Session, series_id: str) -> tuple[str, dict[str, Any]]:
    series_id = validate_series_id(series_id)
    validate_endpoint(FRED_GRAPH_CSV_ENDPOINT)
    try:
        response = session.get(
            FRED_GRAPH_CSV_ENDPOINT,
            params={"id": series_id, "cosd": "1960-01-01"},
            headers={
                "User-Agent": "procurement-materials-platform/1.0 (+GitHub Actions)",
                "Accept": "text/csv,application/octet-stream;q=0.9,text/plain;q=0.8",
            },
            timeout=(15, 60),
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"FRED CSV連線失敗：{type(exc).__name__}") from exc

    validate_endpoint(response.url)
    if response.status_code != 200:
        raise RuntimeError(f"FRED CSV回傳HTTP {response.status_code}")

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    allowed_types = {"text/csv", "application/csv", "application/octet-stream", "text/plain"}
    if content_type and content_type not in allowed_types:
        raise RuntimeError(f"FRED CSV Content-Type異常：{content_type}")

    raw = response.content
    if not raw or len(raw) > MAX_CSV_BYTES:
        raise RuntimeError(f"FRED CSV檔案大小異常：{len(raw)} bytes")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuntimeError("FRED CSV不是有效UTF-8文字") from exc
    if series_id not in text[:500]:
        raise RuntimeError(f"FRED CSV標頭未包含系列代碼：{series_id}")

    metadata = {
        "requestedUrl": response.request.url,
        "finalUrl": response.url,
        "httpStatus": response.status_code,
        "contentType": content_type or None,
        "fileSizeBytes": len(raw),
        "etag": response.headers.get("ETag"),
        "lastModified": response.headers.get("Last-Modified"),
    }
    return text, metadata


def to_positive_finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if text.casefold() in MISSING_VALUES:
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def parse_csv_observations(series_id: str, csv_text: str) -> list[dict[str, Any]]:
    series_id = validate_series_id(series_id)
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = [str(name or "").strip() for name in (reader.fieldnames or [])]
    if len(fieldnames) < 2:
        raise RuntimeError(f"{series_id} CSV欄位不足")

    date_field = next((name for name in fieldnames if name.casefold() in {"observation_date", "date"}), None)
    value_field = next((name for name in fieldnames if name.casefold() == series_id.casefold()), None)
    if date_field is None:
        raise RuntimeError(f"{series_id} CSV缺少日期欄")
    if value_field is None:
        non_date = [name for name in fieldnames if name != date_field]
        if len(non_date) != 1:
            raise RuntimeError(f"{series_id} CSV找不到唯一數值欄")
        value_field = non_date[0]

    observations: list[dict[str, Any]] = []
    for row in reader:
        date_text = str(row.get(date_field) or "").strip()
        match = DATE_PATTERN.fullmatch(date_text)
        if not match:
            raise RuntimeError(f"{series_id}含無效日期：{date_text}")
        value = to_positive_finite_float(row.get(value_field))
        if value is None:
            continue
        period = f"{match.group('year')}-{match.group('month')}"
        observations.append({
            "period": period,
            "value": int(value) if value.is_integer() else value,
        })

    if len(observations) < MIN_OBSERVATIONS_PER_SERIES:
        raise RuntimeError(f"{series_id}有效資料不足：{len(observations)}")
    periods = [item["period"] for item in observations]
    if periods != sorted(periods):
        raise RuntimeError(f"{series_id}月份不是遞增排列")
    if len(periods) != len(set(periods)):
        raise RuntimeError(f"{series_id}月份有重複；所選系列可能不是月頻資料")
    return observations


def fetch_series(
    session: requests.Session,
    material: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    material_id = material["id"]
    if material_id not in SERIES_UNIT_METADATA:
        raise RuntimeError(f"缺少FRED單位設定：{material_id}")
    series_id = validate_series_id(material["fredSeriesCode"])
    csv_text, download_metadata = safe_request_csv(session, series_id)
    observations = parse_csv_observations(series_id, csv_text)
    metadata = SERIES_UNIT_METADATA[material_id]

    return {
        "id": material_id,
        "nameZh": material["nameZh"],
        "nameEn": material["nameEn"],
        "currency": material["currency"],
        "displayUnit": material["unit"],
        "fredSeriesId": series_id,
        "fredSeriesUrl": f"https://fred.stlouisfed.org/series/{series_id}",
        "retrievedVia": "FRED_PUBLIC_GRAPH_CSV_EXPORT",
        "retrievedAt": generated_at,
        "title": f"{material['nameEn']} ({series_id})",
        "frequency": "Monthly",
        "frequencyShort": "M",
        "units": metadata["units"],
        "unitsShort": metadata["unitsShort"],
        "seasonalAdjustment": "Not Seasonally Adjusted",
        "lastUpdated": download_metadata.get("lastModified"),
        "observationStart": observations[0]["period"],
        "observationEnd": observations[-1]["period"],
        "notes": "觀測值取自FRED公開圖表CSV匯出；單位由專案設定保存並在比較前再次驗證。",
        "download": download_metadata,
        "observationCount": len(observations),
        "firstPeriod": observations[0]["period"],
        "latestPeriod": observations[-1]["period"],
        "observations": observations,
    }


def build_fred_payload(
    materials: list[dict[str, Any]],
    series_payload: dict[str, dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    expected_ids = [material["id"] for material in materials]
    if sorted(series_payload) != sorted(expected_ids):
        raise RuntimeError("FRED系列集合與材料設定不一致")
    latest_periods = {material_id: series_payload[material_id]["latestPeriod"] for material_id in expected_ids}
    latest_common_period = min(latest_periods.values())
    latest_available_period = max(latest_periods.values())
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "source": "FRED",
        "isRealData": True,
        "frequency": "monthly",
        "role": "INDEPENDENT_COMPARISON_ONLY",
        "dataset": {
            "name": "Federal Reserve Economic Data (FRED)",
            "downloadMethod": "PUBLIC_GRAPH_CSV_EXPORT",
            "graphCsvEndpoint": FRED_GRAPH_CSV_ENDPOINT,
            "apiKeyRequired": False,
            "seriesCount": len(expected_ids),
            "latestCommonPeriod": latest_common_period,
            "latestAvailablePeriod": latest_available_period,
            "latestPeriods": latest_periods,
        },
        "series": series_payload,
        "failedSeries": [],
    }


def build_status(
    generated_at: str,
    fred_payload: dict[str, Any],
    previous_status: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_status = previous_status or {}
    world_bank = previous_status.get("worldBank")
    if not isinstance(world_bank, dict) or world_bank.get("status") != "SUCCESS":
        raise RuntimeError("status.json缺少有效的World Bank成功狀態")
    dataset = fred_payload["dataset"]
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "dataMode": "WORLD_BANK_PRIMARY",
        "worldBank": world_bank,
        "fred": {
            "status": "SUCCESS",
            "role": "INDEPENDENT_COMPARISON_ONLY",
            "lastAttemptAt": generated_at,
            "lastSuccessAt": generated_at,
            "latestCommonPeriod": dataset["latestCommonPeriod"],
            "latestAvailablePeriod": dataset["latestAvailablePeriod"],
            "latestPeriods": dataset["latestPeriods"],
            "seriesCount": dataset["seriesCount"],
            "downloadMethod": dataset["downloadMethod"],
            "apiKeyRequired": False,
        },
        "isStale": bool(previous_status.get("isStale", False)),
        "warnings": list(previous_status.get("warnings") or []),
    }


def main() -> None:
    generated_at = utc_now_iso()
    materials = read_json(MATERIALS_PATH)
    if not isinstance(materials, list) or not materials:
        raise RuntimeError("materials.json格式錯誤")

    session = requests.Session()
    series_payload: dict[str, dict[str, Any]] = {}
    for material in materials:
        material_id = material["id"]
        series_id = material["fredSeriesCode"]
        print(f"Sync FRED public CSV: material={material_id}, series={series_id}")
        series_payload[material_id] = fetch_series(session, material, generated_at)

    fred_payload = build_fred_payload(materials, series_payload, generated_at)
    previous_status = read_json(STATUS_OUTPUT_PATH) if STATUS_OUTPUT_PATH.exists() else None
    status_payload = build_status(generated_at, fred_payload, previous_status)

    # 七個系列全部下載、解析與驗證成功後才原子置換，避免部分成功覆蓋上一版資料。
    write_json_atomic(FRED_OUTPUT_PATH, fred_payload)
    write_json_atomic(STATUS_OUTPUT_PATH, status_payload)
    print(
        "FRED sync success: "
        f"series={fred_payload['dataset']['seriesCount']}, "
        f"latestCommonPeriod={fred_payload['dataset']['latestCommonPeriod']}"
    )


if __name__ == "__main__":
    main()
