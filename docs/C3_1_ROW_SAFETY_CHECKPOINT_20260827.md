# C3.1 Row-Safety Checkpoint — 2026-08-27

Operational dry run found a concrete overwrite risk in the company worksheet:

- Row 15: `2026/08/25`
- Row 16: A=`26`, while B:L already contain 2026/08/26 market values
- A 2026/08/27 dry run previously resolved the target to Row 16 because the append rule considered only rows whose A cell parsed as a full date.

## Safety correction

C3.1 now defines a fail-closed row-resolution contract:

1. Read A5:L1000 using formatted values.
2. If any row has data in B:L but A is not a complete parseable `yyyy/mm/dd` date, stop without writing.
3. Duplicate target dates stop without writing.
4. If today already exists exactly once, reuse that row.
5. If today is missing, append after the last actually used A:L row, not after the last valid-date row.
6. `DRY_RUN=True` remains mandatory until the operational worksheet date defect is corrected and another company-PC dry run passes.

## Automated validation

New files:

- `scripts/company_market_row_safety.py`
- `tests/test_company_market_row_safety.py`

Tests cover:

- occupied day-only date row -> fail closed;
- missing today -> append after last used row;
- existing today -> reuse row;
- duplicate today -> fail closed;
- invalid A-only row is not overwritten.

Quality Check #78 passed after these row-safety tests were added.

## Operational next gate

Before the next dry run, correct A16 from `26` to the true Google Sheets date `2026/08/26` displayed as `yyyy/mm/dd`. Then run v6.3.2 Dry Run and confirm 2026/08/27 resolves to Row 17 / `A17:L17`.

No production Google Sheet write and no PR merge are authorized at this checkpoint.
