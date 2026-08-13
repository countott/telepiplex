# Declarative Feature Config Removal Implementation Plan

**Goal:** Upgrade a normal published search 1.8.0 configuration without manual
editing while preserving fail-closed behavior for undeclared config loss.

- [x] Reproduce the production failure with the official search 1.8.0 and
  1.9.1 artifacts.
- [x] Add manager tests for declared removal, undeclared removal rejection,
  secret-safe result details, and rollback restoration.
- [x] Implement bounded sequential `remove` declarations in `PluginStore` and
  retain the existing atomic activation rollback.
- [x] Copy migration files through the Feature builder without following
  symbolic links and keep them covered by artifact checksums.
- [x] Add Host API 1.5 compatibility gating and Telegram removed-path feedback.
- [x] Release search 1.9.2 with config schema v2 and the v1-to-v2 `ai` removal.
- [x] Validate Host and search suites plus an artifact-level official 1.8.0 to
  local 1.9.2 upgrade and rollback.
