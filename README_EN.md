# Telepiplex

Telepiplex is a Telegram media delivery and organization bot. `main` is the deployable runtime branch and loads the stable modules by default:

- `app.modules.open115`: 115 authorization, save directories, and offline delivery.
- `app.modules.media_search`: Prowlarr media search, candidate confirmation, and download request submission.
- `app.modules.renaming`: post-download lookup, organization, and renaming.

`feature/telepiplex-core`, `feature/115`, `feature/media-search`, and `feature/renaming` remain module development boundaries. Deployable images should track `main`.

## Telegram Commands

| Command | Description |
| --- | --- |
| `/start` | Show runtime status |
| `/modules` | Show module status |
| `/reload` | Reload `/config/config.yaml`; it does not hot-load Telegram handlers |
| `/config` | Configure 115 tokens |
| `/auth` | Authorize 115 by QR scan |
| `/magnet`, `/m` | Submit a magnet link |
| `/search`, `/s` | Search media and submit a download request |

Restart the container after module code updates or module configuration changes.

## Configuration

Runtime configuration lives at `/config/config.yaml` inside the container. If `modules` is omitted, the deployable runtime behaves as:

```yaml
modules:
  enabled: all
  disabled: []
```

To temporarily disable a stable module:

```yaml
modules:
  enabled: all
  disabled:
    - app.modules.renaming
```

Minimal configuration example:

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789

modules:
  enabled: all
  disabled: []

category_folder:
  - name: 真人电影
    path: /真人电影
  - name: 动画电影
    path: /动画电影
  - name: 真人剧集
    path: /真人剧集
  - name: 动画剧集
    path: /动画剧集
```

Prowlarr settings still belong under `search.prowlarr` in `/config/config.yaml`, especially `search.prowlarr.api_key`.

## Branch Roles

- `main`: deployable composed runtime branch.
- `feature/telepiplex-core`: core runtime only.
- `feature/115`: 115 single-feature branch.
- `feature/media-search`: media search feature branch.
- `feature/renaming`: post-download renaming and organization feature branch.

## Local Verification

```bash
python3 -m unittest tests/test_bot_runtime_startup.py tests/test_composable_integration.py tests/test_composable_core.py
python3 -m py_compile $(git ls-files '*.py')
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```
