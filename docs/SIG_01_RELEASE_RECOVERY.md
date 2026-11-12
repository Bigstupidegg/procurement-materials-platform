# SIG-01 version retention and recovery

This is an executable recovery design, not permission to merge, dispatch or
deploy. Use the existing Human Merge / Release Gate. Only the public System A
repository is involved; no private System B data or credential is required.

## Normal publication

`deploy-pages.yml` remains the only Pages uploader/deployer. It reads the
successful history of that same workflow, validates repository/branch/event and
Git ancestry, and selects the last **completed deployment**, rather than the last
created or queued run. Old pre-migration workflow sources are excluded from the
versioned-history search. A genuine first versioned publication may bootstrap
without an earlier versioned artifact.

The existing `github-pages` artifact is reused; no duplicate archive or credential
is introduced. `actions: read` allows retrieval with the default token. The
download action rejects an artifact digest mismatch. Archive extraction rejects
absolute/traversal paths, symlinks, hard links, special files and duplicates.

Each retained `releases/<id>/` directory, including its release manifest, remains
byte-for-byte unchanged. Per-release `releaseTreeSha256` covers that directory
excluding its own manifest. The root `publication-manifest.json` identifies the
active release and covers the entire deployed tree excluding itself, including
all retained release manifests. It is the authoritative whole-artifact identity.
Do not compare the whole artifact against a per-release subtree hash.

No automatic history deletion is performed. Every subsequent forward release
inherits all version directories from the last verified deployed artifact. Its
artifact retention is 90 days. If that artifact is missing/expired, API evidence
is unavailable, more than 1,000 successful runs need review, or the unpacked site
exceeds 900 MiB, publication stops before upload; it must not silently bootstrap
or purge old URLs. Retention/size policy changes require separate review.

## Recovery to a previously published versioned release

After Human authorization:

1. Record the current and target Pages run IDs, artifact IDs, active release IDs,
   source commits and root publication tree hashes. The target must be a
   successful deployment of this workflow on this repository's main ancestry.
2. Dispatch **Deploy GitHub Pages** on `main`, setting `rollback_run_id` to that
   exact reviewed target run ID. Empty input performs a normal forward release.
   PR/fork/other-branch/failed/expired artifacts are rejected. An automatic event
   cannot request rollback.
3. The same publisher downloads the last deployed artifact and the target
   artifact, checks both, and creates a new publication wrapper. Its root points
   to the target release, while the union of historical release directories is
   retained unchanged. A rollback does not delete the newer version's URLs.
   A duplicate release ID with different content blocks recovery.
4. The recovered payload is verified, uploaded and deployed by the existing sole
   uploader/deployer. No collector, signal builder, source revert, force push or
   synthetic market JSON is used in this artifact recovery path. Current main is
   rechecked before upload/deploy; a change stops the run.
5. Read the deployed root `publication-manifest.json`, target per-release manifest
   and representative HTML/JS/JSON. Compare source/ID/hash/artifact/run identities
   and verify actual browser behavior and CDN propagation. Upload/CI success alone
   is not acceptance.

Artifact rollback changes the served release, not main's desired source state.
Before dispatch, coordinate pending publication/data runs under the same Human
decision. A later normal publication can intentionally replace the rollback;
resume it only after the underlying problem is understood and the desired main
state is reviewed. This change does not itself pause or modify schedules.

## First migration and old-contract emergency recovery

Old, unversioned Pages artifacts have no publication manifest. They cannot be
passed to `rollback_run_id`. Before the first production migration, preserve and
review a matching old-contract source/artifact and its recovery plan.

The earlier local rehearsal used source
`9707a842562211b20a8d8f7b4c4c00b014b8d928`; its restored source tree was
`4f9ed18cc0db14f60ea8682c449fb3f1eeb5743d` and rebuilt site tree SHA-256 was
`d1f12a1de39022fd37919d7c00d1025e7de9605e436088c37ccb3a5e3f1b9071`.
Those are historical, local rehearsal identities, not proof that the current live
site or a future rollback still has that identity.

Recovery to the old contract requires a reviewed normal Git restoration of the
matching frontend, generated data and publication workflows, with running and
scheduled jobs coordinated. A simple revert of PR #36 is insufficient. Rebuild
and compare the reviewed baseline before publishing through the restored path;
read back the real deployed site afterward. Do not blindly restore the entire
old tree over unrelated later main changes. No production recovery is performed
by the local rehearsal or by this document.

## Evidence limits

Unit and isolated integration tests exercise retention, local artifact tar
round-trips, source/metadata rejection and recovery packaging. They do not prove
the hosted download/deployment path, CDN behavior or production browser behavior.
Those remain release-time checks at the existing Human Gate.

Primary action documentation:
- https://github.com/actions/download-artifact
- https://github.com/actions/upload-pages-artifact
