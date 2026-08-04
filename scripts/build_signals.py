from __future__ import annotations

import json
import math
import os
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORLD_BANK_PATH = ROOT / "data" / "world-bank.json"
COMPARISON_PATH = ROOT / "data" / "comparison.json"
MATERIALS_PATH = ROOT / "config" / "materials.json"
RULES_PATH = ROOT / "config" / "signal-rules.json"
OUTPUT_PATH = ROOT / "data" / "signals.json"

MIN_OBSERVATIONS = 13


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def finite_float(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RuntimeError(f"{label}不是有限數字")
    return number


def round_number(value: float, decimals: int = 6) -> int | float:
    rounded = round(value, decimals)
    return int(rounded) if float(rounded).is_integer() else rounded


def percent_change(current: float, previous: float) -> float:
    if previous == 0:
        raise RuntimeError("基期價格為0，無法計算變化率")
    return (current - previous) / previous * 100


def validate_observations(series: dict[str, Any], material_id: str) -> list[dict[str, Any]]:
    raw = series.get("observations")
    if not isinstance(raw, list) or len(raw) < MIN_OBSERVATIONS:
        raise RuntimeError(f"{material_id}至少需要{MIN_OBSERVATIONS}筆月資料")

    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError(f"{material_id}觀測值格式錯誤")
        period = str(item.get("period") or "")
        if len(period) != 7 or period[4] != "-" or not period[:4].isdigit() or not period[5:].isdigit():
            raise RuntimeError(f"{material_id}月份格式錯誤：{period}")
        month = int(period[5:])
        if month < 1 or month > 12:
            raise RuntimeError(f"{material_id}月份格式錯誤：{period}")
        if period in seen:
            raise RuntimeError(f"{material_id}月份重複：{period}")
        seen.add(period)
        value = finite_float(item.get("value"), f"{material_id} {period}價格")
        if value <= 0:
            raise RuntimeError(f"{material_id} {period}價格必須大於0")
        observations.append({"period": period, "value": value})

    periods = [item["period"] for item in observations]
    if periods != sorted(periods):
        raise RuntimeError(f"{material_id}月份不是遞增排列")
    return observations


def source_corroboration(
    material_id: str,
    comparison_material: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    if not comparison_material:
        return {
            "status": "NOT_AVAILABLE",
            "label": "FRED尚無比較資料",
            "confidence": "MEDIUM",
            "latestDifferencePercentVsWorldBank": None,
            "recent12MonthMeanAbsoluteDifferencePercent": None,
            "note": "市場趨勢仍以World Bank主資料計算。",
        }

    if comparison_material.get("comparisonAvailable") is not True:
        return {
            "status": "UNIT_NOT_COMPARABLE",
            "label": "單位不可直接比較",
            "confidence": "MEDIUM",
            "latestDifferencePercentVsWorldBank": None,
            "recent12MonthMeanAbsoluteDifferencePercent": None,
            "note": str(comparison_material.get("comparisonReason") or "FRED僅供旁證，不影響World Bank趨勢訊號。"),
        }

    latest_difference = abs(
        finite_float(
            comparison_material.get("latestDifferencePercentVsWorldBank"),
            f"{material_id}最新來源差異",
        )
    )
    mean_difference = abs(
        finite_float(
            comparison_material.get("recent12MonthMeanAbsoluteDifferencePercent"),
            f"{material_id}近12月來源差異",
        )
    )
    high_limit = finite_float(
        thresholds["sourceHighMeanAbsoluteDifferencePercent"], "來源高度一致門檻"
    )
    medium_limit = finite_float(
        thresholds["sourceMediumMeanAbsoluteDifferencePercent"], "來源大致一致門檻"
    )

    if mean_difference <= high_limit and latest_difference <= high_limit:
        status, label, confidence = "HIGH_AGREEMENT", "兩來源高度一致", "HIGH"
    elif mean_difference <= medium_limit and latest_difference <= medium_limit:
        status, label, confidence = "MEDIUM_AGREEMENT", "兩來源大致一致", "MEDIUM"
    else:
        status, label, confidence = "REVIEW_REQUIRED", "兩來源差異需複核", "LOW"

    return {
        "status": status,
        "label": label,
        "confidence": confidence,
        "latestDifferencePercentVsWorldBank": round_number(
            finite_float(
                comparison_material.get("latestDifferencePercentVsWorldBank"),
                f"{material_id}最新來源差異",
            )
        ),
        "recent12MonthMeanAbsoluteDifferencePercent": round_number(mean_difference),
        "note": "FRED僅作交叉核對，不會改寫World Bank趨勢訊號。",
    }


def determine_trend(changes: dict[str, float], thresholds: dict[str, Any]) -> dict[str, str]:
    one_month = changes["oneMonthPercent"]
    three_month = changes["threeMonthPercent"]
    six_month = changes["sixMonthPercent"]
    reversal = finite_float(thresholds["shortTermReversal1MonthPercent"], "短期反轉門檻")

    if one_month >= reversal and three_month <= -reversal:
        return {
            "code": "DOWNTREND_REBOUND",
            "label": "跌勢中的短期反彈",
            "summary": "最近一個月回升，但三個月方向仍為下跌。",
        }
    if one_month <= -reversal and three_month >= reversal:
        return {
            "code": "UPTREND_PULLBACK",
            "label": "漲勢中的短期回檔",
            "summary": "最近一個月回落，但三個月方向仍為上漲。",
        }
    if one_month < 0 and three_month < 0 and six_month < 0:
        return {
            "code": "BROAD_DOWNTREND",
            "label": "短中期同步下跌",
            "summary": "一個月、三個月與六個月變化率皆為負值。",
        }
    if one_month > 0 and three_month > 0 and six_month > 0:
        return {
            "code": "BROAD_UPTREND",
            "label": "短中期同步上漲",
            "summary": "一個月、三個月與六個月變化率皆為正值。",
        }
    if abs(three_month) < 3 and abs(six_month) < 5:
        return {
            "code": "RANGE_BOUND",
            "label": "區間盤整",
            "summary": "三個月與六個月變化仍在盤整區間。",
        }
    return {
        "code": "MIXED",
        "label": "多空訊號交錯",
        "summary": "不同觀察期間的方向不一致，需避免只引用單一月份。",
    }


def determine_signal(
    changes: dict[str, float],
    latest_vs_average_12: float,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    three = changes["threeMonthPercent"]
    six = changes["sixMonthPercent"]
    strong_down = finite_float(thresholds["strongDecrease3MonthPercent"], "強跌門檻")
    down_3 = finite_float(thresholds["decrease3MonthPercent"], "三月下跌門檻")
    down_6 = finite_float(thresholds["decrease6MonthPercent"], "六月下跌門檻")
    strong_up = finite_float(thresholds["strongIncrease3MonthPercent"], "強漲門檻")
    up_3 = finite_float(thresholds["increase3MonthPercent"], "三月上漲門檻")
    up_6 = finite_float(thresholds["increase6MonthPercent"], "六月上漲門檻")

    if three <= strong_down or (three <= down_3 and six <= down_6):
        return {
            "code": "NEGOTIATE_REDUCTION",
            "label": "降價議價機會",
            "priority": "HIGH",
            "marketInterpretation": "短中期市場價格明顯回落，供應商若仍沿用高價基期，應要求重新檢討。",
            "recommendedAction": "要求供應商以相同材料、相同單位及相同基準月份重算，提出降價、折讓或價格回饋方案。",
            "supplierClaimCheck": "若供應商提出漲價，應優先要求說明採購落後期、舊庫存消化及材料占比，不能只引用單月高點。",
        }
    if three <= down_3 or (six <= down_6 and latest_vs_average_12 < 0):
        return {
            "code": "CHALLENGE_INCREASE",
            "label": "挑戰漲價依據",
            "priority": "MEDIUM",
            "marketInterpretation": "市場方向偏弱或最新價格低於近12月平均，全面漲價的市場依據有限。",
            "recommendedAction": "要求供應商拆分原料、加工、能源、匯率與運費影響，並以可驗證的基準期證明漲價幅度。",
            "supplierClaimCheck": "不宜接受只寫『原料上漲』的概括理由；應核對實際材料占比及價格傳導時間。",
        }
    if three >= strong_up or (three >= up_3 and six >= up_6):
        return {
            "code": "VERIFY_STRONG_INCREASE",
            "label": "上漲壓力需核實",
            "priority": "HIGH",
            "marketInterpretation": "短中期市場價格明顯上升，可能形成成本壓力，但不等於供應商可按原料漲幅等比例調價。",
            "recommendedAction": "要求供應商提供材料占比、實際採購月份、庫存週期及價格公式，計算原料變動對成品價格的合理傳導幅度。",
            "supplierClaimCheck": "只承認與實際成本結構相符的影響；保留價格回落時對稱調降條款。",
        }
    if three >= up_3 or six >= up_6:
        return {
            "code": "VERIFY_INCREASE",
            "label": "核實成本傳導",
            "priority": "MEDIUM",
            "marketInterpretation": "市場出現上行壓力，但仍須確認供應商是否已在既有價格中吸收或提前採購。",
            "recommendedAction": "先核對成本結構與採購落後期，再討論暫時性附加費、分段調整或價格重議機制。",
            "supplierClaimCheck": "避免直接套用市場漲幅；合理成品漲幅應為材料占比乘以可歸屬的材料變化率。",
        }
    return {
        "code": "MONITOR_AND_HOLD",
        "label": "盤整觀察",
        "priority": "LOW",
        "marketInterpretation": "市場尚未形成明確單向趨勢，單靠原料行情不足以支持大幅調價。",
        "recommendedAction": "維持現價或短期報價，設定下一個檢討月份與觸發門檻，避免在方向不明時一次性調價。",
        "supplierClaimCheck": "議價重點轉向年度用量、付款條件、交期、運費與價格有效期。",
    }


def build_material_signal(
    material: dict[str, Any],
    wb_series: dict[str, Any],
    comparison_material: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    material_id = material["id"]
    observations = validate_observations(wb_series, material_id)
    latest = observations[-1]
    latest_value = latest["value"]

    anchors = {
        "oneMonthPercent": observations[-2]["value"],
        "threeMonthPercent": observations[-4]["value"],
        "sixMonthPercent": observations[-7]["value"],
        "twelveMonthPercent": observations[-13]["value"],
    }
    changes = {
        key: round_number(percent_change(latest_value, anchor))
        for key, anchor in anchors.items()
    }

    last_12_values = [item["value"] for item in observations[-12:]]
    average_12 = statistics.fmean(last_12_values)
    high_12 = max(last_12_values)
    low_12 = min(last_12_values)
    range_position = 50.0 if high_12 == low_12 else (latest_value - low_12) / (high_12 - low_12) * 100
    monthly_returns = [
        percent_change(observations[index]["value"], observations[index - 1]["value"])
        for index in range(len(observations) - 11, len(observations))
    ]
    monthly_volatility = statistics.pstdev(monthly_returns) if len(monthly_returns) > 1 else 0.0
    latest_vs_average_12 = percent_change(latest_value, average_12)

    low_percentile = finite_float(thresholds["rangeLowPercentile"], "12月低檔門檻")
    high_percentile = finite_float(thresholds["rangeHighPercentile"], "12月高檔門檻")
    if range_position <= low_percentile:
        position_label = "近12月低檔"
    elif range_position >= high_percentile:
        position_label = "近12月高檔"
    else:
        position_label = "近12月中間區間"

    trend = determine_trend({key: float(value) for key, value in changes.items()}, thresholds)
    signal = determine_signal(
        {key: float(value) for key, value in changes.items()}, latest_vs_average_12, thresholds
    )
    corroboration = source_corroboration(material_id, comparison_material, thresholds)

    evidence = [
        f"3個月變化 {float(changes['threeMonthPercent']):+.2f}%",
        f"6個月變化 {float(changes['sixMonthPercent']):+.2f}%",
        f"最新價相對近12月平均 {latest_vs_average_12:+.2f}%",
        f"目前位於近12月價格區間的 {range_position:.1f}% 位置",
    ]

    return {
        "id": material_id,
        "nameZh": material["nameZh"],
        "nameEn": material["nameEn"],
        "currency": wb_series.get("currency") or material.get("currency") or "USD",
        "displayUnit": wb_series.get("displayUnit") or material.get("unit"),
        "sourceUnit": wb_series.get("sourceUnit"),
        "latestPeriod": latest["period"],
        "latestValue": round_number(latest_value),
        "changes": changes,
        "twelveMonthStatistics": {
            "average": round_number(average_12),
            "high": round_number(high_12),
            "low": round_number(low_12),
            "latestVsAveragePercent": round_number(latest_vs_average_12),
            "rangePositionPercent": round_number(range_position),
            "positionLabel": position_label,
            "monthlyReturnVolatilityPercent": round_number(monthly_volatility),
        },
        "trend": trend,
        "negotiationSignal": signal,
        "sourceCorroboration": corroboration,
        "evidence": evidence,
        "limitations": [
            "訊號只反映World Bank原材料月度價格方向，不等於供應商成品成本變動。",
            "應另行核對材料成本占比、採購落後期、庫存、匯率、運費、加工與能源成本。",
        ],
    }


def build_signals_payload(
    world_bank: dict[str, Any],
    comparison: dict[str, Any],
    materials: list[dict[str, Any]],
    rules: dict[str, Any],
    generated_at: str,
) -> dict[str, Any]:
    if world_bank.get("source") != "WORLD_BANK_PINK_SHEET" or world_bank.get("isRealData") is not True:
        raise RuntimeError("world-bank.json不是正式World Bank資料")
    if comparison.get("isRealData") is not True or comparison.get("primarySource") != "WORLD_BANK_PINK_SHEET":
        raise RuntimeError("comparison.json不是正式比較資料")
    if rules.get("primarySource") != "WORLD_BANK_PINK_SHEET":
        raise RuntimeError("signal-rules.json主要來源設定錯誤")
    thresholds = rules.get("thresholds")
    if not isinstance(thresholds, dict):
        raise RuntimeError("signal-rules.json缺少thresholds")

    output: dict[str, Any] = {}
    for material in materials:
        material_id = material["id"]
        try:
            wb_series = world_bank["series"][material_id]
        except KeyError as exc:
            raise RuntimeError(f"缺少World Bank系列：{material_id}") from exc
        comparison_material = comparison.get("materials", {}).get(material_id)
        output[material_id] = build_material_signal(
            material, wb_series, comparison_material, thresholds
        )

    priority_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    signal_counts: dict[str, int] = {}
    for item in output.values():
        priority = item["negotiationSignal"]["priority"]
        priority_counts[priority] += 1
        code = item["negotiationSignal"]["code"]
        signal_counts[code] = signal_counts.get(code, 0) + 1

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "isRealData": True,
        "latestPeriod": world_bank["dataset"]["latestPeriod"],
        "primarySource": "WORLD_BANK_PINK_SHEET",
        "comparisonSource": "FRED",
        "signalPolicy": rules["policy"],
        "thresholds": thresholds,
        "summary": {
            "materialCount": len(output),
            "priorityCounts": priority_counts,
            "signalCounts": signal_counts,
        },
        "materials": output,
    }


def main() -> None:
    generated_at = utc_now_iso()
    payload = build_signals_payload(
        read_json(WORLD_BANK_PATH),
        read_json(COMPARISON_PATH),
        read_json(MATERIALS_PATH),
        read_json(RULES_PATH),
        generated_at,
    )
    write_json_atomic(OUTPUT_PATH, payload)
    print(
        "Signal build success: "
        f"materials={payload['summary']['materialCount']}, "
        f"priority={payload['summary']['priorityCounts']}"
    )


if __name__ == "__main__":
    main()
