# search 7 月 29 日日志后续事项

更新时间：2026-07-30

状态：`recorded / deferred`

本文只记录从 7 月 29 日搜索测试日志确认的后续事项，不授权在当前任务中
修改业务逻辑。手动 `/m` 属于 download 的独立入口，不纳入 search 后续
事项。

## P1：Prowlarr Query 与 Indexer 扇出过大

7 月 29 日两次片源搜索共形成 8 个 Query，每个 Query 都请求 18 个
Indexer：

- Harry Potter：5 × 18；
- Rick and Morty：3 × 18；
- 总计 144 个 Query/Indexer 请求；
- 126 次正常返回，18 次失败；
- BigFANGroup：8 次 75 秒超时；
- Magnet Cat：7 次 HTTP 400；
- Internet Archive：3 次 HTTP 400；
- `release_gate` 在增量过程中执行 144 次。

后续评审方向：

- 按 Query 价值分层执行，优先官方英文名、原名和用户确认的查询；
- 达到足够结果数量或质量后停止低价值别名；
- 对持续超时或确定不支持当前查询的 Indexer 做请求内熔断；
- 对增量 `release_gate` 计算做批量或节流，避免每个请求完成后全量重算。

## P1：metadata probe 未约束初始候选规划

`media.search.resolve_metadata` 已收到文件探测得到的 season、episode 和
content shape，但当前先用纯文本 query 完成候选规划，之后才应用 probe。
这会扩大无交互候选集合，并可能把本可由 S09E10 等结构化信息排除的候选
带入 `metadata_ambiguous`。

后续评审方向：

- 将 probe 转换为候选规划的结构化 intent，而不是只在候选选定后应用；
- probe 只能缩小 scope，不得凭文件名改写作品身份；
- 如果上游已有 confirmed search contract，应优先复用合同，不重新规划。

## P2：source orchestration 配置与实际入口不一致

默认配置保留 `ai.source_orchestration.enable`，但正常 `SearchFeature`
调用始终传入 candidate editor；planner 会先进入 anchored candidate
路径，后面的 source gateway orchestration 分支不会执行。

后续需要二选一：

1. 删除未生效的配置和死路径，保持当前 candidate-first 架构；
2. 重新设计 source orchestration，使其遵守“先展示候选、选中后只验证
   单个候选”的 search 1.2.0 合同。

在完成独立设计和测试计划前，不实施其中任何方案。
