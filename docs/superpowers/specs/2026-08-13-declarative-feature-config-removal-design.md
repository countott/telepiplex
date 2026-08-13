# Declarative Feature Config Removal Design

## Problem

The published `search 1.8.0` default config contains a required top-level `ai`
mapping. `search 1.9.1` removes that mapping and rejects unknown top-level
properties. The Host's safe default-fill migration preserves operator values,
so every normal 1.8.0 config fails 1.9.1 validation with
`config_migration_required`.

## Contract

Host API 1.5 adds signed, declarative configuration migrations inside a Feature
artifact. A Feature that changes `config_schema_version` may provide sequential
files named `migrations/config-<from>-to-<to>.json`. The v1 declaration has a
fixed format and supports only `remove` operations with bounded mapping-key
paths.

The Host validates declarations before dependency installation, applies every
required version step before default filling and new-schema validation, and
reports only removed field paths. It never logs removed values. Missing,
ambiguous, malformed, skipped, or incompatible migrations fail closed. A
Feature using this contract requires Host API 1.5 so older Hosts cannot offer
the update.

Configuration migration remains part of the existing activation transaction.
The complete pre-migration config is written to the new release's private
rollback snapshot. Failed activation restores it immediately; an explicit
Feature rollback restores it before starting the old release.

## search 1.9.2

`search 1.9.2` advances `config_schema_version` from 1 to 2 and declares one
operation: remove the top-level `ai` mapping. All other search, metadata,
Prowlarr, scoring, category-folder, and credential values remain untouched.
