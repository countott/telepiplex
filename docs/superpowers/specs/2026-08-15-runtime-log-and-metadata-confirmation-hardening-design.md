# 运行时日志与元数据确认链路彻底加固设计

**状态：** 已全部落地，并通过专项压力、全仓回归和 Feature 制品验证
**目标版本：** Host `v3.5.4-host`、SDK `1.3.2`、download `1.0.15`、search `1.11.2`、rename `1.5.3`
**范围：** Feature 日志传输、Host 日志采集、search 候选确认、rename 回调与取消、storage 批量读取、响应体去重和完整压力验证。

## 1. 事实边界

两份 2026-08-15 运行日志证明故障由两条链路叠加形成：

1. search 成功结果超过 asyncio 子进程默认单行读取上限，Host 的 stdout 采集任务退出；Feature 随后继续同步写日志，管道填满后阻塞整个 Feature 事件循环，连 `operation.control` 都无法处理。
2. rename 的 Telegram 候选回调在 30 秒回调期限内同步等待最长 120 秒的 `confirm_metadata`；search 又在确认阶段重新执行完整 plan，业务失败被外层 `deadline_exceeded` 覆盖。

因此网络超时不是修复边界。必须同时消除日志反压、确认重搜、回调长等待和逐文件 RPC 放大。

## 2. 方案比较

### 方案 A：分层有界与可恢复链路（采用）

- SDK 在所有诊断入口对大型 `params/result` 做结构化摘要，并在 stdout 写出前施加最终字节上限。
- Host 改用分块读取而非 `readline()`，即使旧 Feature 输出超大单行也持续排空；采集任务异常时立即终止并按既有监督策略恢复子进程。
- search 把待确认候选写入有 TTL 的持久 resolution store；`confirm_metadata` 只能消费 `resolution_id + candidate_ref`，不得重新 plan，并缓存成功结果用于幂等重放。
- rename 候选回调只做鉴权、状态迁移和后台任务调度，立即返回；长 RPC、身份发布和后续整理都在可取消的运行时任务中完成。
- download 提供有界 `get_file_info_batch`；rename 的 StorageProxy 对只读文件信息做操作内缓存，任何变更后清空，减少跨 Feature RPC 数量但不跳过变更后的新鲜验证。
- search capability 只返回 canonical contract、命名摘要和展示信息，删除顶层重复 evidence/source_queries，并压缩 contract 内不再消费的大型来源明细。

该方案在不更换 RPC 协议的前提下覆盖生产根因，并保留旧 Feature 输出的兼容读取。

### 方案 B：单独 diagnostics socket

把业务 stdout 与诊断事件改成两个 framed Unix socket。隔离性最好，但需要同时迁移 Host、SDK、现有 Feature 打包与安装协议，升级窗口内仍需兼容 stdout，范围明显超过本次故障。

### 方案 C：只扩大行上限和超时

扩大 `StreamReader.limit` 可以延后部分异常，扩大 Telegram 回调期限可以延后超时，但两者都不能约束无界响应、恢复采集任务或阻止确认重搜；不采用。

## 3. 日志传输设计

诊断日志遵守三层边界：

1. `params/result` 超过 8 KiB 时变为类型、原始字节数、键/元素数量和有限键名组成的摘要；小结果保持现有可读结构。
2. 完整诊断事件写 stdout 前限制在 32 KiB。仍然超限时保留身份、事件名、状态、耗时和传输字段，把 facts 替换为截断摘要。
3. Host 用固定块读取并自行切分换行，最多解析 1 MiB 单行；更大行只记录字节数和 SHA-256，不持久化原始敏感内容。

Host 对每个 stdout/stderr 采集任务注册存活回调。若子进程仍在运行而采集任务异常退出，Host 记录安全错误并终止该子进程，让现有 supervisor 重启和 operation reconciliation 生效，绝不留下“进程健康但无人读管道”的状态。

## 4. search 确认设计

`resolve_metadata` 仍负责一次完整 plan。出现多候选时保存以下冻结记录：

- `resolution_id`
- 原始 query 和 probe
- 最多五个完整冻结候选
- plan 的 entry kind 和必要来源上下文
- 创建、过期时间
- 可选的已确认 candidate ref 和最终结果

store 使用 search state 目录中的 SQLite，默认 TTL 24 小时、最多保留 256 条；写入和读取时清理过期记录。成功确认不立即删除，而是缓存最终结果到过期时间，使 rename 在响应丢失或重启后可以按相同 idempotency key 重放。

`confirm_metadata` 必须先查 store，再以 `candidate_ref` 唯一选择候选。缺少、过期或不匹配分别返回稳定 reason code，不调用 `plan_builder`。确认使用冻结 query/probe，调用者提交的同名文本不能改变已选身份。

## 5. rename 回调、错误与取消设计

候选点击后同步阶段只完成：

1. 验证 callback、job、owner 和 inventory session；
2. 把 durable job 改为 `resolving_metadata`，保存 `resolution_id` 与选择；
3. 把 operation 更新为运行中的 `metadata_resolution`；
4. 通过 FeatureRuntime 生成 `rename-metadata-<job>` 后台任务并立即关闭交互。

后台任务调用 search、发布身份、把 job 更新为 `ready_metadata`，再恢复普通或 inventory 整理。瞬时错误恢复到 `awaiting_metadata` 并重新展示同一候选；resolution 过期等确定性错误进入失败并明确要求重新扫描。取消会设置既有 cancel event 并取消尚未进入线程执行的后台任务。

Feature 重启时，普通下载任务的 `resolving_metadata` 属于 resumable 状态；依赖 search 的持久幂等结果继续完成，而不是重新搜索。存量批次仍沿用既有安全边界：批次会明确失败并要求重新扫描，不在丢失内存 session 后猜测恢复，也不会开始文件变更。

## 6. storage 批量与验证设计

`get_file_info_batch(paths)` 最多接受 128 个唯一规范路径，provider 内部仍使用现有安全单文件 API，但跨 Feature 只产生一次 RPC。rename StorageProxy 分块请求并缓存结果：

- 规划阶段和执行前验证可复用同一只读快照；
- `rename/move/copy/delete/create` 返回后清空缓存；
- 移动、重命名后的目标验证始终重新读取；
- provider 不支持 batch 时逐条兼容回退。

这项优化只减少跨进程往返和日志量，不放宽文件身份、目标冲突、源删除或空目录清理门槛。

自动下载任务只有在源目录已删除或按新鲜目录快照确认无需删除时才记为完整成功。删除失败、查询失败或源目录仍非空都会产生警告里程碑，并把 durable rename job 收口为失败；已验证的目标文件仍发布 `media.organized`，避免因为清理问题让已移动媒体无法进入 Plex。手工 inventory 明确保护的扫描根目录不属于自动清理失败。

## 7. 响应体去重

search resolved response 不再顶层复制 `media_metadata.evidence` 和内部 `source_queries`。canonical evidence 中保留来源链接、字段来源、provider 状态、决策和 series inventory；删除与 `items` 完全重复的 `tvdb_inventory`，把未被后续代码消费的 cast、crew、backdrop 和 episode inventory 明细改成计数摘要。

## 8. 验收与压力门禁

- 生成超过 64 KiB 的真实 Feature 诊断事件，确认 Host 持续读取、后续 health/control RPC 可在 1 秒内完成。
- 连续输出至少 2,000 个大型结果事件，确认无采集任务退出、无管道反压、事件循环 ticker 持续前进。
- resolve 多候选后 confirm 100 次，`plan_builder` 调用总数仍为 1；重建 SearchFeature 后能从持久 store 返回同一结果。
- 阻塞 confirm RPC 时，rename callback 在 1 秒内返回，cancel 在 1 秒内进入 cancelling/cancelled，且不发生后续文件变更。
- 65 文件 Veep fixture 使用有界批次读取，RPC 数量相较逐文件基线显著下降，所有变更后验证仍为新鲜读取。
- resolved response 不包含顶层 `evidence/source_queries`，大型剧集结果低于诊断传输上限或被摘要，canonical contract 仍通过严格校验。
- Core 与 download/search/rename/sync/caption 全量测试、专项压力测试、Feature 包构建与 `unzip -t` 全部通过。

Mac 本地不执行 Git、发布或标签操作；完成后只通过 Syncthing 交付 Unraid。
