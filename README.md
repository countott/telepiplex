# media-search Feature

该分支只包含媒体搜索 Feature 源码。它使用 Wikipedia、无需 Key 的豆瓣证据和 TVDB 形成当前请求内的媒体实体：1–7 个合格候选全部展示，超过 7 个要求用户缩小范围；豆瓣和 TVDB 条目链接直接锁定实体。AI 只在规则无法理解自然语言或选中电影需要验证浅层关系时提供待外部来源校验的提示，不参与候选评分，也不能生成稳定 ID、Prowlarr 查询或最终媒体契约。

电影确认后按官方英文标题或日文罗马字标题加年份搜索。剧集确认后再选择全剧、指定季或指定集，绝不会从 TVDB 第一集推导 `S01E01`。关联电影的检索身份与整理身份分离：Prowlarr 始终按电影搜索，本次任务可选择独立整理或归入目标剧集 Specials。

它同时提供无状态的 `media.search.resolve_metadata`，供 direct magnet 下载后的 renaming 实时复用同一套证据门禁。搜索证据、候选、评分、范围和关系选择均即用即弃，不创建媒体实体数据库。

运行配置位于 `/config/plugins/media-search/config.yaml`。Feature 不包含 Core、Telegram 或其他 Feature 源码。

Wikipedia 和豆瓣默认可直接取证。TVDB 与 AI 默认启用，但仍分别需要填写 TVDB API Key，以及 AI API URL、Key 和模型；凭证缺失时会如实降级为不可用状态。

Prowlarr 结果会在本地二次打分后再展示。公开配置入口是 `search.scoring`：
- `prefer_resolution`、`prefer_source`、`prefer_codec`、`prefer_audio`、`reject_keywords` 定义默认关键词组
- `keyword_scores` 用于标题关键词加权
- `indexer_scores` 用于按 indexer 名称加权

如果不填 `search.scoring`，Feature 会回退到内置默认权重。

```bash
python /opt/telepiplex/tools/build_feature.py . dist/media-search-1.3.0.tpx
```
