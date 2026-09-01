"""C3.2 scheduled local Shadow runner; formal market writes are impossible."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from zoneinfo import ZoneInfo

try:
    from c3_2_deferred_assembly import assemble_deferred_canonical_business_date
    from c3_2_observation_canonicalization import RawObservation, canonicalize_daily_observations
    from c3_2_observation_migration import V2_COLUMNS
    from c3_2_shadow_observation_store import append_shadow_observation_plan, build_shadow_observation_row, enforce_shadow_write_safety, plan_shadow_observation_append
    from company_market_collector import fetch_browser_quotes, fetch_yfinance_quotes
except ModuleNotFoundError:
    from scripts.c3_2_deferred_assembly import assemble_deferred_canonical_business_date
    from scripts.c3_2_observation_canonicalization import RawObservation, canonicalize_daily_observations
    from scripts.c3_2_observation_migration import V2_COLUMNS
    from scripts.c3_2_shadow_observation_store import append_shadow_observation_plan, build_shadow_observation_row, enforce_shadow_write_safety, plan_shadow_observation_append
    from scripts.company_market_collector import fetch_browser_quotes, fetch_yfinance_quotes


def _raw(row: tuple[str, ...]) -> RawObservation:
    index = {name: position for position, name in enumerate(V2_COLUMNS)}
    return RawObservation(row[index["observation_id"]], row[index["material_id"]], row[index["source_id"]], row[index["source_date"]], float(row[index["price"]]), row[index["currency"]], row[index["unit"]], row[index["market_type"]], row[index["observation_at"]], row[index["observation_kind"]], row[index["source_status"]], row[index["date_parse_status"]])


def run(*, sheet_id: str, credential_file: str, dry_run: bool) -> int:
    enforce_shadow_write_safety()
    import gspread

    collected_at = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    if date.today().weekday() >= 5:
        print("SCHEDULED_SHADOW=NORMAL_SKIP_NON_BUSINESS_DAY execution_at=" + collected_at + " no_smm_backfill=TRUE")
        return 0
    print("SCHEDULED_SHADOW_EXECUTION execution_at=" + collected_at + " missed_recovery=TASK_SCHEDULER_START_WHEN_AVAILABLE")
    quotes = fetch_browser_quotes()
    quotes.update(fetch_yfinance_quotes())
    spreadsheet = gspread.service_account(filename=credential_file).open_by_key(sheet_id)
    v2 = spreadsheet.worksheet("Market_Observation_V2")
    values = v2.get_all_values()
    if not values or tuple(values[0]) != V2_COLUMNS:
        print("SCHEDULED_SHADOW=FAIL_CLOSED reason=V2_SCHEMA_MISMATCH")
        return 1
    candidates = [build_shadow_observation_row(key, quote, evaluated_on=date.today(), collected_at=collected_at) for key, quote in quotes.items() if quote.ok]
    plan = plan_shadow_observation_append(candidates, values[1:])
    if plan.status != "READY":
        print("SCHEDULED_SHADOW=FAIL_CLOSED reason=" + str(plan.failure_reason))
        return 1
    candidate_dates = sorted({row[4] for row in candidates})
    target = max(candidate_dates) if candidate_dates else ""
    observations = [_raw(row) for row in candidates if row[4] == target]
    canonical = canonicalize_daily_observations(target, observations) if target else None
    assembly = assemble_deferred_canonical_business_date(target, canonical.canonical_records) if canonical else None
    print("SCHEDULED_SHADOW mode=" + ("DRY_RUN" if dry_run else "V2_APPEND_ONLY") + " source_success=" + str(len(candidates)) + " v2_append=" + str(len(plan.rows)) + " duplicate_same=" + str(plan.duplicate_same_count))
    print("SHADOW_CANONICAL target=" + (target or "UNAVAILABLE") + " status=" + (canonical.status if canonical else "UNAVAILABLE") + " assembly=" + (assembly.status if assembly else "UNAVAILABLE"))
    if dry_run:
        return 0
    result = append_shadow_observation_plan(sheet_id=sheet_id, credential_file=credential_file, plan=plan)
    print("V2_READBACK status=" + result.status + " appended=" + str(len(result.rows)))
    return 0 if result.status == "APPEND_COMPLETE" or not result.rows else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return run(sheet_id=os.environ.get("GOOGLE_SHEET_ID", ""), credential_file=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
