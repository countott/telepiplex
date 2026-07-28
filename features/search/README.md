# search Feature

该目录只包含媒体搜索 Feature 源码。Search 1.1.1 将普通文本和 Wikipedia、豆瓣、TVDB 直链接入同一条候选、严格元数据与 Prowlarr 管线。

普通文本通过 `search_media_sources` 并发广泛召回全部已启用 Provider 的事实，不按标题相似度、年份一致性或来源数量提前淘汰；来源工具仍允许最多两轮定向深查。AI 只负责把真实事实 ID 整理为 0–6 个作品候选，识别跨来源身份以及 root、season、episode、related work 层级；程序随后验证引用并补查缺失 Provider。候选保存全部已确认的来源链接、稳定 ID 和海报，无法确认的来源保留为 `unresolved`。Wikipedia、豆瓣和 TVDB 三个来源均已确认时，候选标记为 `v1`；补全后仍只有一至两个来源时标记为可展示的 `v0`，同时显示具体失败来源，不因来源不全提前丢弃候选。首轮零事实时允许 AI 生成一次纠错或别名查询，但最终候选仍必须来自 Provider 的真实事实。

纯数字片名需要用中文或英文引号明确标记，例如 `/s "1917"` 或 `/s “1917”`；引号只用于声明这是片名，不进入 Provider 查询。未加引号的纯数字仍按普通数字输入处理。

直链入口先精确读取稳定 ID 并锁定用户锚点，再用页面事实反查其他 Provider。稳定 ID 可直接证明时由程序绑定；存在多个近似条目时由 AI 选择属于锚点的事实，但不能改变锚定作品或生成第二组候选。文本候选在展示前完成跨来源补全；直链始终形成一个锁定候选。

文本最终只有一个候选时自动进入下一步；2–6 个候选通过一条编号海报拼图消息交给用户选择；零候选明确返回 `no_match`。拼图缺失单张海报时使用编号占位卡，全部缺失时降级为文本。候选一经展示即冻结链接集合，选中后禁止重新按标题搜索 Provider。

选中后只精确读取冻结候选中已经保存的链接，由这些页面构建严格 `media_metadata v1`。候选阶段已经齐全的 `v1` 直接进入精确读取；补全失败的 `v0` 保留已有链接、海报和失败状态，并沿用与用户直链相同的精确读取与元数据门禁。严格 v1 保存根身份、稳定 ID、全部来源链接、季集层级、TVDB inventory、标题、别名、海报、字段来源、AI 判断与 unresolved 状态。媒体类型冲突返回 `metadata_conflict`；必要字段不足返回 `metadata_incomplete`；固定链接本身读取失败单独报告。标题或年份的来源差异保留并生成警告。

《蜂蜜与四叶草》这类来源粒度不同的剧集会形成一个层级候选：TVDB 和 Wikipedia 保存为 `series_root`，豆瓣第一、二季分别保存为 season 1、season 2；真人电影仍是独立作品。AI 判断的季集号必须通过 TVDB inventory 验证，失败时不得强行挂载。

v1 通过后才由程序生成 Prowlarr Query，最终元数据和查询构造不会交给 AI。日本动画依次优先使用官方罗马字、官方英文名、其他来源拉丁别名、原名和用户原文，其他作品使用同一事实优先级并去重；季集后缀只来自 v1。Prowlarr 不参与作品身份判断。

Search 1.1.1 不设置 30/65/90 秒业务规划预算，也不会把 AI 较慢转换成无候选。Provider、AI 和 Prowlarr 的 HTTP 客户端仍使用可配置故障超时。AI 技术故障不会退化为程序评分候选，交互会明确区分来源失败、来源限流、AI `no_match`、AI 故障、候选绑定失败、固定链接读取失败、Prowlarr 全部失败和 v1 不完整，并在已进入前台链路时提供对应提示以及重试、取消和退出。一个或两个来源成功时仍展示 `v0` 候选；零事实时明确列出失败来源；所有来源均不可用时明确告知当前来源全部不可用。

Feature 同时提供无状态的 `media.search.resolve_metadata`，供 direct magnet 下载后的 rename 实时复用同一套证据门禁。用户确认后的 `media_metadata v1` 与 `naming_metadata` 仍按合同传给 `download.provider`，再由下载完成事件交给 rename；搜索证据与候选仍是当前请求内状态，不创建媒体实体数据库。

运行配置位于 `/config/plugins/search/config.yaml`。Feature 不包含 telepiplex、Telegram 或其他 Feature 源码。

Wikipedia 和豆瓣默认可直接取证，不需要额外 API Key。TVDB 与 AI 默认启用，但仍分别需要填写 TVDB API Key，以及 AI API URL、Key 和模型。所有 TVDB/AI 凭据只由服务端适配器读取，不会进入模型消息或工具结果。任一来源关闭、凭据缺失、鉴权失败、超时、限流、被拦截或服务不可用时都会保留独立状态；其余来源会继续工作，最终是否可继续由严格 v1 完整性决定。

Prowlarr 继续用 Movie/TV 分类做媒体类型粗筛，并按已启用 Indexer 和当前范围的每条 Query 独立有界并发查询；成功的查询返回多少就增量合并、门禁、评分和更新多少。一个 Query 失败不会丢弃同 Indexer 的其他结果，只有全部 Query 都失败才把该 Indexer 计为异常。搜索中和完成后都只显示当前 Top 12：海报候选卡保留作品身份，片源消息不重复长片名，只显示最终可选条目数、Indexer 完成数和异常数；每条用一行展示范围、去重后的画质/片源/编码/动态范围/声道与音频格式、版本标记、约数大小、做种和发布组。显式 `2CH` 会显示为 `2.0`，但不会仅凭 AAC 推断声道。相同片源的多 Query 或多 Indexer 镜像在内部合并，不向用户暴露“版本”或“来源组”概念；原始评分与真实错误详情只保留在内部状态和日志。按钮内部绑定稳定片源 ID，后续重排不会改变已经显示过的按钮所指向的片源。`search.prowlarr.timeout` 是全局搜索上限，`search.prowlarr.indexer_timeout` 是单 Indexer 上限（默认 75 秒）。

Prowlarr 结果先经过身份与范围正确性硬门禁，再进行片源质量评分；`Season 02` 和 `Complete Season 02` 都会按明确的第二季整季包解析，不能混入其他季或全剧结果。单集、单季和多季包不会混排，最多展示 12 个结果且不会自动降级范围。公开配置入口是 `search.scoring`：
- `prefer_resolution`、`prefer_source`、`prefer_codec`、`prefer_audio`、`reject_keywords` 定义默认关键词组
- `keyword_scores` 用于标题关键词加权
- `indexer_scores` 用于按 indexer 名称加权

如果不填 `search.scoring`，Feature 会回退到内置默认权重。

```bash
python tools/build_feature.py features/search /tmp/search-1.1.1.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
