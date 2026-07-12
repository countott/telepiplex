# Release Gate Hardening Design

## Goal

Close the three verified release blockers in the Core Feature platform without changing the Core API version or any Feature business behavior:

1. A Provider update must not make an already active consumer newly blocked.
2. An unspecified Feature `internal_error` must remain retryable instead of consuming the dead-letter poison budget.
3. A Feature artifact must not contain or declare another Telepiplex Feature dependency through any supported packaging surface.

## Scope

Only `feature/telepiplex-core` changes. The open115, media-search, renaming, and plex-management source branches remain unchanged. Existing deferred business decisions remain outside this work.

## Provider Update Invariant

`CapabilityRouter.prepare_activation()` already builds a complete candidate snapshot before it changes live routes. It will additionally compare the candidate snapshot with the current snapshot.

Preparation is rejected when a plugin that is currently registered and unblocked would become blocked in the candidate snapshot. The Feature being activated retains the existing self-dependency check. The raised routing error identifies the newly blocked consumers and their missing capabilities.

Because rejection occurs before `PluginManager` drains the old process, changes the active release, or commits routes, the existing activation rollback path is not needed for this case: the old Provider remains active and consumers keep their routes.

This invariant applies equally to future Providers and is not special-cased for open115.

## Event Failure Classification

Core only consumes the poison-attempt budget for explicit, deterministic terminal errors. `internal_error` is removed from that set because the SDK uses it as the safe envelope for every otherwise unclassified exception, including temporary database, filesystem, and adapter failures.

The existing explicit deterministic codes remain terminal. Transport errors, availability errors, timeouts, and `internal_error` leave the delivery pending and do not increment the poison budget. This does not change the event payload or RPC protocol.

A permanently broken handler that only returns `internal_error` remains pending and visible in operational status instead of being silently terminalized. Explicit contract/input errors can still reach dead-letter and unblock later processing.

## Feature Dependency Isolation

The source builder adopts a fail-closed dependency policy.

### Requirements input

`requirements-feature.txt` accepts only normalized named PEP 508 distribution requirements. The builder uses `packaging.requirements.Requirement` and accepts only successfully parsed requirements whose `url` is empty. It rejects pip directives and indirections such as `-r`, `-c`, `-e`, index/find-links options, local paths, bare VCS links, and named URL requirements. This prevents dependency content from escaping the file that Core validates. Core declares `packaging>=24,<27` explicitly so the same parser is available in Docker and release environments.

Every accepted named requirement is normalized. Any `telepiplex-*` distribution other than `telepiplex-plugin-sdk` is rejected.

### Plugin metadata

After building `plugin.whl`, the builder reads its `.dist-info/METADATA` and validates every `Requires-Dist` entry with the same Telepiplex distribution rule. This closes dependencies declared in `pyproject.toml` rather than `requirements-feature.txt`.

### Final wheelhouse

Before creating the `.tpx`, the builder inspects wheel distribution names in the final wheelhouse. Any `telepiplex-*` wheel other than the SDK is rejected. This is the final defense against direct or transitive sibling packaging.

The builder does not introduce a general third-party dependency allowlist; ordinary named PyPI dependencies remain supported.

## Error Handling

- Provider compatibility failures return a stable routing error and leave old routes/process/release untouched.
- Dependency syntax that cannot be proven safe fails the build with `FeatureBuildError` before artifact creation.
- Invalid or missing wheel metadata fails the build instead of skipping validation.
- Error messages identify the affected consumer or prohibited distribution without exposing credentials or URL query values.

## Testing

TDD covers each release blocker:

1. Install a Provider and consumer, attempt a Provider update that removes a required capability, assert the update fails and old Provider/consumer routes remain active.
2. Deliver an event through a client that returns transient `internal_error` before succeeding, assert the event remains pending without dead-letter attempt consumption and later succeeds.
3. Assert requirements indirection and URL/path forms are rejected; assert sibling `Requires-Dist` metadata and sibling wheelhouse contents are rejected; retain a positive build test for ordinary dependencies and the SDK.

After targeted tests pass, verification includes the full Core unittest and pytest suites, `pip check`, fresh `.tpx` builds for all four Features, artifact verification, same-process installation, dependency protection, shutdown/restore, unchanged Core PID, and clean Git worktrees.

## Non-Goals

- No new RPC retryability field.
- No Core API version bump.
- No changes to Feature manifests or business workflows.
- No general dependency allowlist or package signing system.
- No implementation of deferred TODO business decisions.
