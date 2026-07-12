# media-search Feature

该分支只包含媒体搜索 Feature 源码。它使用 AI + Wikipedia/TVDB 形成并确认 canonical media metadata，再用英文标题查询 Prowlarr；Prowlarr 不负责中文标题回填。选择片源后通过 `download.provider` RPC 提交下载计划。

运行配置位于 `/config/plugins/media-search/config.yaml`。Feature 不包含 Core、Telegram 或其他 Feature 源码。

```bash
python /opt/telepiplex/tools/build_feature.py . dist/media-search-1.0.0.tpx
```
