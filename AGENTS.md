# C3.2 Operating Rules

- Work autonomously on `v2.3-c3-2-daily-automation`: inspect, fix low-risk code, test, normal commit/push, and run approved Shadow/Pending pilots.
- Keep `ALLOW_GOOGLE_SHEET_WRITE=0`; Production Write and A:L writes are disabled. Never merge main, release/tag, force-push, alter credentials/security, or delete/overwrite Sheet data.
- `Market_Raw` is immutable. `Market_Observation_V2` is append-only; always read back IDs and reconcile before any rerun. Never promote `LEGACY_UNVERIFIED` rows.
- Human Gate is required for A:L/Production Write, destructive change, confirmed-vs-confirmed conflict, source-policy/security change, or unprovable correctness.
- Yahoo: same-day data is `YAHOO_UNCONFIRMED`; only next-day historical data with target date and parseable Close is `YAHOO_DAILY_CLOSE_CONFIRMED`. Do not call it an exchange settlement. Unconfirmed-to-confirmed changes are normal versions.
- SMM uses explicit Daily Snapshot dates. LME uses explicit delayed official source dates. No inferred dates or substitutions.
- Canonicalization and Deferred Assembly are shadow-only until separately approved; assembly consumes canonical records only and requires 11/11 same-date records.
- Use Codex for code and tests; use read-only source/Sheet/GitHub validation. Keep diagnostics redacted and never log credentials or raw values unnecessarily.
