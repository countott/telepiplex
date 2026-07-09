# Telepiplex Core

`feature/telepiplex-core` is the core runtime branch for Telepiplex. It contains shared startup code, configuration loading, logging, the message queue, user checks, and the basic Telegram Bot runtime.

This branch does not include 115 delivery, media search, Prowlarr, TVDB, Plex, Aria2, video transfer, or media organization features. Business capabilities should be extracted from the current `main` branch into dedicated feature branches, then stitched together by `main`.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Show core runtime status |
| `/reload` | Reload `/config/config.yaml` |

## Configuration

Runtime configuration still lives at `/config/config.yaml` inside the container:

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789

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

`category_folder` is the shared save-directory contract for business branches. The core branch itself does not download or organize media.

## Local Verification

```bash
python3 -m unittest tests/test_telepiplex_core_surface.py
python3 -m py_compile app/115bot.py app/init.py app/utils/message_queue.py app/utils/logger.py app/utils/log_sanitizer.py app/utils/directory_config.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

## Branch Role

- `main`: current integrated business implementation.
- `feature/telepiplex-core`: core runtime only.
- `feature/115`: 115 single-feature branch.
- `feature/media-search`: media search feature branch, replacing old `feature/prowlarr-search`.
