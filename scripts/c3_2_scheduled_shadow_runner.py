"""C3.2 scheduled local Shadow runner; formal market writes are impossible."""
from __future__ import annotations

import argparse
from datetime import date, datetime
import os
from zoneinfo import ZoneInfo

try:
    from c3_2_deferred_assembly import assemble_deferred_canonical_business_date
    from c3_2_observation_canonicalization import RawObservation, YAHOO_UNCONFIRMED, canonicalize_daily_observations
    from c3_2_observation_migration import V2_COLUMNS
    from c3_2_shadow_observation_store import append_shadow_observation_plan, build_shadow_observation_row, build_yahoo_confirmed_observation_row, enforce_shadow_write_safety, plan_shadow_observation_append
    from company_market_collector import fetch_browser_quotes, fetch_yfinance_historical_close_quotes, fetch_yfinance_quotes
except ModuleNotFoundError:
    from scripts.c3_2_deferred_assembly import assemble_deferred_canonical_business_date
    from scripts.c3_2_observation_canonicalization import RawObservation, YAHOO_UNCONFIRMED, canonicalize_daily_observations
    from scripts.c3_2_observation_migration import V2_COLUMNS
    from scripts.c3_2_shadow_observation_store import append_shadow_observation_plan, build_shadow_observation_row, build_yahoo_confirmed_observation_row, enforce_shadow_write_safety, plan_shadow_observation_append
    from scripts.company_market_collector import fetch_browser_quotes, fetch_yfinance_historical_close_quotes, fetch_yfinance_quotes


def _raw(row: tuple[str, ...]) -> RawObservation:
    index = {name: position for position, name in enumerate(V2_COLUMNS)}
    return RawObservation(row[index["observation_id"]], row[index["material_id"]], row[index["source_id"]], row[index["source_date"]], float(row[index["price"]]), row[index["currency"]], row[index["unit"]], row[index["market_type"]], row[index["observation_at"]], row[index["observation_kind"]], row[index["source_status"]], row[index["date_parse_status"]])


def _business_source_date(value: object) -> bool:
    try:
        return datetime.fromisoformat(str(value)).date().weekday() < 5
    except ValueError:
        return False


def _eligible_yahoo_confirmation_dates(observations: list[RawObservation], *, evaluated_on: date) -> list[str]:
    """Return only previously stored, governed Yahoo observations eligible for re-read."""
    return sorted({
        observation.source_date for observation in observations
        if observation.observation_kind == YAHOO_UNCONFIRMED
        and observation.source_status in {"SUCCESS", "RETRY_SUCCESS"}
        and _business_source_date(observation.source_date)
        and observation.source_date < evaluated_on.isoformat()
    })


def _open_shadow_source_dates(observations: list[RawObservation]) -> list[str]:
    """Keep governed, incomplete source dates open for later source arrivals.

    A 16:30 Taipei collection time is not a market date.  SMM, Yahoo and LME
    can arrive on different schedules, so a later observation must cause the
    original explicit source date to be considered again without backfill.
    """
    return sorted({
        observation.source_date for observation in observations
        if observation.record_id.startswith("shadow-")
        and _business_source_date(observation.source_date)
    })


def _historical_yahoo_candidates(
    observations: list[RawObservation], *, evaluated_on: date, collected_at: str, history_fetcher=fetch_yfinance_historical_close_quotes
) -> list[tuple[str, ...]]:
    targets = _eligible_yahoo_confirmation_dates(observations, evaluated_on=evaluated_on)
    if not targets:
        return []
    needed = {(observation.source_id, observation.source_date) for observation in observations if observation.observation_kind == YAHOO_UNCONFIRMED and observation.source_date in targets}
    candidates: list[tuple[str, ...]] = []
    for (key, target), quote in history_fetcher(targets).items():
        source_id = {"brent_yfinance": "YFINANCE_BZ=F", "silver_yfinance": "YFINANCE_SI=F", "gold_yfinance": "YFINANCE_GC=F"}.get(key, "")
        if (source_id, target) not in needed or not quote.ok or quote.observed_at != target:
            continue
        candidates.append(build_yahoo_confirmed_observation_row(
            key, quote, evaluated_on=evaluated_on, collected_at=collected_at, history_rows=[(target, quote.value)]
        ))
    return candidates


def _evaluate_dates(rows: list[tuple[str, ...]], target_dates: list[str]) -> tuple[bool, list[str]]:
    """Canonicalize stored V2 history by source date before any append occurs."""
    observations = [_raw(row) for row in rows]
    statuses: list[str] = []
    for target in target_dates:
        canonical = canonicalize_daily_observations(target, observations)
        assembly = assemble_deferred_canonical_business_date(target, canonical.canonical_records)
        statuses.append(target + ":" + canonical.status + ":" + assembly.status)
        if canonical.status == "HUMAN_REVIEW_REQUIRED" or assembly.status == "HUMAN_REVIEW_REQUIRED":
            return False, statuses
    return True, statuses


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
    existing_rows = [tuple(row) for row in values[1:]]
    existing_observations = [_raw(row) for row in existing_rows]
    candidates = [build_shadow_observation_row(key, quote, evaluated_on=date.today(), collected_at=collected_at) for key, quote in quotes.items() if quote.ok and _business_source_date(quote.observed_at)]
    candidates.extend(_historical_yahoo_candidates(existing_observations, evaluated_on=date.today(), collected_at=collected_at))
    skipped_nonbusiness = sum(1 for quote in quotes.values() if quote.ok and not _business_source_date(quote.observed_at))
    plan = plan_shadow_observation_append(candidates, existing_rows)
    if plan.status != "READY":
        print("SCHEDULED_SHADOW=FAIL_CLOSED reason=" + str(plan.failure_reason))
        return 1
    candidate_dates = sorted({row[4] for row in candidates})
    target_dates = sorted(
        set(candidate_dates)
        | set(_eligible_yahoo_confirmation_dates(existing_observations, evaluated_on=date.today()))
        | set(_open_shadow_source_dates(existing_observations))
    )
    safe, evaluation_statuses = _evaluate_dates(existing_rows + list(plan.rows), target_dates)
    if not safe:
        print("SCHEDULED_SHADOW=FAIL_CLOSED reason=CONFIRMED_CANONICAL_CONFLICT status=" + ",".join(evaluation_statuses))
        return 1
    print("SCHEDULED_SHADOW mode=" + ("DRY_RUN" if dry_run else "V2_APPEND_ONLY") + " source_success=" + str(len(candidates)) + " skipped_nonbusiness_source=" + str(skipped_nonbusiness) + " v2_append=" + str(len(plan.rows)) + " duplicate_same=" + str(plan.duplicate_same_count))
    print("SHADOW_CANONICAL_REEVALUATION=" + (",".join(evaluation_statuses) if evaluation_statuses else "UNAVAILABLE"))
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
