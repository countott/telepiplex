# renaming Feature

该分支只包含媒体整理 Feature。它消费 durable `download.completed`，通过 `storage.provider` 操作 115 文件，并在成功后发布 `media.organized`。

规则顺序：普通电影/剧集先按 canonical metadata 和文件名确定性命名；只有文件名无法覆盖锁定集数时才调用 AI 做 patch 型映射，S00 等复杂关系不会干扰普通路径。完成整理后只保留已映射的目标视频：额外/小视频、字幕、NFO、海报及其他文件全部删除。无法形成可靠映射的整个任务进入 `/未整理`。

```bash
python /opt/telepiplex/tools/build_feature.py . dist/renaming-1.0.0.tpx
```
