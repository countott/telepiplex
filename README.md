# Telepiplex Media Search

`feature/media-search` 是媒体搜索单点能力分支，替代旧 `feature/prowlarr-search`。

这个分支保留 `/search` 和 `/s`、Prowlarr 搜索、结果评分、元数据链接解析、候选确认、TVDB/Douban/AI 兜底解析，以及到 `download_task` 的交接合同。实际 115 投递和下载后整理由 `main` 缝合，不在本分支实现。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/start` | 显示媒体搜索能力说明 |
| `/reload` | 重载配置 |
| `/search 片名` | 搜索片源 |
| `/s 片名` | `/search` 的短命令 |

## 配置

运行时配置路径仍是容器内 `/config/config.yaml`。本分支需要：

```yaml
search:
  enable: true
  prowlarr:
    base_url: "http://your-prowlarr:9696"
    api_key: "your_prowlarr_api_key"
```

TVDB 和 AI 是搜索确认链的可选增强配置。

## 本地验证

```bash
python3 -m unittest tests/test_media_search_surface.py tests/test_media_search_utils.py
python3 -m py_compile app/115bot.py app/init.py app/handlers/search_handler.py app/handlers/download_handler.py app/adapters/prowlarr.py app/adapters/tvdb.py app/utils/search_query.py app/utils/search_resolution.py app/utils/release_score.py app/utils/ai.py app/utils/media_metadata.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```
