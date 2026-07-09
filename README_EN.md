# Telepiplex Media Search

`feature/media-search` is the single-feature media search branch. It replaces the old `feature/prowlarr-search` name.

This branch keeps `/search` and `/s`, Prowlarr search, release scoring, metadata-link parsing, candidate confirmation, TVDB/Douban/AI fallback resolution, and the `download_task` handoff contract. Actual 115 delivery and post-download organization are stitched in by `main`.

## Commands

| Command | Description |
| --- | --- |
| `/start` | Show media-search capability notes |
| `/reload` | Reload configuration |
| `/search title` | Search releases |
| `/s title` | Short form of `/search` |

## Configuration

Runtime configuration still lives at `/config/config.yaml`. This branch needs:

```yaml
search:
  enable: true
  prowlarr:
    base_url: "http://your-prowlarr:9696"
    api_key: "your_prowlarr_api_key"
```

TVDB and AI are optional enhancements for the search confirmation chain.

## Local Verification

```bash
python3 -m unittest tests/test_media_search_surface.py tests/test_media_search_utils.py
python3 -m py_compile app/115bot.py app/init.py app/handlers/search_handler.py app/handlers/download_handler.py app/adapters/prowlarr.py app/adapters/tvdb.py app/utils/search_query.py app/utils/search_resolution.py app/utils/release_score.py app/utils/ai.py app/utils/media_metadata.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```
