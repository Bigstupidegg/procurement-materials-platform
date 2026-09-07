from __future__ import annotations

import unittest
from scripts.c3_2_observation_canonicalization import CanonicalDailyRecord, RawObservation
from scripts.c3_2_canonical_store import plan_canonical_decisions


def record(kind="YAHOO_DAILY_CLOSE_CONFIRMED"):
    obs=RawObservation("obs-1","BRENT_FUT","YFINANCE_BZ=F","2026-09-01",90.0,"USD","USD/bbl","FUTURE","2026-09-02T10:00:00+08:00",kind)
    return CanonicalDailyRecord(obs,"CANONICAL",kind)


class CanonicalStoreTests(unittest.TestCase):
    def test_plan_is_idempotent(self):
        first=plan_canonical_decisions([record()],decided_at="2026-09-02T10:00:00+08:00")
        again=plan_canonical_decisions([record()],decided_at="2026-09-02T11:00:00+08:00",existing_rows=first.rows)
        self.assertEqual((first.status,len(first.rows)),("READY",1)); self.assertEqual((again.status,len(again.rows),again.duplicate_same_count),("READY",0,1))

    def test_legacy_cannot_be_persisted(self):
        self.assertEqual(plan_canonical_decisions([record("LEGACY_UNVERIFIED")],decided_at="x").status,"FAIL_CLOSED")


if __name__ == "__main__": unittest.main()
