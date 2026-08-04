from __future__ import annotations

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

FRED_SERIES_ENDPOINT = "https://api.stlouisfed.org/fred/series"
FRED_OBSERVATIONS_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"
FRED_ALLOWED_HOST = "api.stlouisfed.org"
API_KEY_PATTERN = re.compile(r"^[a-z0-9]{32}$")
DATE_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])$")
MIN_OBSERVATIONS_PER_SERIES = 60
MISSING_VALUES = {"", ".", "..", "...", "na", "n/a", "null"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def validate_api_key(value: str) -> str:
    api_key = value.strip()
    if not API_KEY_PATTERN.fullmatch(api_key):
        raise RuntimeError("FRED_API_KEY格式不正確；必須是32碼小寫英數字。")
    return api_key


def validate_endpoint(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != FRED_ALLOWED_HOST:
        raise RuntimeError(f"FRED API端點不在允許清單：{url}")


def safe_request_json(
    session: requests.Session,
    endpoint: str,
    params: dict[str, str],
) -> dict[str, Any]:
    validate_endpoint(endpoint)
    try:
        response = session.get(
            endpoint,
            params=params,
            headers={
                "User-Agent": "procurement-materials-platform/1.0 (+GitHub Actions)",
                "Accept": "application/json",
            },
            timeout=(15, 60),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"FRED API連線失敗：{type(exc).__name__}") from exc

    if response.is_redirect:
        raise RuntimeError("FRED API出現未預期重新導向")

    if response.status_code != 200:
        message = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                message = str(payload.get("error_message") or payload.get("message") or "")
        except ValueError:
            pass
        suffix = f"：{message[:160]}" if message else ""
        raise RuntimeError(f"FRED API回傳HTTP {response.status_code}{suffix}")

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type not in {"application/json", "text/json"}:
        raise RuntimeError(f"FRED API Content-Type異常：{content_type}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("FRED API回傳內容不是有效JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("FRED API回傳JSON最外層不是物件")
    return payload


def parse_series_metadata(series_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("seriess")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise RuntimeError(f"{series_id}系列中繼資料筆數異常")

    item = items[0]
    if item.get("id") != series_id:
        raise RuntimeError(f"{series_id}系列代碼不一致")
    if item.get("frequency_short") != "M":
        raise RuntimeError(f"{series_id}不是月頻資料：{item.get('frequency')}")

    title = str(item.get("title") or "").strip()
    units = str(item.get("units") or "").strip()
    if not title or not units:
        raise RuntimeError(f"{series_id}缺少標題或單位")

    return {
        "title": title,
        "frequency": str(item.get("frequency") or "Monthly"),
        "frequencyShort": "M",
        "units": units,
        "unitsShort": str(item.get("units_short") or "").strip() or None,
        "seasonalAdjustment": str(item.get("seasonal_adjustment") or "").strip() or None,
        "lastUpdated": str(item.get("last_updated") or "").strip() or None,
        "observationStart": str(item.get("observation_start") or "").strip() or None,
        "observationEnd": str(item.get("observation_end") or "").strip() or None,
        "notes": str(item.get("notes") or "").strip() or None,
    }


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


def parse_observations(series_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("observations")
    if not isinstance(raw_items, list):
        raise RuntimeError(f"{series_id}缺少observations陣列")

    observations: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        date_text = str(item.get("date") or "").strip()
        match = DATE_PATTERN.fullmatch(date_text)
        if not match:
            raise RuntimeError(f"{series_id}含無效日期：{date_text}")

        value = to_positive_finite_float(item.get("value"))
        if value is None:
            continue

        period = f"{match.group('year')}-{match.group('month')}"
        observations.append(
            {
                "period": period,
                "value": int(value) if value.is_integer() else value,
            }
        )

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
    api_key: str,
    material: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    series_id = str(material["fredSeriesCode"]).strip()
    common_params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }

    metadata_payload = safe_request_json(session, FRED_SERIES_ENDPOINT, common_params)
    metadata = parse_series_metadata(series_id, metadata_payload)

    observation_payload = safe_request_json(
        session,
        FRED_OBSERVATIONS_ENDPOINT,
        {
            **common_params,
            "observation_start": "1960-01-01",
            "sort_order": "asc",
            "limit": "100000",
        },
    )
    observations = parse_observations(series_id, observation_payload)

    return {
        "id": material["id"],
        "nameZh": material["nameZh"],
        "nameEn": material["nameEn"],
        "currency": material["currency"],
        "displayUnit": material["unit"],
        "fredSeriesId": series_id,
        "fredSeriesUrl": f"https://fred.stlouisfed.org/series/{series_id}",
        "retrievedVia": "FRED_API_JSON",
        "retrievedAt": generated_at,
        **metadata,
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

    latest_periods = {
        material_id: series_payload[material_id]["latestPeriod"]
        for material_id in expected_ids
    }
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
            "downloadMethod": "FRED_API_JSON",
            "apiVersion": "v1",
            "seriesEndpoint": FRED_SERIES_ENDPOINT,
            "observationsEndpoint": FRED_OBSERVATIONS_ENDPOINT,
            "apiKeyRequired": True,
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
            "apiKeyRequired": True,
        },
        "isStale": bool(previous_status.get("isStale", False)),
        "warnings": list(previous_status.get("warnings") or []),
    }


def main() -> None:
    generated_at = utc_now_iso()
    api_key = validate_api_key(os.getenv("FRED_API_KEY", ""))
    materials = read_json(MATERIALS_PATH)
    if not isinstance(materials, list) or not materials:
        raise RuntimeError("materials.json格式錯誤")

    session = requests.Session()
    series_payload: dict[str, dict[str, Any]] = {}
    for material in materials:
        material_id = material["id"]
        series_id = material["fredSeriesCode"]
        print(f"Sync FRED API series: material={material_id}, series={series_id}")
        series_payload[material_id] = fetch_series(session, api_key, material, generated_at)

    fred_payload = build_fred_payload(materials, series_payload, generated_at)
    previous_status = read_json(STATUS_OUTPUT_PATH) if STATUS_OUTPUT_PATH.exists() else None
    status_payload = build_status(generated_at, fred_payload, previous_status)

    # 七個系列全部下載、解析與驗證成功後才原子置換。
    # API Key不寫入JSON、程式輸出或Repository。
    write_json_atomic(FRED_OUTPUT_PATH, fred_payload)
    write_json_atomic(STATUS_OUTPUT_PATH, status_payload)
    print(
        "FRED API sync success: "
        f"series={fred_payload['dataset']['seriesCount']}, "
        f"latestCommonPeriod={fred_payload['dataset']['latestCommonPeriod']}"
    )


if __name__ == "__main__":
    main()
