# search 元数据回查能力设计

## 目的

direct magnet 没有预先确认的 `media_metadata`。rename 仍需要通过 TVDB、豆瓣和 Wikipedia 回查并在必要时调用 AI，但不能复制 search 的证据规则和 API 适配器。

## 契约

search 提供独占 `media.search` capability 的 `resolve_metadata` 方法。输入是从下载根、文件名和片源标题组合出的查询；输出是通过现有严格证据门禁生成并确认的 canonical `media_metadata`、对应 `naming_metadata` 及查询证据。该方法不搜索 Prowlarr、不提交下载、不产生 Telegram 交互。

如果证据或 AI 不能形成有效计划，能力调用失败，rename 将整批送入未整理。

