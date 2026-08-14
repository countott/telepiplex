# telepiplex 双格式诊断日志设计

## 目标

telepiplex 的每次 Host 启动创建一个独立日志会话目录。该目录同时保存面向人的中文日志、面向机器的 JSONL 日志，以及按 Feature 分类的同源日志。两种格式来自同一个规范化诊断事件，不能各自拼装或产生语义偏差。

## 会话目录

生产环境使用以下结构：

```text
/config/logs/sessions/20260814T231530+0800-a83f2c/
  telepiplex.human.log
  telepiplex.machine.jsonl
  feature-search.human.log
  feature-search.machine.jsonl
  feature-download.human.log
  feature-download.machine.jsonl
  feature-rename.human.log
  feature-rename.machine.jsonl
  feature-sync.human.log
  feature-sync.machine.jsonl
  feature-caption.human.log
  feature-caption.machine.jsonl
```

会话目录名由本地启动时间和随机 `session_id` 组成，不复用、不追加上次启动的文件。Feature 重启仍写入当前 Host 会话目录，并通过 `instance_id`、PID 和 restart count 区分进程实例。

会话目录保留最近 30 次启动且最长 30 天，任一条件命中即清理整个旧会话目录。既有固定文件 `telepiplex.log` 与 Feature `runtime.log` 不再写入，但不在升级时自动删除。

## 单一事实源与双格式输出

所有标准 Logger、Feature stdout/stderr、RPC、Telegram 前台动作和业务边界先转换成一个 `DiagnosticEvent`。事件在完成递归脱敏后，才同时进入：

1. 全局 human 与 machine 文件；
2. 若属于 Feature，再进入同会话目录下该 Feature 的 human 与 machine 文件；
3. 全局 human 渲染结果进入 Docker stdout。

因此同一事件在两个格式和两个分类视图中共享 `event_id`，字段值不能漂移。

## 机器日志契约

机器日志为 UTF-8 JSONL，每行一个 JSON 对象，固定 `schema_version=1.0`。顶层字段稳定且始终存在，不适用值使用 `null` 或空对象，不让消费者猜测字段缺失含义：

- `event_id`、producer sequence、Host ingest sequence；
- UTC、本地时间、时区、Unix 纳秒和单调时钟；
- level、event name、message、component、stage、status、duration；
- session、trace、span、parent span、operation、request、incident；
- Host/Feature 版本、plugin、instance、PID、线程、async task；
- 结构化 input、output、state transition、retry、transport 和 user surface；
- error code、type、message、retryable、stack 和 cause chain；
- privacy.redacted_paths、privacy.redaction_count 和完整性信息。

事件名和枚举使用稳定英文技术身份。自由文本只承载人类说明，不能代替 code、status 或 stage。超长的已脱敏文本按有序 `payload.chunk` 事件拆分，保留引用、长度、SHA-256 和总块数，不静默截断。Feature 生产序号与 Host 汇入序号可用于检测丢失或乱序。

随 SDK 发布 `diagnostic-event-v1.schema.json`，自动测试逐行验证实际日志。

## 人类日志契约

人类日志使用中文叙事块。首行说明何时、哪个组件、发生了什么；后续按业务含义展示链路、输入、结果、状态变化、耗时、重试、用户实际看到的脱敏文案、异常调用路径和下一步。所有已有事实都必须呈现，只允许改变组织和标签，不允许为了简短而丢弃 populated fields。

人类日志避免 JSON、固定字段空值和连续 `key=value`。每个事件保留短 `event_id`，错误保留完整 `incident_id`，用于和 JSONL 精确互查。

## 链路上下文

每个 Telegram Update 建立 `trace_id`。Host 与 Feature RPC envelope 携带诊断上下文并建立父子 span；`operation_id` 继续作为 search、download、rename、sync 的业务主链；每次 RPC 使用独立 `request_id`。未捕获异常创建 `incident_id`，后台记录脱敏堆栈，Telegram 前台显示同一个问题编号。

Feature SDK 通过带版本前缀的单行 JSON transport 把标准 logging record 交给 Host。Host 解析后写入全局和 Feature 分类日志。无法解析的 print、stdout 和 stderr 也包装为 `feature.process_output`，不会丢弃。

## 脱敏与故障退化

沿用现有 Token、API Key、Authorization、Cookie、密码、URL、磁力链接和敏感键规则，并扩展到嵌套对象、异常消息、异常链和 stack。脱敏发生在持久化与 stdout 之前。机器日志记录哪些 JSON path 被脱敏，但永不记录原值。

日志目录或文件写入失败不得终止业务；诊断系统退化到 stderr，并产生稳定错误码。ERROR 与 CRITICAL 立即 flush。日志处理器内部异常不能递归进入自身。

## 接入范围

- Host Logger 与启动、关闭、配置、安全快照；
- Telegram Update、发送、编辑、删除、callback answer 和未捕获异常；
- Host→Feature、Feature→Host、Feature→Feature RPC；
- Feature 启停、重启、隔离与 stdout/stderr；
- operation、milestone、revision、owner 与阶段转换；
- 现有 search/download/rename/sync/caption 日志及外部源调用结果。

## 版本

- Host `v3.5.0-host`
- SDK `1.3.0`
- search `1.9.7`
- download `1.0.12`
- rename `1.4.5`
- sync `1.1.1`
- caption `0.1.3`

## 验收

自动测试必须证明：每次启动得到不同目录；两种格式位于同一目录；Feature 分类文件位于该目录；30次/30天按目录清理；机器日志通过 schema；同事件跨文件 event ID 相同；敏感值在所有出口消失；异常前后台 incident ID 相同；RPC 上下文跨进程连续；Feature 非标准输出被收录；完整测试与带故障注入的全链路压测无业务失败。
