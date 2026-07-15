# renaming Feature

该分支只包含媒体整理 Feature。它消费 durable `download.completed`，优先使用 open115 提供的真实下载根、完整文件树和 Prowlarr 片源证据，通过 `storage.provider` 操作 115 文件，并在成功后发布 `media.organized`。direct magnet 没有 canonical contract 时，会先调用 `media.search.resolve_metadata` 复用 Wikipedia、豆瓣、TVDB 与 AI 证据门禁。

普通电影按计划文件名、唯一候选、AI 证据、可解释大小兜底的固定顺序选择主视频；剧集的未匹配大视频必须由 AI 明确判定。所有目标冲突在第一次写操作前预检，映射冲突或证据不足时整个下载根进入 `/未整理`，不执行部分业务移动。完成整理后只保留已确认目标视频；字幕、NFO、海报及其他下载附属文件随源下载根清理。

```bash
python /opt/telepiplex/tools/build_feature.py . dist/renaming-1.1.0.tpx
```
