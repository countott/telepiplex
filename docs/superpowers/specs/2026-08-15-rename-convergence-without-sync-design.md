# Rename Convergence Without Sync Design

## Goal

Make rename converge safely after interruption, report success only after every
file and source directory satisfies its postcondition, and remove the automatic
rename to sync/Plex integration.

## Scope

The automatic product flow ends at rename:

```text
search -> confirmation -> download -> download.completed -> rename -> completed
```

Rename does not publish `media.organized`, enter `handoff_plex`, name `sync` as a
next owner, enqueue a sync job, or trigger a Plex scan. The sync Feature remains
available through its manual commands, `library.sync` capability, and MCP tools,
but it no longer subscribes to rename output.

## Rename success invariant

A rename operation is `completed` only when:

1. confirmed metadata passes detailed validation;
2. every scanned media file has exactly one terminal outcome;
3. every canonical target exists and its provider identity or fingerprint is
   verified;
4. every source that should move is absent;
5. every source work-group directory is absent, except explicitly protected
   category or library roots;
6. there are no target conflicts, failed files, unresolved files, cleanup
   failures, or failed work groups; and
7. the durable rename job and Host operation report both contain that result.

User notification delivery is best effort and does not change a verified file
transaction from success to failure.

## Storage convergence

Rename uses the inventory snapshot before remote lookups. Canonical no-ops need
no conflict lookup. Remote file-info lookups are deduplicated and bounded in
chunks of at most 32 paths so a 120-second RPC does not contain 128 sequential
rate-limited calls.

Copy/delete recovery uses the following identity rules:

- same provider ID at the target is an idempotent no-op;
- an existing target with the same SHA1 as the source is a recoverable prior
  copy: delete the retained source, then verify source absence;
- a different fingerprint is a hard conflict and is never overwritten;
- unverifiable identity fails closed;
- after every rename, move, source delete, and directory delete, fresh provider
  state must prove the postcondition.

The 115 inventory snapshot carries SHA1 where the provider supplies it.

## Source cleanup

Manual `/rename` deletes the selected work-group root when it is freshly proven
empty. Only configured category/library roots are protected. A successful
provider delete is followed by a fresh absence check; a retained, unreadable, or
still-present directory makes cleanup incomplete and therefore prevents rename
success.

## Metadata reconciliation

Search/download continues to use aired-release scope. Rename metadata resolution
uses observed inventory scope: it selects the exact season/episode coordinates
already present in the file probe without applying air-date filtering. Missing
coordinates produce a detailed `probe_inventory_mismatch`; confirmed-contract
validation reports the exact field path instead of the generic
`invalid confirmed media_metadata` message. Localized Chinese and original title
fields remain independent through confirmation and naming.

## Automatic sync removal

- rename manifest no longer publishes `media.organized`;
- sync manifest and runtime no longer subscribe to `media.organized`;
- rename finalization completes locally and contains no downstream handoff;
- inventory completion does not publish verified groups;
- automatic event and handoff tests are replaced by negative assertions;
- generic Host event infrastructure remains available to other Features;
- sync manual commands, capability, MCP service, and job implementation remain.

## Minimal validation

Use focused tests only:

- download client batch bounding and snapshot SHA1;
- rename executor replay, postconditions, and selected-root cleanup;
- rename finalization and inventory terminal-state tests proving no event/handoff;
- sync runtime test proving no automatic event subscription while manual surface
  remains;
- Honey and Clover inventory-scope and detailed validation regressions;
- focused package suites for files touched if the narrow tests reveal coupled
  failures.

No large path-count pressure matrix or full repository suite is required in this
iteration.

## Local delivery boundary

All edits and tests run only in `/Users/young/Documents/telepiplex`. No Git
command, worktree, publication, or GitHub action is used. Delivery ends after
local validation and Syncthing reaches `Up to Date / 最新` for
`/mnt/user/archives/life hacker/telepiplex`.
