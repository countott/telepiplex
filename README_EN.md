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
  catalog: /config/plugins/catalog.yaml
  install_timeout: 300
  startup_timeout: 30
  drain_timeout: 120
  stabilize_seconds: 10
  restart_limit: 3
```

## Feature installation and updates

Feature branches are development source. Runtime releases are immutable `.tpx` artifacts built from those branches. The container never checks out Git branches and Core images never contain business source code.

`/config/plugins/catalog.yaml` maps `name@version` to a local path or HTTPS release with a pinned SHA-256 digest:

```yaml
plugins:
  media-search:
    versions:
      "1.2.0":
        url: https://example.invalid/releases/media-search-1.2.0.tpx
        sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

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

GitHub Actions builds and pushes the `linux/amd64` Core image `ghcr.io/<owner>/telepiplex-core:1.0.0`. The same release builds Linux `.tpx` assets for `open115`, `media-search`, `renaming`, and `plex-management` from their independent Feature branches. It also publishes `catalog.yaml` and `catalog.yaml.sha256`; every HTTPS asset is pinned to its real SHA-256, Feature branch, and commit.

The version in each Feature `manifest.yaml` is an immutable `name@version` identity. A code change requires a version bump, and the workflow rejects a reused version with different bytes. Until OPS-TODO-01B adds remote discovery and Telegram confirmation, place the released `catalog.yaml` at `/config/plugins/catalog.yaml`; Core never updates silently.

## Development and verification

Core, the SDK, and `.tpx` build tools stay in the same repository. Feature branches depend only on the Core API/SDK contract and never import another Feature.

```bash
python3 tools/build_tpx.py --help
python3 -m unittest discover -s tests -t .
git diff --check
```
