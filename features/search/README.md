# search Feature

该分支只包含媒体搜索 Feature 源码。普通文本先由 AI 理解查询意图并强制调用 `search_media_sources`，首轮并行轻查 Wikipedia 中英文、无需 Key 的豆瓣证据和 TVDB；AI 可根据规范化首轮结果自主决定是否进行最多两轮定向深查。程序随后验证 AI 引用的来源事实与同实体关联，形成当前请求内的媒体实体：1–7 个合格候选全部展示，超过 7 个要求用户缩小范围。

Prompt 用于指导模型，工具 Schema、调用预算、凭据隔离和证据验证器负责硬约束。AI 只能引用本次工具返回的事实，不能制造稳定 ID、官方标题、年份、海报、Prowlarr 查询或最终媒体契约，也不能自动选择同名候选。最终 Prowlarr Query、发布结果门禁和 `media_metadata v1` 都不会交给 AI。

电影确认后只按官方英文标题或日文罗马字标题搜索。剧集确认后再选择全剧、指定季或指定集，Prowlarr 查询只使用标题、`S01` 或 `S01E01`，绝不会从 TVDB 第一集推导 `S01E01`。关联电影的检索身份与整理身份分离：Prowlarr 始终按电影搜索，本次任务可选择独立整理或归入目标剧集 Specials。

豆瓣和 TVDB 条目链接继续由确定性程序锁定稳定 ID，不经过 AI 身份选择。AI 不可用、不支持工具协议、调用越界或验证失败时，普通文本会回退到原有确定性来源链路。

它同时提供无状态的 `media.search.resolve_metadata`，供 direct magnet 下载后的 rename 实时复用同一套证据门禁。用户确认后的 `media_metadata v1` 与 `naming_metadata` 仍按原合同传给 `download.provider`，再由下载完成事件交给 rename；搜索证据、候选、评分、范围和关系选择均即用即弃，不创建媒体实体数据库。

运行配置位于 `/config/plugins/search/config.yaml`。Feature 不包含 Telepiplex、Telegram 或其他 Feature 源码。

Wikipedia 和豆瓣默认可直接取证，不需要额外 API Key。TVDB 与 AI 默认启用，但仍分别需要填写 TVDB API Key，以及 AI API URL、Key 和模型。所有 TVDB/AI 凭据只由服务端适配器读取，不会进入模型消息或工具结果。任一来源关闭、凭据缺失、鉴权失败、超时、限流、被拦截或服务不可用时都会保留独立状态，其余来源仍可继续工作。

Prowlarr 结果先经过身份与范围正确性硬门禁，再进行片源质量评分；单集、单季和多季包不会混排，最多展示 12 个结果且不会自动降级范围。公开配置入口是 `search.scoring`：
- `prefer_resolution`、`prefer_source`、`prefer_codec`、`prefer_audio`、`reject_keywords` 定义默认关键词组
- `keyword_scores` 用于标题关键词加权
- `indexer_scores` 用于按 indexer 名称加权

如果不填 `search.scoring`，Feature 会回退到内置默认权重。

```bash
python tools/build_feature.py features/search /tmp/search-1.0.3.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
