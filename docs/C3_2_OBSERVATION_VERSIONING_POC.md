# C3.2 Observation Versioning PoC

Status: local-only PoC.  No Google Sheet schema or data has changed.

## Policy model

`Market_Raw` becomes an append-only observation layer.  A physical row is an
immutable source observation, not a daily canonical price.  A proposed v2 row
keeps the current fields and adds `observation_id`, `observation_at`,
`observation_kind`, `canonical_status`, `canonical_reason`, and
`canonicalized_at`.

`observation_id` is a UUID or other immutable physical primary key.  The
natural version key is `(source_date, material_id, source_id, observation_at)`.
This permits same-day revisions without overwrite.  Existing `record_id`
cannot remain the unique key because it currently contains only date/material.

## Canonicalization

Yahoo Futures observations with `source_date == today` are
`YAHOO_UNCONFIRMED` and remain history only.  On the next day, the
historical query must still contain that source date and expose a parseable
Close before a new `YAHOO_DAILY_CLOSE_CONFIRMED` observation may become the
canonical record.  A changed confirmed Close is a new version, not a conflict
with an earlier unconfirmed observation. Multiple confirmed closes with
different source/unit/price are `HUMAN_REVIEW_REQUIRED`; same-value versions
are safe duplicates. SMM uses its
daily snapshot policy and LME its documented delayed official source-date
policy.  Deferred Same-Date Assembly must receive only canonical records.

## Migration and rollback

1. Create a new versioned observation worksheet or a new v2 range; do not
   alter the current `Market_Raw` header in place.
2. Backfill existing rows as `LEGACY_UNVERIFIED` observations with their
   original values and a deterministic migration identifier.  Do not mark any
   Yahoo legacy record canonical automatically.
3. Run read-only reconciliation: row counts, key uniqueness, blank canonical
   status for unresolved Yahoo history, and no A:L delta.
4. Run a separate human-approved dual-read PoC where assembly still has no
   writer.  Cut over only after canonical selection, source policy, and
   read-back acceptance are approved.

Rollback is a reader configuration rollback to the existing `Market_Raw`
contract.  Because the proposed v2 store is append-only and separate, rollback
does not delete or overwrite observations.  Any real schema creation, backfill,
dual-read cutover, or canonical status persistence requires the Schema
Migration Human Gate.

## Phase A approval boundary

Phase A creates `Market_Observation_V2` only.  Each existing `Market_Raw` row
is copied without changing source fields, with a deterministic SHA-256
`legacy-*` observation ID derived from its original row position and values.
`observation_at` remains blank and both `observation_kind` and
`canonical_status` are `LEGACY_UNVERIFIED`; no Yahoo legacy row is a final
close or a canonical record.  The migration reads back every observation ID and
its full row before reporting success.  It is safe to rerun after a partial
append because matching IDs are not appended again; mismatched, duplicate, or
unknown IDs fail closed.
