# media-search Feature

该分支只包含媒体搜索 Feature 源码。它先使用 Wikipedia、无需 Key 的豆瓣证据和 TVDB 执行严格规则判定；证据能够唯一确认普通媒体时直接形成 canonical media metadata，存在歧义或复杂关系时才调用两阶段 AI。确认后只用英文或原始标题查询 Prowlarr；Prowlarr 不负责中文标题回填。选择片源后通过 `download.provider` RPC 提交下载计划。

它同时提供 `media.search.resolve_metadata`，供 direct magnet 下载后的 renaming 复用同一套证据门禁。该能力只生成 canonical metadata，不搜索 Prowlarr，也不提交下载。

运行配置位于 `/config/plugins/media-search/config.yaml`。Feature 不包含 Core、Telegram 或其他 Feature 源码。

Wikipedia 和豆瓣默认可直接取证。TVDB 与 AI 默认启用，但仍分别需要填写 TVDB API Key，以及 AI API URL、Key 和模型；凭证缺失时会如实降级为不可用状态。

```bash
python /opt/telepiplex/tools/build_feature.py . dist/media-search-1.0.0.tpx
```
