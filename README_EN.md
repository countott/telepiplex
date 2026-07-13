# Telepiplex Core

`feature/telepiplex-core` is the only long-running Docker runtime. The container has one permanent Core process; business capabilities such as 115, media search, renaming, and Plex management run as isolated Feature child processes instead of in-process modules or a stitched `main` runtime.

Each Feature owns its Python virtual environment, configuration, state, and versioned release directory. Core talks to declared capabilities over Unix Domain Sockets and owns command routing, event delivery, health checks, draining, switching, and rollback. Installing, updating, enabling, disabling, or rolling back a Feature does not restart Core. A single restart is allowed only when the Core API contract itself changes.

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
  catalog: https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml
  catalog_refresh_interval: 21600
  install_timeout: 300
  startup_timeout: 30
  drain_timeout: 120
  stabilize_seconds: 10
  restart_limit: 3
```

## Feature installation and updates

Feature branches are development source. Runtime releases are immutable `.tpx` artifacts built from those branches. The container never checks out Git branches and Core images never contain business source code.

`plugins.catalog` accepts either a remote HTTPS URL or a local file path. The default points at the aggregate release catalog. The legacy default catalog is `<plugins.root>/catalog.yaml` (`/config/plugins/catalog.yaml` with the default configuration above); Core falls back to the official URL only when that legacy file is missing: `https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml`. An existing legacy file remains local; every other explicit local path preserves its local configuration intent even when its file is missing. For offline or pinned operation, download the catalog and configure its local path. The catalog maps `name@version` to a local path or HTTPS release with a pinned SHA-256 digest:

```yaml
plugins:
  media-search:
    versions:
      "1.2.0":
        url: https://example.invalid/releases/media-search-1.2.0.tpx
        sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

Send `/plugin` in Telegram for first installation or routine management. Core lists installed Features and uninstalled catalog candidates. Only dependency-satisfied, ready candidates receive an Install button; a blocked candidate instead identifies its prerequisite Feature or exact missing capability. Installed Features receive an Update button whenever a newer release is available. Install and Update buttons target that Feature's newest stable, Core-compatible release and execute only after the authorized user selects them. Core never installs automatically, installs in bulk, or updates silently.

### Advanced/offline operations

Normal operation requires only `/plugin` and a button click. If the catalog is unavailable, a pinned version is required, or an offline package is being used, `/plugin install <name@version|artifact.tpx>` and `/plugin update <name@version|artifact.tpx>` remain available as exact-reference fallbacks.

Commands:

```text
/plugin install media-search@1.2.0
/plugin update media-search@1.3.0
/plugin enable media-search
/plugin disable media-search
/plugin rollback media-search
/plugin remove media-search
/plugin status media-search
/plugin doctor
```

An existing absolute `.tpx` path is also accepted by `install` and `update`. Core verifies and installs the new release, starts a shadow process, checks health, drains active work, and switches routes atomically. A failure at any stage keeps the old release active.

## Aggregate GitHub releases

Production releases use a `platform-v<semver>` tag:

```bash
git tag platform-v1.0.0
git push origin platform-v1.0.0
```

GitHub Actions builds and pushes the `linux/amd64` Core image `ghcr.io/<owner>/telepiplex-core:1.0.0`. The same release builds Linux `.tpx` assets for `open115`, `media-search`, `renaming`, and `plex-management` from their independent Feature branches. It also publishes `catalog.yaml` and `catalog.yaml.sha256`; every HTTPS asset is pinned to its real SHA-256, Feature branch, and commit, with `provides` / `requires` capability metadata derived from the verified manifest.

The version in each Feature `manifest.yaml` is an immutable `name@version` identity. A code change requires a version bump, and the workflow rejects a reused version with different bytes.

Core refreshes the remote catalog once at startup and then checks the current release of each installed Feature for its newest stable, Core-compatible release every `catalog_refresh_interval: 21600` seconds (six hours). Refreshes require HTTPS, enforce size and schema limits, and replace the cache atomically. Network or catalog failures skip only that check and retain the last valid catalog; Core and other Features continue running.

When an update is available, Core sends one Telegram notification to `allowed_user` with the current version, target version, source commit, and “Confirm update” / “Not now” buttons. The existing verification, shadow startup, drain, atomic switch, and rollback transaction runs only after an authorized user selects “Confirm update”; Core never updates silently. Offline deployments can save the released catalog as `/config/plugins/catalog.yaml` and point the configuration back to that local path.

## Development and verification

Core, the SDK, and `.tpx` build tools stay in the same repository. Feature branches depend only on the Core API/SDK contract and never import another Feature.

```bash
python3 tools/build_tpx.py --help
python3 -m unittest discover -s tests -t .
git diff --check
```
