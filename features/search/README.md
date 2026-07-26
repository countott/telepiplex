# search Feature

该分支只包含媒体搜索 Feature 源码。普通文本先由 AI 理解查询意图并强制调用 `search_media_sources`，首轮并行轻查 Wikipedia 中英文、无需 Key 的豆瓣证据和 TVDB；AI 可根据规范化首轮结果自主决定是否进行最多两轮定向深查。程序随后验证 AI 引用的来源事实与同实体关联，形成当前请求内的媒体实体：1–7 个合格候选全部展示，超过 7 个要求用户缩小范围。

Prompt 用于指导模型，工具 Schema、调用预算、凭据隔离和证据验证器负责硬约束。AI 只能引用本次工具返回的事实，不能制造稳定 ID、官方标题、年份、海报、Prowlarr 查询或最终媒体契约，也不能自动选择同名候选。最终 Prowlarr Query、发布结果门禁和 `media_metadata v1` 都不会交给 AI。

电影确认后只按官方英文标题或日文罗马字标题搜索，不支持电影系列/合集搜索。剧集确认后再选择全剧、指定季或指定集：经 TVDB 验证只有第一季的全剧会并行搜索 `Title S01`、`Title Season 01`、`Title Complete`；多季全剧会并行搜索 `Title S01-S03`、`Title Complete`；指定季会并行搜索 `Title S02`、`Title Season 02`，以覆盖包含 `Complete Season 02` 的整季标题；指定集只搜索 `Title S02E05`。这些范围都不回退裸标题，也绝不会从 TVDB 第一集推导 `S01E01`。关联电影的检索身份与整理身份分离：Prowlarr 始终按电影搜索，本次任务可选择独立整理或归入目标剧集 Specials。

豆瓣和 TVDB 条目链接继续由确定性程序锁定稳定 ID，不经过 AI 身份选择。AI 不可用、不支持工具协议、调用越界或验证失败时，普通文本会回退到原有确定性来源链路。

它同时提供无状态的 `media.search.resolve_metadata`，供 direct magnet 下载后的 rename 实时复用同一套证据门禁。用户确认后的 `media_metadata v1` 与 `naming_metadata` 仍按原合同传给 `download.provider`，再由下载完成事件交给 rename；搜索证据、候选、评分、范围和关系选择均即用即弃，不创建媒体实体数据库。

运行配置位于 `/config/plugins/search/config.yaml`。Feature 不包含 Telepiplex、Telegram 或其他 Feature 源码。

Wikipedia 和豆瓣默认可直接取证，不需要额外 API Key。TVDB 与 AI 默认启用，但仍分别需要填写 TVDB API Key，以及 AI API URL、Key 和模型。所有 TVDB/AI 凭据只由服务端适配器读取，不会进入模型消息或工具结果。任一来源关闭、凭据缺失、鉴权失败、超时、限流、被拦截或服务不可用时都会保留独立状态，其余来源仍可继续工作。

Prowlarr 继续用 Movie/TV 分类做媒体类型粗筛，并按已启用 Indexer 和当前范围的每条 Query 独立有界并发查询；成功的查询返回多少就增量合并、门禁、评分和更新多少。一个 Query 失败不会丢弃同 Indexer 的其他结果，只有全部 Query 都失败才把该 Indexer 计为异常。搜索中和完成后都只显示当前 Top 12：海报候选卡保留作品身份，片源消息不重复长片名，只显示最终可选条目数、Indexer 完成数和异常数；每条用一行展示范围、去重后的画质/片源/编码/动态范围/声道与音频格式、版本标记、约数大小、做种和发布组。显式 `2CH` 会显示为 `2.0`，但不会仅凭 AAC 推断声道。相同片源的多 Query 或多 Indexer 镜像在内部合并，不向用户暴露“版本”或“来源组”概念；原始评分与真实错误详情只保留在内部状态和日志。按钮内部绑定稳定片源 ID，后续重排不会改变已经显示过的按钮所指向的片源。`search.prowlarr.timeout` 是全局搜索上限，`search.prowlarr.indexer_timeout` 是单 Indexer 上限（默认 75 秒）。

Prowlarr 结果先经过身份与范围正确性硬门禁，再进行片源质量评分；`Season 02` 和 `Complete Season 02` 都会按明确的第二季整季包解析，不能混入其他季或全剧结果。单集、单季和多季包不会混排，最多展示 12 个结果且不会自动降级范围。公开配置入口是 `search.scoring`：
- `prefer_resolution`、`prefer_source`、`prefer_codec`、`prefer_audio`、`reject_keywords` 定义默认关键词组
- `keyword_scores` 用于标题关键词加权
- `indexer_scores` 用于按 indexer 名称加权

如果不填 `search.scoring`，Feature 会回退到内置默认权重。

```bash
python tools/build_feature.py features/search /tmp/search-1.0.8.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
