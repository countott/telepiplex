# Rename Partial Resolution and Native 115 Move Design

## Goal

Make rename organize every file whose canonical episode coordinate is unique,
leave only ambiguous files untouched with one optional AI explanation, and
replace copy-then-delete moves with the official 115 server-side batch move
API.

## Problem statement

The attached runtime log contains two independent failures:

1. Search resolves the correct series, but
   `apply_inventory_probe_scope(...)` raises
   `probe_inventory_mismatch` as soon as any observed coordinate is absent
   from the canonical inventory. The whole work group therefore stops even
   when most files have a unique canonical match.
2. Rename executes files serially. The download provider's move operation is
   implemented as copy followed by delete, and its file-info batch method is
   still a sequential loop. A 65-file release amplifies provider calls and
   makes rename take many minutes.

The Honey and Clover example is not sufficient evidence that `S01E25` and
`S01E26` are TVDB absolute, DVD, special, OVA, or any other specific order.
The implementation must not choose an alternate order from counts or filename
suffixes alone.

## P0: deterministic partial resolution

### Search contract

For an inventory probe with explicit season/episode coordinates, Search
partitions observations into:

- `matched`: the canonical inventory contains exactly one item for the
  coordinate;
- `unmatched`: the canonical inventory contains no item for the coordinate;
- `duplicate`: the canonical inventory contains more than one item for the
  coordinate.

Search returns a confirmed contract when at least one coordinate is matched.
Only matched canonical items enter `media_metadata.items`. Evidence records an
`inventory_reconciliation` object containing observed, matched and unresolved
counts plus stable unresolved coordinate reason codes. An all-unmatched probe,
an unscoped probe that cannot select exactly one season, or a malformed probe
still fails closed.

This behavior applies only to rename inventory reconciliation.
`apply_series_scope(...)` for search/download release selection remains strict.

### Rename planning and execution

Rename plans operations only for matched contract items. Existing file-first
planning gives every other media fact a `keep_original` resolution. Therefore:

- matched files are renamed and moved;
- unmatched or duplicate-coordinate files remain at their exact source path;
- non-primary and unsupported files remain untouched;
- target conflicts and provider errors remain per-file failures.

Source cleanup removes only freshly verified empty directories. A directory
containing an unresolved file is retained and is not a cleanup failure.

### Result states

The durable rename job uses:

- `completed` when every media file is organized or already canonical;
- `partial_completed` when at least one file is organized/canonical, all other
  media files are explicitly kept, and there are no target conflicts,
  mutation failures, or cleanup failures;
- `failed` when no safe file is organized or any hard failure remains.

The shared Host operation state stays `completed` for both complete and partial
success so existing Host databases and Feature protocol constraints remain
compatible. Partial success is exposed as `stage=partial_completed` and
`details.completion_kind=partial_completed`. Durable job state and capability
results retain the explicit `partial_completed` value.

Inventory batches count partial work groups separately and finish successfully
when every group is either complete or partial and no hard failure remains.

### AI explanation boundary

After deterministic file execution, rename may make one best-effort AI call per
work group when unresolved media files remain and AI is configured. The input
contains only confirmed work identity, canonical coordinate summaries,
unresolved source names and stable reason codes. The accepted output is limited
to:

- one summary string;
- a bounded list of possible causes;
- a bounded list of user checks.

AI output never creates or changes a file resolution, season number, episode
number, target path, rename operation, move operation, or cleanup decision.
Invalid/unavailable AI output falls back to a deterministic explanation and
does not change the task result.

## P1: native 115 batch move

115 officially documents the production endpoint:

```text
POST https://proapi.115.com/open/ufile/move
Content-Type: multipart/form-data
file_ids=<comma-separated file or folder IDs>
to_cid=<target directory ID>
```

The provider adds `move_files_by_id(file_ids, target_dir_id)`. It validates and
deduplicates IDs, submits one native move request, clears affected cache state,
and returns a structured success/failure result. The old copy-delete
implementation remains only as a compatibility fallback for storage providers
that do not expose native batch move; normal 115 execution never calls it.

Rename prepares each file independently through identity checks, conflict
checks, and any required rename. It then groups prepared moves by target
directory, creates each target directory once, and submits configurable chunks
of IDs. The default chunk size is 32 and the schema allows 1 through 100.

After every batch call, rename verifies postconditions from fresh immediate
directory listings:

- the target directory contains the expected provider ID under the expected
  canonical name;
- the source parent no longer contains that provider ID.

Verification is authoritative even when the provider call returns failure, so
a partially applied external operation can still be reconciled correctly.
Failed batches are not blindly retried because the public response does not
document atomicity or per-file outcomes. A later rerun is safe through the
existing provider-ID replay rules.

## Observability

File results include:

- `completion_kind`;
- structured unresolved files and reason codes;
- deterministic or AI ambiguity explanation;
- native move batch count and per-file terminal outcomes through the existing
  journal.

User notification reports organized, already-canonical, retained, conflict and
failed counts, names unresolved files, and states that they remain in place.

## Acceptance criteria

- Aired-order Honey and Clover metadata with local `S01E01-S01E26` and
  `S02E01-S02E12` selects 36 canonical items and records `S01E25/S01E26` as
  unresolved.
- Rename organizes those 36 files, keeps the two unresolved files at their
  source paths, does not delete their source directory, and produces
  `partial_completed`.
- The AI explanation is called at most once and cannot alter file operations;
  AI failure produces the same file outcomes.
- A probe with zero matched coordinates remains unresolved and performs no
  mutation.
- The normal 115 move path calls `/open/ufile/move`, never `/open/ufile/copy`
  or `/open/ufile/delete`.
- Files targeting the same directory are moved in one or more bounded native
  batches, with fresh target/source directory verification.
- A batch response that disagrees with observed provider state is resolved from
  observed IDs and paths, not from the response boolean alone.
- Replaying a complete or partial job makes no duplicate move.

## Local delivery boundary

All edits and tests run only in `/Users/young/Documents/telepiplex`. Do not run
Git, create worktrees, publish, or connect the Mac checkout to GitHub. Build
temporary Feature packages only under `/tmp`. Delivery ends after local
verification and Syncthing reports `Up to Date / 最新` for
`/mnt/user/archives/life hacker/telepiplex`.
