# telepiplex 单任务 Telegram 关键路径优化设计

## 目标

在用户严格一次只执行一个媒体任务的前提下，缩短从 Telegram 点击到 Feature 启动、以及业务终态到 Telegram 最终可见状态的时间。优化不依赖并发执行多个任务，不改变 search → download → rename → sync 的业务依赖顺序。

## 已验证基线

10 次串行完整链路、并发 1、每次 Telegram API 固定 50 ms 时：

- 前台完成平均 1.418 s，p95 1.451 s；
- Telegram API 延迟归零后平均 0.981 s，p95 1.019 s；
- callback 到 Feature RPC 平均 104.8 ms；
- EventDispatcher 排队平均 4.2 ms，p95 7.3 ms；
- 每个任务约产生 12 次 Telegram API 调用。

因此当前改动只处理 Telegram 前台关键路径。PTB update 并发和 EventDispatcher 跨 operation 并发不在本设计范围内。

## 设计

### 1. callback feedback 与 Feature RPC 解耦

`operation_gate` 仍先执行持久化且原子的 callback claim，防止重复点击。claim 成功后，不再串行等待 `answerCallbackQuery` 和 busy message edit；Host 创建一个受跟踪的 callback feedback task，然后立即允许 `dynamic_callback_gateway` 发起 Feature RPC。

feedback task 保存精确的 `operation_id`、`segment_id`、`generation`、`callback_generation` 和 `message_id`。busy 写入完成后，它检查该 segment 的持久化状态：如果 callback 已释放、segment 已 sealing/sealed、或 operation 已推进，则重新投影该 segment 的最新持久化内容，确保迟到的 busy 写入不能成为最终界面。

feedback task 不持有 operation render lock 等待 Telegram 网络；只在最终校正时获取该锁。因此 Feature、milestone 和 terminal 投影不会被一个慢 busy API 调用阻塞。任务注册表支持 drain，并纳入 Host 关闭和压测验收。

### 2. 单 operation latest-projection 合并

`OperationReportSink` 继续以 operation 为键，只保留最新 revision。worker 在第一次 Telegram 写入前使用一个很短的合并窗口，让紧邻的 report 与 seal 汇合。

seal 无论 worker 是否已存在，都写入最新 pending 目标。若一个 sealing segment 的最新业务 revision 尚未渲染，renderer 使用一次 text/caption/media edit 同时写入最终内容并清空 reply markup，然后直接完成 durable seal；不再追加一次 `editMessageReplyMarkup`。若内容已经渲染，只执行必要的按钮清理。

不丢弃以下状态：首次 segment 创建、seal、handoff 里程碑、terminal。只合并尚未发出的同 operation 中间 revision。

### 3. 统一 Telegram 投影生命周期

保留 operation report 与 durable milestone 各自的持久化和重试语义；它们不能合并为同一种队列项。新增一个 Host 级投影生命周期入口，统一 attach、start 和 drain 两个 sink，避免启动、恢复和关闭代码分别管理两个 Telegram writer。

这项结构调整本身不计入性能收益，但必须服务于 callback feedback drain 和 report coalescing，不能改变 milestone 的 exactly-once、目标记录、unknown 恢复或三次尝试规则。

### 4. 性能与正确性合同

压测工具和测试必须暴露并验证：

- callback 到 Feature RPC 延迟；
- 每任务 Telegram API 调用数；
- callback feedback drain；
- late busy 最终不会覆盖 durable projection；
- seal 与最新 report 合并时只执行一次 Telegram edit；
- terminal、milestone、segment seal、重复 callback 语义不变；
- task 与 FD 最终增量为 0。

50 ms Telegram 模型下，验收目标是 callback 到 Feature RPC p95 小于 30 ms、每任务 Telegram API 调用不超过 9 次、前台完成 p95 至少下降 15%。调用预算以当前真实 segment UX 为边界，不通过删除必要的身份、下载、重命名或 terminal 消息达成。

## 版本与发布边界

改动只影响 telepiplex Host，Host patch 版本从 `v3.6.5-host` 提升到 `v3.6.6-host`。Feature manifest、Feature `pyproject.toml` 和 SDK 版本不变。

所有源码和测试只在 Mac `/Users/young/Documents/telepiplex` 完成，不执行 Git。验证后通过 Syncthing 交给 Unraid，由用户决定 Git 与发布动作。
