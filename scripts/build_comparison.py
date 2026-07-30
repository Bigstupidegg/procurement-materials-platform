from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORLD_BANK_PATH = ROOT / "data" / "world-bank.json"
FRED_PATH = ROOT / "data" / "fred.json"
COMPARISON_OUTPUT_PATH = ROOT / "data" / "comparison.json"
MATERIALS_PATH = ROOT / "config" / "materials.json"

UNIT_RULES = {
    "zinc": {"worldBank": ["/mt"], "fred": ["metric ton"]},
    "copper": {"worldBank": ["/mt"], "fred": ["metric ton"]},
    "aluminium": {"worldBank": ["/mt"], "fred": ["metric ton"]},
    "nickel": {"worldBank": ["/mt"], "fred": ["metric ton"]},
    "crude_oil": {"worldBank": ["/bbl"], "fred": ["barrel"]},
    "natural_gas": {"worldBank": ["/mmbtu"], "fred": ["million btu", "mmbtu"]},
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


def finite_number(value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError("比較資料含非有限數字")
    return number


def observation_map(series: dict[str, Any]) -> dict[str, float]:
    items = series.get("observations")
    if not isinstance(items, list):
        raise RuntimeError("系列缺少observations")
    result: dict[str, float] = {}
    for item in items:
        period = str(item["period"])
        if period in result:
            raise RuntimeError(f"比較資料月份重複：{period}")
        result[period] = finite_number(item["value"])
    return result


def round_or_int(value: float, decimals: int = 6) -> int | float:
    rounded = round(value, decimals)
    return int(rounded) if float(rounded).is_integer() else rounded


def build_material_comparison(
    material: dict[str, Any],
    wb_series: dict[str, Any],
    fred_series: dict[str, Any],
) -> dict[str, Any]:
    material_id = material["id"]
    wb_map = observation_map(wb_series)
    fred_map = observation_map(fred_series)
    overlap = sorted(set(wb_map) & set(fred_map))
    if not overlap:
        raise RuntimeError(f"{material_id}沒有重疊月份")

    base = {
        "id": material_id,
        "nameZh": material["nameZh"],
        "nameEn": material["nameEn"],
        "worldBank": {
            "latestPeriod": wb_series["latestPeriod"],
            "displayUnit": wb_series["displayUnit"],
            "sourceUnit": wb_series.get("sourceUnit"),
        },
        "fred": {
            "seriesId": fred_series["fredSeriesId"],
            "latestPeriod": fred_series["latestPeriod"],
            "displayUnit": fred_series["displayUnit"],
            "sourceUnits": fred_series["units"],
        },
        "overlapCount": len(overlap),
        "firstOverlapPeriod": overlap[0],
        "latestOverlapPeriod": overlap[-1],
    }

    rule = UNIT_RULES.get(material_id)
    wb_unit_text = str(wb_series.get("sourceUnit") or "").casefold()
    fred_unit_text = str(fred_series.get("units") or "").casefold()
    unit_match = bool(rule) and any(x in wb_unit_text for x in rule["worldBank"]) and any(
        x in fred_unit_text for x in rule["fred"]
    )
    if not unit_match:
        reason = (
            "World Bank與FRED的鐵礦砂單位標示不同（dmtu與metric ton），"
            "未經正式換算規則確認前不計算差異。"
            if material_id == "iron_ore"
            else "World Bank與FRED的單位未通過相容性檢查，因此不計算差異。"
        )
        return {
            **base,
            "comparisonAvailable": False,
            "comparisonReason": reason,
            "latestWorldBankValue": round_or_int(wb_map[overlap[-1]]),
            "latestFredValue": round_or_int(fred_map[overlap[-1]]),
            "observations": [],
        }

    compared: list[dict[str, Any]] = []
    for period in overlap:
        wb_value = wb_map[period]
        fred_value = fred_map[period]
        if wb_value == 0:
            raise RuntimeError(f"{material_id} World Bank值為0，無法計算百分比")
        difference = fred_value - wb_value
        difference_pct = difference / wb_value * 100
        compared.append({
            "period": period,
            "worldBankValue": round_or_int(wb_value),
            "fredValue": round_or_int(fred_value),
            "differenceFredMinusWorldBank": round_or_int(difference),
            "differencePercentVsWorldBank": round_or_int(difference_pct),
        })

    recent = compared[-12:]
    absolute_pcts = [abs(float(item["differencePercentVsWorldBank"])) for item in recent]
    latest = compared[-1]
    return {
        **base,
        "comparisonAvailable": True,
        "comparisonReason": None,
        "latestWorldBankValue": latest["worldBankValue"],
        "latestFredValue": latest["fredValue"],
        "latestDifferencePercentVsWorldBank": latest["differencePercentVsWorldBank"],
        "recent12MonthMeanAbsoluteDifferencePercent": round_or_int(sum(absolute_pcts) / len(absolute_pcts)),
        "recent12MonthMaxAbsoluteDifferencePercent": round_or_int(max(absolute_pcts)),
        "observations": compared,
    }


def build_comparison_payload(
    world_bank: dict[str, Any],
    fred: dict[str, Any],
    materials: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    if world_bank.get("source") != "WORLD_BANK_PINK_SHEET" or world_bank.get("isRealData") is not True:
        raise RuntimeError("world-bank.json不是正式World Bank資料")
    if fred.get("source") != "FRED" or fred.get("isRealData") is not True:
        raise RuntimeError("fred.json不是正式FRED資料")

    output: dict[str, Any] = {}
    for material in materials:
        material_id = material["id"]
        try:
            wb_series = world_bank["series"][material_id]
            fred_series = fred["series"][material_id]
        except KeyError as exc:
            raise RuntimeError(f"缺少比較系列：{material_id}") from exc
        output[material_id] = build_material_comparison(material, wb_series, fred_series)

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "isRealData": True,
        "primarySource": "WORLD_BANK_PINK_SHEET",
        "comparisonSource": "FRED",
        "sourcePolicy": {
            "worldBankRole": "PRIMARY",
            "fredRole": "INDEPENDENT_COMPARISON_ONLY",
            "automaticFallback": False,
            "note": "FRED僅供研究與交叉比較，不會自動取代World Bank主資料。",
        },
        "materials": output,
    }


def main() -> None:
    generated_at = utc_now_iso()
    world_bank = read_json(WORLD_BANK_PATH)
    fred = read_json(FRED_PATH)
    materials = read_json(MATERIALS_PATH)
    payload = build_comparison_payload(world_bank, fred, materials, generated_at)
    write_json_atomic(COMPARISON_OUTPUT_PATH, payload)
    available = sum(1 for item in payload["materials"].values() if item["comparisonAvailable"])
    print(f"Comparison build success: comparable={available}, total={len(payload['materials'])}")


if __name__ == "__main__":
    main()
