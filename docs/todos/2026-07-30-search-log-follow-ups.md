# search 7 月 29 日日志后续事项

更新时间：2026-08-03

状态：`resolved / closed`

本文记录从 7 月 29 日搜索测试日志确认的后续事项及最终处理结论。手动
`/m` 属于 download 的独立入口，不纳入 search 后续事项。

## 已关闭：Prowlarr Query 与 Indexer 扇出过大

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

该统计对应旧的多别名查询流程。search 现已只从确认后的 canonical identity
生成一个范围明确的 query，因此旧的 Query 扇出修复项已经过期并关闭。
Indexer 自身的超时或 HTTP 400 仍属于运行质量观测，但不再作为本轮 search
业务流程改动。

## 已完成：metadata probe 在歧义前约束候选

旧实现虽然收到了文件探测得到的 season、episode 和 content shape，却先用
纯文本 query 完成候选规划，之后才应用 probe，导致本可排除的候选进入
`metadata_ambiguous`。

search 1.4.0 在 `metadata_ambiguous` 判断前把 probe 转换为电影/剧集类型
约束，只保留匹配类型的候选；唯一剧集候选确定后才应用 season/episode
scope。probe 不参与标题、年份或稳定 ID 判断，也不能在多个同类型作品之间
选择；约束后为零则返回 `metadata_unresolved`。上游已有 confirmed
`media_metadata` 时，rename 继续直接复用合同，不调用此 capability。

## 已完成：移除未生效的 source orchestration

旧默认配置曾保留 `ai.source_orchestration.enable`，但正常 `SearchFeature`
并不进入该 source gateway orchestration 分支。

search 1.4.0 采用原方案 1：删除未生效的工具编排配置、运行时分支、工具
网关、定向 handler 和专属测试，保留豆瓣首次发现、统一 AI 候选清洗以及
确认后的 Wikipedia/TVDB 确定性增强。原先误挂在旧配置下但被现行 AI 共用的
`thinking_mode` 已提升为 `ai.thinking_mode`，默认行为保持 `enabled`。
