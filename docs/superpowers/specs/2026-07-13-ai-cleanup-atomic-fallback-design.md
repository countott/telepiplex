# renaming AI 清理与整批失败设计

## 输入契约

renaming 优先消费 open115 发布的真实 `download_root`、完整 `file_tree`、Prowlarr `release` 证据和已确认 `media_metadata`。旧事件仍可通过 `storage.provider` 重建文件树。

direct magnet 没有 canonical contract 时，renaming 先调用 `media.search.resolve_metadata`，由 media-search 重新查询 Wikipedia、豆瓣、TVDB，并在严格证据不足时调用 AI。成功后进入同一 canonical 流程；失败时保留 legacy TVDB+AI 兼容路径，最终仍不能确认则整批进入未整理。

## 普通电影主视频

选择顺序固定为：

1. contract `source_hint` 或 Prowlarr release title 能唯一精确定位的文件；
2. 目录中唯一视频；
3. 多候选时调用 AI，输入包含 contract 证据、Prowlarr 片源、下载根和完整文件树；
4. AI 不可用或无有效答案时，仅在最大文件相对第二名达到可解释比例时使用大小兜底；否则拒绝整理。

AI 返回的主视频必须精确存在于文件树。未选视频只会随源下载根清理，不会被误当成目标媒体。

## 剧集未匹配视频

文件名能够唯一覆盖确认集数时先走规则。规则映射之外的小样片可按明确大小规则清理；任何未匹配的大视频都必须由 AI 明确列入 `discard_files`，否则整批拒绝整理。

## 冲突和未整理

所有目标路径冲突都在第一次改名或移动前预检。冲突、映射不完整或 AI 无法确认时，不执行目标移动，而是把原下载根整体移动到 `unorganized_path`。不再保留“发生已知映射冲突后部分移动”的业务结果。

