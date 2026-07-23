# Telepiplex

Telepiplex in `main` is the only long-running Docker runtime. The container has one permanent Telepiplex process; business capabilities such as 115, media search, rename, and Plex management come from standalone source directories below `features/` and run as isolated Feature child processes.

Each Feature owns its Python virtual environment, configuration, state, and versioned release directory. Telepiplex talks to declared capabilities over Unix Domain Sockets and owns command routing, event delivery, health checks, draining, switching, and rollback. Installing, updating, enabling, disabling, or rolling back a Feature does not restart Telepiplex. A single restart is allowed only when the Host API contract itself changes.

## Runtime

```bash
docker compose up -d
```

Only `/config` is persistent. Feature data lives under `/config/plugins`; process sockets live in the container's ephemeral `/tmp/telepiplex` directory.

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789
plugins:
  root: /config/plugins
  catalog: https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml
  catalog_refresh_interval: 21600
  install_timeout: 300
  startup_timeout: 30
  drain_timeout: 120
  stabilize_seconds: 10
  restart_limit: 3
```

## Host API 1.2 and Telegram interactions

Host API 1.2 remains startup-compatible with API 1.0/1.1 Features. On top of the persisted operation status, explicit exit/cancel/rollback controls, cross-Feature handoff, and process-restart recovery introduced in 1.1, it adds safe `send_photo` / `edit_photo` poster actions. Features using those actions must declare `host_api: ">=1.2,<2.0"`. Both `/start` and Telegram's native command menu are generated from the manifests of currently enabled, routable Features. Disabled, dependency-blocked, and Host-reserved commands are not advertised as executable.

Each user may own only one active interaction at a time. While input is requested, Telepiplex accepts only ordinary text or callback IDs shown by the current status message. While work is running, cancelling, or rolling back, unrelated commands and stale buttons are blocked. Controls mean:

- **Exit** ends a pre-execution interaction without business changes.
- **Cancel task** stops later polling and pipeline stages and reports the actual stop point; already-completed remote effects are not described as rolled back when no exact inverse exists.
- **Cancel and roll back** appears only while every completed change has a stable identity and verified inverse. Conflicts or restore failures produce an explicit partial-rollback result with remaining objects.

Telepiplex owns configuration writes and Feature route switching as one coordinated task. Cancellation restores the previous configuration and route when verification succeeds and otherwise reports manual checks. Cancelling a 115 download never deletes downloaded content; an offline-task record is removed only when an exact InfoHash is known, using the mode that preserves source files.

## Feature installation and updates

`features/<plugin_id>` directories are the Feature development source. Runtime releases are immutable `.tpx` artifacts built from those directories in `main`. The container never checks out Git branches and Telepiplex images never contain business source code.

`plugins.catalog` accepts either a remote HTTPS URL or a local file path. New installations read `https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml` from the `catalog` branch by default; this is the official rolling catalog endpoint. The legacy default catalog is `<plugins.root>/catalog.yaml` (`/config/plugins/catalog.yaml` with the default configuration above); Telepiplex falls back to the official URL only when that legacy file is missing. An existing legacy file remains local; every other explicit local path preserves its local configuration intent even when its file is missing. Every Feature Release still carries the complete catalog snapshot and checksum current at publication time for offline or pinned operation; Telepiplex Releases do not carry a catalog. The catalog maps `name@version` to a local path or HTTPS release with a pinned SHA-256 digest:

```yaml
plugins:
  search:
    versions:
      "1.0.0":
        url: https://example.invalid/releases/search-1.0.0.tpx
        sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

Send `/plugin` in Telegram for first installation or routine management. Telepiplex lists installed Features and uninstalled catalog candidates. Only dependency-satisfied, ready candidates receive an Install button; a blocked candidate instead identifies its prerequisite Feature or exact missing capability. Installed Features receive an Update button whenever a newer release is available. Install and Update buttons target that Feature's newest stable, Host-compatible release and execute only after the authorized user selects them. Telepiplex never installs automatically, installs in bulk, or updates silently.

### Advanced/offline operations

Normal operation requires only `/plugin` and a button click. If the catalog is unavailable, a pinned version is required, or an offline package is being used, `/plugin install <name@version|artifact.tpx>` and `/plugin update <name@version|artifact.tpx>` remain available as exact-reference fallbacks.

Commands:

```text
/plugin install search@1.0.0
/plugin update search@1.0.0
/plugin enable search
/plugin disable search
/plugin rollback search
/plugin remove search
/plugin status search
/plugin doctor
```

An existing absolute `.tpx` path is also accepted by `install` and `update`. Telepiplex verifies and installs the new release, starts a shadow process, checks health, drains active work, and switches routes atomically. A failure at any stage keeps the old release active.

## Independent GitHub release channels

Telepiplex, Features, and the catalog publish independently. Only a `telepiplex-v<semver>` tag builds Telepiplex. The Mac development directory never creates tags or commits and never connects to a remote; release tags are created and published only from the sole Git workspace on Unraid after Syncthing is current.

That workflow first pushes the `linux/amd64` Telepiplex image `ghcr.io/<owner>/telepiplex:1.1.0` and `latest`, then creates the `telepiplex-v1.1.0` same-tag GitHub Release explicitly marked **Latest**. A Telepiplex Release has no Feature, catalog, or other assets and does not change a Feature. The five Feature tag families are `download-v<semver>`, `search-v<semver>`, `rename-v<semver>`, `sync-v<semver>`, and `caption-v<semver>`. Their first identity releases are `download-v1.0.0`, `search-v1.0.0`, `rename-v1.0.0`, `sync-v1.0.0`, and the placeholder `caption-v0.1.0`. These tags are also published only from Unraid.

The release order is fixed: publish and restart a Telepiplex version satisfying the Host API requirement first, then publish the new Features one at a time. The Feature workflow updates the catalog after each publication.

Each Feature tag builds or reuses exactly one immutable `.tpx`, creates that Feature's GitHub Release, and optimistically merges the result into the `catalog` branch. Feature Releases are created with `--latest=false`, so they never take the repository's **Latest** label; Latest always belongs to the most recently successful `telepiplex-v<semver>` Release. The branch holds the complete `catalog.yaml` and `catalog.yaml.sha256`; every HTTPS asset is pinned to its real SHA-256, `main` source identity, and commit, with `provides` / `requires` capability metadata derived from the verified manifest.

Each Feature Release carries a complete catalog snapshot. After Feature publication, the workflow reads the confirmed `catalog` branch snapshot again and replaces the catalog assets on that Feature Release, covering catalog advances caused by concurrent publications. Rolling readers always use the `catalog` branch; Telepiplex Releases and historical Platform Releases do not participate in catalog synchronization.

The Feature version in each `manifest.yaml` is an immutable `name@version` identity. Any change to code, artifact bytes, or source commit requires a version bump; the workflow rejects a reused version with a different digest or source. `search`, `download`, `rename`, `sync`, and `caption` are new technical identities with no compatibility aliases for the previous plugin IDs. Existing installations do not upgrade automatically, and the new catalog publishes only these five IDs.

Telepiplex refreshes the remote catalog once at startup and then checks the current release of each installed Feature for its newest stable, Host-compatible release every `catalog_refresh_interval: 21600` seconds (six hours). Refreshes require HTTPS, enforce size and schema limits, and replace the cache atomically. Network or catalog failures skip only that check and retain the last valid catalog; Telepiplex and other Features continue running.

When an update is available, Telepiplex sends one Telegram notification to `allowed_user` with the current version, target version, source commit, and “Confirm update” / “Not now” buttons. The existing verification, shadow startup, drain, atomic switch, and rollback transaction runs only after an authorized user selects “Confirm update”; Telepiplex never updates silently. Offline deployments can save the released catalog as `/config/plugins/catalog.yaml` and point the configuration back to that local path.

## Development and verification

Telepiplex, the SDK, and `.tpx` build tools stay in one directory tree. Each Feature source directory depends only on the Host API/SDK contract and never imports another Feature.

```bash
python3 tools/build_tpx.py --help
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -t .
```
