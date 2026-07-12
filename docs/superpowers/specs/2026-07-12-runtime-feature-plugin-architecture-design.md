# Telepiplex Runtime Feature Plugin Architecture

**Date:** 2026-07-12

**Status:** Approved for direct implementation

**Source topology:** one local repository and one remote repository, with
`feature/telepiplex-core` as the Docker/runtime source and one `feature/*`
branch per business Feature.

## 1. Goal

Turn Telepiplex into a core-hosted plugin runtime in which one Docker container
runs a permanent core process and each installed Feature runs as an isolated
child process. Installing, upgrading, enabling, disabling, rolling back, or
removing a Feature must not restart core.

The daily runtime image is built only from `feature/telepiplex-core`. `main` is
not a live composition branch during this migration; it may later be rebuilt
as a validated release aggregation.

## 2. Confirmed Constraints

- One Docker container contains core and all installed Feature processes.
- Core and Features do not share one Python process.
- Every Feature has its own virtual environment and subprocess.
- Core-to-Feature communication uses Unix domain sockets.
- Feature branches remain source branches in the same local and remote Git
  repository.
- Runtime installation uses immutable, versioned `.tpx` artifacts built from a
  Feature branch, not a live Git checkout.
- Feature lifecycle operations do not restart core.
- A core contract upgrade may restart the core container.
- 115 is a Feature, not part of core.
- Features depend on named capabilities and events, never another Feature name
  or Python package.
- Feature configuration and state are separated from core configuration.
- Updates are announced but applied only on an administrator command.
- Upgrade drains active work; it never kills a non-idempotent operation and
  calls it complete.

## 3. Runtime Architecture

The core process owns the Telegram connection, administrator authorization,
plugin catalog, package installer, supervisor, capability router, durable event
journal, configuration service, and user-facing response rendering.

Each Feature subprocess owns only its business behavior. It never imports
`app.init`, Telegram objects, another Feature, or a live core implementation.
It communicates through the versioned Telepiplex Plugin Contract.

```text
telepiplex-core container
├── core process
│   ├── Telegram gateway and dynamic command router
│   ├── plugin manager and package verifier
│   ├── subprocess supervisor
│   ├── capability RPC router
│   ├── durable event journal
│   └── config/schema service
├── /config/plugins/open115/.../venv        -> open115 process
├── /config/plugins/media-search/.../venv   -> media-search process
├── /config/plugins/renaming/.../venv       -> renaming process
└── /config/plugins/plex-management/.../venv -> plex process
```

Subprocess isolation is a failure and dependency boundary, not a security
sandbox. Installed artifacts are trusted code running with the container's
filesystem permissions.

## 4. Repository and Development Model

The existing repository and remote remain authoritative:

- `feature/telepiplex-core` contains core runtime, contract schemas, SDK,
  packaging CLI, Dockerfile, and core tests.
- `feature/115` contains only the 115 Feature source, manifest, config schema,
  locked dependencies, migrations, and tests.
- `feature/media-search`, `feature/renaming`, and
  `feature/plex-management` follow the same Feature layout.

The source branch and production artifact are connected by one build command.
The artifact records repository URL, source branch, commit SHA, plugin version,
build timestamp, core API range, and SHA-256 digest. Local development installs
the same artifact into a running core through a local-path catalog entry; CI
and production install the published copy of that artifact.

No Feature branch carries a private copy of the core bot runtime. Contract
fixtures may be generated from the released SDK, but runtime imports across
branches are forbidden.

## 5. Plugin Package Contract

A `.tpx` file is a deterministic ZIP archive containing:

```text
manifest.yaml
plugin.whl
wheelhouse/*.whl
config.schema.json
config.default.yaml
migrations/
checksums.sha256
```

`manifest.yaml` contains at least:

- stable `plugin_id`, display name, semantic version, and entry point;
- compatible `core_api` version range;
- provided capabilities and whether each provider is exclusive;
- required capabilities and subscribed events;
- dynamic Telegram commands and callback namespaces;
- configuration schema version;
- state migration version;
- source repository, branch, and commit SHA;
- build and package checksums.

The package includes its complete wheelhouse so installation uses
`pip --no-index` and does not resolve new dependencies in production.

## 6. Filesystem and Configuration Ownership

```text
/config/config.yaml
/config/core.db
/config/plugins/catalog.yaml
/config/plugins/<plugin_id>/config.yaml
/config/plugins/<plugin_id>/state/
/config/plugins/<plugin_id>/releases/<version>/
/config/plugins/<plugin_id>/active.json
/tmp/telepiplex/<instance_hash>.sock
```

`/config/config.yaml` contains only core settings: Telegram credentials,
administrator, artifact repositories, supervisor policy, and logging.

Each Feature owns its `config.yaml`, schema, migrations, and state directory.
Core validates Feature configuration against the installed schema and exposes a
generic Telegram configuration flow. A Feature never reads another Feature's
configuration. Shared values are exposed as explicit core configuration
capabilities or included in event/request envelopes.

Unix sockets are ephemeral and use fixed-length hashed names under
`/tmp/telepiplex`; deriving socket names from arbitrary install paths can exceed
the platform `AF_UNIX` path limit.

## 7. Process and RPC Contract

Core launches a Feature with its private venv Python, entry point, socket path,
config path, state path, and one-time startup token. The Feature creates the
Unix socket and performs a handshake containing its manifest identity,
contract version, capabilities, command declarations, and health status.

RPC uses UTF-8 newline-delimited JSON envelopes with explicit maximum frame
size, request ID, method, deadline, trace ID, and idempotency key. Responses
contain either a typed result or a stable error code plus sanitized detail.
The first API version supports:

- lifecycle: `handshake`, `health`, `drain`, `shutdown`;
- capability request/response;
- event delivery and acknowledgement;
- command and callback delivery;
- configuration validation and reload;
- task inspection and interruption reporting.

Core validates every envelope before routing it. Unknown methods, contract
versions, capabilities, or event schemas fail closed without terminating core.

## 8. Capability and Event Model

Features declare dependencies by capability, never by plugin identity.

Initial capability relationships are:

```text
open115
  provides: download.provider, storage.provider

media-search
  requires: download.provider
  provides: media.search

renaming
  requires: storage.provider
  subscribes: download.completed
  publishes: media.organized

plex-management
  subscribes: media.organized
  provides: plex.management
```

Exclusive provider capabilities have exactly one active provider. Events may
have multiple subscribers. Core refuses activation when a required capability
is missing or ambiguous and reports the unresolved dependency through
`/plugin status`.

The durable event journal is stored in `/config/core.db`. Delivery is
at-least-once with an event ID and subscriber acknowledgement. Consumers must
use the event ID or domain idempotency key to prevent duplicate effects.

## 9. Telegram Interaction

Core is the only Telegram client. It permanently registers generic command,
callback, and conversation gateways, allowing route tables to change without
restarting the Application.

Feature manifests declare commands, but Features receive normalized command
envelopes rather than Telegram objects. They return typed response actions such
as message text, edit, keyboard, document, or progress update. Core validates
and renders those actions.

Core provides administrator commands:

```text
/plugin install <plugin>@<version>
/plugin update <plugin>
/plugin enable <plugin>
/plugin disable <plugin>
/plugin rollback <plugin>
/plugin remove <plugin>
/plugin status [plugin]
/plugin doctor
```

Install and upgrade commands are restricted to the configured administrator,
audit logged, and reject unverified artifacts.

## 10. Installation and Activation

Installation is transactional:

1. Resolve an artifact from the configured catalog or an approved local path.
2. Download/copy it into a staging directory.
3. Verify archive paths, checksums, identity, version, and core API range.
4. Unpack without following links outside staging.
5. Create a private venv and install only from the bundled wheelhouse.
6. Validate default/current config and run package self-tests.
7. Launch a shadow process and require a successful handshake and health check.
8. Resolve all capability dependencies and command conflicts.
9. Atomically write `active.json` and add routes.
10. Keep the previous release until retention policy permits deletion.

Any failure removes staging and leaves the old active version untouched.

## 11. Upgrade, Drain, and Rollback

Updates are never automatic. Core may notify that a compatible version exists.
On `/plugin update`:

1. Stage and validate the new version while the old version serves traffic.
2. Start the new version in shadow mode.
3. Mark the old version `draining` and stop new dispatches to it.
4. Wait for active work up to the configured deadline.
5. Remaining tasks must be reported as `interrupted`; non-idempotent work is
   never automatically replayed unless its Feature contract explicitly allows
   recovery.
6. Atomically switch capability, event, command, and callback routes.
7. Observe the new process during a stabilization window.
8. Stop the old process but retain its release for rollback.

If staging, handshake, dependency resolution, migration, or stabilization
fails, core restores old routes and the old process. `/plugin rollback`
performs the same drain and route-switch procedure in reverse.

## 12. Fault Isolation

The supervisor tracks process state, heartbeat, exit code, restart count, and
last sanitized error. Unexpected exits use bounded exponential backoff. A
Feature exceeding the restart threshold is quarantined rather than restarted
forever.

When a provider disappears:

- core remains available;
- dependent Features become `blocked` or `degraded`;
- routes requiring the missing capability return a clear user error;
- unrelated Features continue running;
- durable events remain pending until a compatible subscriber returns or an
  administrator resolves them.

Core startup loads only releases recorded in `active.json`. A corrupt or
incompatible Feature is quarantined and cannot stop the Telegram bot.

## 13. Security and Integrity

The first release trusts artifacts published by configured repository owners,
but still enforces:

- HTTPS or approved local paths;
- SHA-256 verification of archive and members;
- ZIP path traversal and symlink rejection;
- manifest identity/version validation;
- administrator-only lifecycle commands;
- redaction of secrets in logs and RPC errors;
- no shell interpolation of manifest values;
- bounded package size, frame size, timeouts, and process resources where the
  host permits them.

Artifact signing may be added later without changing the package identity
model. The design does not claim same-container subprocesses are hostile-code
sandboxes.

## 14. Core Contract Evolution

Core API uses semantic major/minor versions. Additive methods and fields raise
the minor version; breaking changes raise the major version. Features declare a
supported range and ignore additive unknown response fields.

Before a core image upgrade, `/plugin doctor` reports installed Features that
will become incompatible. A new core contract may require one explicit
container restart, which is allowed. Normal Feature lifecycle operations never
restart core.

## 15. Migration Order

This architecture is implemented in bounded phases:

1. Core contract, artifact verifier/builder, plugin store, supervisor, Unix RPC,
   capability router, event journal, and `/plugin` commands.
2. A reference echo Feature proves install/enable/update/drain/rollback/remove
   without restarting core.
3. Migrate 115 as `download.provider` and `storage.provider`.
4. Migrate media-search to the Telegram gateway and `download.provider` RPC.
5. Migrate renaming to `download.completed`, `storage.provider`, and
   `media.organized`.
6. Migrate Plex management to `media.organized` and independent background
   tasks.
7. Build an end-to-end artifact matrix and Docker runtime test.

The existing in-process `ModuleRegistry` remains only as a temporary migration
surface and is removed after all four Features pass subprocess integration.
There is no compatibility promise for external third-party modules using the
old Python import contract.

## 16. Acceptance Criteria

- A core-only Docker container starts with no business Feature code installed.
- Installing each `.tpx` artifact through core requires no core restart.
- A Feature can be enabled, disabled, upgraded, rolled back, and removed while
  `/start` and `/plugin status` remain responsive.
- Feature dependencies are resolved only through capabilities/events.
- No Feature imports core internals, Telegram, `init`, or another Feature.
- A Feature crash cannot terminate core or unrelated Features.
- Dependency conflicts, command conflicts, incompatible API ranges, invalid
  config, bad checksums, and failed health checks reject activation atomically.
- In-flight work drains or becomes explicitly interrupted during upgrade.
- Feature config/state and releases survive core container recreation through
  `/config`.
- Local development and production install the identical artifact built from a
  recorded source branch and commit SHA.
- End-to-end tests prove open115 -> media-search -> download completion ->
  renaming -> media.organized -> Plex without a combined `main` runtime.

## 17. Deferred Scope

- Hostile-code sandboxing inside the shared container.
- Multiple core replicas or zero-downtime core contract upgrades.
- Remote multi-host Feature placement.
- Automatic Feature upgrades.
- Public third-party marketplace and cryptographic publisher trust chains.
- Rebuilding `main` as a complete release bundle before the plugin migration is
  verified.
