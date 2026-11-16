# SDD ledger — plan: docs/superpowers/plans/2026-11-16-centralized-config.md

Approved spec: docs/superpowers/specs/2026-11-16-centralized-config-design.md.
Source baseline (local temporary evidence): `/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-config-18ssd64_/baseline.json`.

## Preflight

| Tasks | Shared file/interface | Check |
| --- | --- | --- |
| 1 / 2 | prepared config dict, per-instance snapshot | Store owns projection; Supervisor receives dict, never main YAML path. No overlapping files. |
| 1 / 3 | CentralConfigStore, ConfigChange, PluginStore | Exact method contract recorded in plan. Both preserve unrelated paths; workers coordinate refinements before use. |
| 1 / 4 | main config path, migration and templates | Root injects production backend; standalone tests may keep the explicit legacy Store mode. |
| 2 / 3 | Supervisor.start config argument, Broker config hooks | Runtime worker owns Broker/Supervisor files; lifecycle worker owns Manager and installs hooks by assignment/interface. |
| 2 / 4 | runtime startup and manifest declaration | Root uses new runtime constructor/hooks; no shared file editing. |
| 3 / 4 | configure_patch/configure_shared and reload | Manager owns transactions; root passes raw wizard patches and shared edits. |
| 1 | internal scope/test consistency | Projection, migration, atomic patches and Store integration have matching tests and owned files. |
| 2 | internal scope/test consistency | Snapshot, writer identity, Download adapter and manifest parser scoped together. |
| 3 | internal scope/test consistency | Central lifecycle gets separate tests; legacy behavior remains explicit. |
| 4 | internal scope/test consistency | Root owns templates, user flows and production injection; tests exercise integration. |
| 5 | integration/version changes | Root adjusts final version requirements only after worker code settles; full tests and independent review required. |

Mac AGENTS overrides skill Git/worktree/commit/review-package procedures. Use local baseline and source review instead. Do not delete this progress document at completion; no Git history exists on this Mac.

## Progress

- [x] Preceding cleanup and approved design.
- [x] Written implementation plan and preflight interface scan.
- [x] Task 1 central storage.
- [x] Task 2 runtime and Download writeback.
- [x] Task 3 central lifecycle.
- [x] Task 4 Host/Telegram/templates.
- [x] Task 5 docs/versions/full validation/review.

## Evidence

- Cleanup: Rename 518 passed + 26 subtests; Caption 500 passed, 6 skipped with temporary declared OpenCC dependency; schema parity 1,888 cases; focused Host 53 passed, 1 skipped, 58 subtests.
- Cleanup independent source review: no actionable findings.
- Storage review round 1: fixed empty ancestor rollback and explicit scoring-table migration; 42 storage/Store tests passed. Scoped review marked both ADDRESSED.
- Lifecycle review round 1: fixed launch-snapshot consistency and target-default code rollback; 64 manager tests passed. Scoped review marked both ADDRESSED.
- Shared Telegram draft CAS added under lifecycle lock; manager total 67 passed, 3 subtests.
- Runtime review round 1: fixed post-persist stale local credential installation with generation checks, including direct/scan authorization and empty cancellation; Download full 276 passed, 33 subtests. Scoped review independently ran 9 focused tests and marked ADDRESSED.
- Final Feature suites after version alignment: Search 3605 passed/2 skipped/246 subtests; Rename 518 passed/26 subtests/one existing logger.warn warning; Sync 160 passed/80 subtests; Caption 500 passed/6 skipped (temporary OpenCC dependency path).
- Host wiring/Telegram/template focused run: 120 passed, 48 subtests before final Caption discoverability and shared draft additions.
- Another user-owned chat concurrently edits Telegram photo/message retry code in plugin_handler.py and interaction_handler.py. User explicitly confirmed to preserve it. Initial intermediate import errors and four photo test failures occurred while that code was incomplete; later focused Host tests passed with the completed helper. Do not attribute or revert those regions as configuration changes.
- Current version set: Host 3.10.0/API 1.10; SDK 2.3.0; Download 2.3.0; Search 2.11.2; Rename 2.4.6; Sync 2.1.3; Caption 1.3.5. All Feature SDK pins and echo example align with SDK 2.3.0.
- Final whole-change integration review complete. Two final gaps (actual reload-result feedback and obsolete README path ownership) fixed in one wave; scoped 100 tests/52 subtests passed and final reviewer marked both addressed.
- Host first full session 48158: 4 failed, 853 passed, 1 skipped, 349 subtests. Two Caption flows lacked declared OpenCC (reproduced, then 2 passed with temporary dependency path); two old API/config-surface contract assertions updated to approved architecture.
- Final Host full suite session 46017 passed with temporary OpenCC dependency path and all final source fixes: 859 passed, 1 skipped, 353 subtests in 171.31s.
- Independent final Download run by root: 276 passed, 33 subtests in 6.83s. Workspace boundary verified: no .git/.worktrees; .stfolder retained.
- Final Feature aggregate: 5059 passed, 8 skipped. Final whole-change review and scoped fix-wave rereview complete, no unresolved important findings. Delivery records list source files individually, actual commands/results, migration/Download floor and user-controlled Syncthing/Unraid handoff.
