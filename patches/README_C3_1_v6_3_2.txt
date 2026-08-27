C3.1 v6.3.2 operational gate

1. Keep DRY_RUN=True.
2. Run once while A16 is still `26`; expected result is fail-closed because B:L are occupied but A16 is not a full yyyy/mm/dd date.
3. Correct A16 to a true Google Sheets date displayed as `2026/08/26`.
4. Run again; expected target for 2026/08/27 is Row 17 / A17:L17.
5. Do not merge PR #16 or enable production writes until both behaviors are verified.
