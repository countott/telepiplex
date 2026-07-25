# Search 按索引器增量片源设计

## 目标

Prowlarr 搜索不再由一个慢索引器阻塞整批结果。Search 并发查询每个已启用
Indexer；任一 Indexer 返回后立即执行既有正确性门禁与评分排序，并更新同一条
Telegram 消息。用户可在搜索未完成时选择已经出现的片源。

## 边界

- 不改变媒体身份确认、片源门禁或评分权重。
- 不新增 Telegram 消息类型；继续通过 operation 状态编辑同一条消息。
- 不让单个 Indexer 的失败终止其他 Indexer。
- 全局搜索超时保持 `search.prowlarr.timeout`，默认 200 秒。
- 新增单 Indexer 超时 `search.prowlarr.indexer_timeout`，默认 75 秒，用于切断
  FlareSolverr 重试造成的长尾。
- Search 发布版本升级为 `1.0.5`。

## Prowlarr 查询

1. 读取 `/api/v1/indexer`，筛选已启用且符合 `indexer_ids` 的 Indexer。
2. 每个 Indexer 独立请求 `/api/v1/search`，只传该 Indexer 的 ID。
3. 并发任务独立收集成功结果或结构化错误。
4. 若 Indexer 列表不可用，则保留原聚合搜索作为兼容兜底。
5. 聚合结果按稳定片源身份去重，再进入既有 `gate_releases` 和
   `rank_releases`。

单 Indexer 超时或 HTTP 错误只进入报告的异常统计，不清除其他 Indexer 已返回
的结果。

## 增量状态与节流

每次有 Indexer 完成时重新计算当前 Top 12。第一次出现可选结果时立即上报；
后续更新至少间隔 1.25 秒，最终结果必须上报。这样用户能尽快选择，同时避免
同一聊天每秒多次编辑。

搜索进行中保持 operation `state=running`、`stage=prowlarr_search`，并把片源
按钮放入 `details.keyboard`。全部 Indexer 完成后改为
`state=awaiting_input`、`stage=release_selection`。

## 稳定选择

显示序号只用于当前排名。每个片源由 magnet、下载地址、详情地址或完整来源字段
计算 16 位稳定摘要，回调格式为：

```text
search:release:<plan_id>:<release_id>
```

Search 保存所有已通过门禁片源的 `release_by_id` 映射，而不只保存当前 Top 12。
因此旧消息中的按钮即使在后续重排后被替换，Telegram 已送达的旧回调仍会选中原
片源，不会按新排名误选。

用户点击片源后，Search 先确认 ID 有效，再标记结果冻结、取消剩余 Indexer
任务，然后解析并提交该 ID 对应的精确片源。搜索任务因“已选择”而取消时不得
清理计划或覆盖提交状态。

## 紧凑 Telegram 报告

有结果时报告严格由两行摘要和最多 12 行片源组成：

```text
🔍 Constantine 2005
搜索结果 12｜索引器完成 1/3｜异常 2
① 128分｜整片｜4K / REMUX / HEVC｜做种46｜~35G｜标题…
```

- 第一行只显示放大镜和 Query。
- 第二行只显示当前结果数、已完成/总 Indexer 数和异常数。
- 每个片源恰好一行；不显示门禁明细、来源计数、Indexer 名称或各项加减分。
- `2160p` 在 UI 中显示为 `4K`；其余命中规格保留纯标签并用 ` / ` 分隔。
- 大小四舍五入为约数 GiB，例如 `~35G`。
- 零结果时允许额外显示一行“没有同身份、同范围片源”，但真实错误详情只写日志
  与内部状态，不展开到 Telegram 消息。

规格字段排在标题前，标题最后截断。每行设定长度预算，并对整条消息执行 4096
字符上限保护；即使标题极长也必须保留 12 行结果。

## 失败与兼容

- 全部 Indexer 失败且无合格结果：关闭计划，Telegram 只显示零结果和异常数量，
  真实来源与错误保留在日志和内部状态。
- 部分 Indexer 失败：保留成功结果，Telegram 只显示异常数量。
- Indexer 列表读取失败：执行旧聚合查询并保留结构化错误。
- 旧的注入式测试和离线调用仍可只提供二参数 `release_search`，走聚合路径。

## 验证

- Adapter：Indexer 过滤、单 Indexer 参数、75 秒超时、真实错误类型。
- 报告：两行摘要、一片源一行、纯规格标签、约数大小、12 项按钮稳定 ID、消息
  不超过 4096。
- Service：并发快结果先出现、慢/失败 Indexer 不阻塞、增量重排、旧按钮稳定
  选择、选择后冻结并取消剩余任务、全部失败行为、聚合兼容。
- 版本：manifest、pyproject、README 与两处版本断言同步为 `1.0.5`。
