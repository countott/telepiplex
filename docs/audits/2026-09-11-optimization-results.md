# telepiplex 第二轮优化成果

日期：2026-09-11。用户要求基于当前版本再发起一轮优化。本轮在 Mac 本地实施；未运行真实 Git、连接本项目 GitHub、发布或修改远端媒体。

## 当前基础与收敛范围

读取现役代码确认 Host3.6.11、search2.2.0，保留9月9日的按钮生命周期修复及 `/pr` 原文检索。此前9月5日的测试结果不是本轮基线：本轮重跑Search得到650 passed /2 skipped /121 subtests；Host基线680 passed /4 failed /1 skipped /260 subtests。4项失败是旧版本表和fake发布标签预期滞后，已按实际交付版本精确修正并通过全套回归。

本轮仅实施三个有受控证据的改动包：

| 包 | 已复现的成本 | 修改目标 | 必须保留 |
|---|---|---|---|
| Host历史任务查询 | `active` / `active_records` 参数化状态条件没有匹配现有partial index，历史终态也被扫描 | 使用代码内可信状态集合的统一SQL谓词，复用已有索引 | owner仍参数化；活动唯一性、排序、锁、消息归属与清理保护 |
| Search海报等待 | TMDB最高优先级海报已成功，仍等慢的豆瓣/TVDB；父取消时本地consumer未收回 | 按优先级结算，赢家确定就停止无用等待；所有退出路径回收本方法任务 | 高优先级在途时不得让低源抢先；原总超时、身份核验和SourceScheduler共享读取 |
| Rename快照恢复 | 缓存命中读盘两次、完整校验三次；同步SQLite/全树验证阻塞事件循环 | 单次已验证读取，存储工作下放线程；首轮保留持久提交后重读 | 计数/拓扑/摘要/错引用拒绝；取消后无ack或文件改动；两次远端复验不变 |

SDK提供方已经逐页按键读取，没有发现每页重复整树扫描。本轮不新增SDK API/缓存/索引/数据库迁移，不加快照GC，不删文件修改前后校验，也不改变自动流程止于rename的产品范围。

## 实施与正式验证

三个优化包均已落到源码，先用新增测试证明旧代码失败，再验证正式实现。三个包均通过独立规格及质量审查，最终跨包源码、测试与版本审查也已通过，无需修改项；过程与证据在执行记录中列出。

| 正式实现证据 | 优化前 | 优化后 | 解释 |
|---|---:|---:|---|
| 10k 历史任务，`active()` 真实 SQLite VM 步数 | 34,653 | 35 | 同一回归数据，owner 输入仍绑定参数 |
| 10k 历史任务，`active_records()` VM 步数 | 70,256 | 51 | 排序与全部活动状态不变 |
| 单候选 TMDB 先成功，补全等待中位数 | 121.471 ms | 12.347 ms | 受控延迟，来源调用均为 3 次 |
| 五候选 TMDB 先成功，补全等待中位数 | 122.459 ms | 12.556 ms | 来源调用均为 15 次 |
| 20k 节点首次快照接收，完整校验次数 | 4 | 2 | 保留写前校验、提交后独立重读校验 |
| 20k 节点快照重放，完整读盘 / 校验次数 | 2 / 3 | 1 / 1 | 损坏、缺页、错引用仍拒绝 |
| 20k 节点快照重放，中位总时长 | 452.07 ms | 163.88 ms | 本机真实 SQLite 与分页数据 |
| 上述重放各轮最大循环延迟的中位数 | 447.36 ms | 33.83 ms | 三轮统计，工作线程仍有 GIL 竞争 |

Host 正式查询基准每档预热10次、测量50次；1k/10k/100k终态历史下，`active()` VM 为47/35/35，批量活动查询为54/51/51。100k 下50条 fake Telegram 清理完成50条、API调用50次，仍逐条执行活动任务保护。

Search 用冻结基线 helper 与正式 helper 同进程做9情景×30次配对，共270对；全部候选字典、身份、顺序、赢家和调用多重集合相等。低优先级先返回而TMDB稍后成功时，两版仍约42 ms；高源超预算的回退仍约42 ms，未提前绕过原优先级或重置总超时。父取消后本方法的consumer全部收回，已有HTTPS海报不发新请求。SourceScheduler的共享请求仍按自身超时收尾，可能继续占用并发槽；这些数据只证明候选补全等待下降，不代表真实网络总耗时或后续确认也按相同比例加速。

快照基准分别对10k/20k节点做3次无追踪计时，另用独立追踪运行统计读取和校验；事件循环心跳间隔2 ms。循环延迟字段取三轮各自最大延迟的中位数，并非所有样本的最大值。20k首次接收中位时长843.02→543.12 ms，各轮最大循环延迟的中位数610.86→38.91 ms；正式实现冷读三轮峰值为40.02/33.49/38.91 ms，重放为44.67/33.83/31.00 ms，本轮观测到的最高值是44.67 ms。提供方仍逐页按键查询，20k读取40页；消费者重放不再请求提供方页面。取消检查留在事件循环；已开始的本地SQLite写线程可以收尾，取消后不会继续ack或媒体变更。

另修复一处测试隔离缺陷：`RawSearchTest` 在清理阶段恢复进入测试前的全局配置。以 `/pr` 测试先于身份确认测试运行，修复前21 passed /1 teardown error，修复后21 passed /8 subtests，外网保护全程开启。没有通过重排用例掩盖问题，也未修改产品配置行为。

### 最终默认测试

| 范围 | passed | skipped | subtests passed | 实际耗时 |
|---|---:|---:|---:|---:|
| Host / SDK集成 | 686 | 1 | 260 | 131.73 s |
| search | 661 | 2 | 121 | 14.48 s |
| download | 174 | 0 | 33 | 3.13 s |
| rename | 385 | 0 | 24 | 9.88 s |
| sync | 157 | 0 | 80 | 7.67 s |
| caption | 1 | 0 | 0 | 0.06 s |
| 合计 | **2,064** | **3** | **518** | — |

六套均退出0，无失败或测试保护错误。3项跳过分别是2项需主动启用的在线来源测试、1项需传入外部发布制品集合的矩阵测试；不视为已通过。上述合计不重复计入实施、审查和定向红绿测试。

实际运行命令与AGENTS.md一致，另加载临时外网保护插件：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-20260911:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider -p local_network_audit --tb=short tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-20260911:src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider -p local_network_audit --tb=short tests
  )
done
```

各套按文件所有权冻结后分别运行，上述循环是等价复现形式。临时插件位于 `/tmp/telepiplex-20260911/local_network_audit.py`，复用项目现有 `ExternalNetworkGuard` 并在测试结束保留拦截记录，允许真实Unix RPC/loopback；临时材料清理后可按AGENTS.md原命令复测。测试中的发布脚本使用临时fake Git记录器，没有运行真实Git或连接仓库。

### 业务与压力

真实Feature/RPC、实际10k文件执行与重放包含在本轮验证中；快照改动后单独的RPC+10k组合7 passed，最终根全套再次覆盖。Search 新增真实 `command` 候选首报后立即确认验证，保留媒体v2身份与范围。测试通过外部来源和媒体存储替身运行，没有向真实Telegram、115或Plex发起操作。

| 压力用例 | 实际结果 |
|---|---|
| 200条流程，32并发，注入10次milestone中断 | 200完成 /0失败，10次全部恢复，800重复milestone幂等；唯一终态owner为rename |
| Telegram真实Application队列入口，12条流程，4并发，每条3次点击，API延迟25 ms、忙提示500 ms | 12完成 /0失败，拒绝24次重复callback，download/rename副作用各12次，36消息段全部持久封存 |

实际使用临时网络保护runner执行以下脚本参数，原始输出位于 `final-pressure-operation.log` 和 `final-pressure-telegram.json`：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  /tmp/telepiplex-20260911/run_guarded_pressure.py \
  tools/pressure_operation_pipeline.py --pipelines 200 --concurrency 32 --milestone-faults 10
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  /tmp/telepiplex-20260911/run_guarded_pressure.py \
  tools/pressure_telegram_pipeline.py --pipelines 12 --concurrency 4 \
  --telegram-latency-ms 25 --busy-latency-ms 500 --duplicate-clicks 3 \
  --frontend-mode queue --timeout-seconds 60 \
  --output /tmp/telepiplex-20260911/final-pressure-telegram.json
```

该压力下回调确认P95为2,839.832 ms，首候选P95为2,934.376 ms；默认串行更新队列仍会受到人为500 ms忙提示延迟影响。本轮没有更改队列并发、交互归属或消息生命周期，不能把“无业务失败”写成“交互时延已全部解决”。

### 逐文件交付

共新增5个、修改18个文件，无删除或重命名。以下路径相对项目根目录：

| 操作 | 文件 | 用途 |
|---|---|---|
| 修改 | `app/115bot.py` | Host版本3.6.12 |
| 修改 | `app/runtime/interaction_coordinator.py` | 复用已有活动任务partial index |
| 新增 | `tests/test_active_query_cost.py` | 真实SQLite语义与历史增长成本回归 |
| 修改 | `tests/test_bot_runtime_startup.py` | Host版本合同 |
| 修改 | `tests/test_technical_identity_migration.py` | Search/Rename当前版本身份合同 |
| 修改 | `tests/test_unraid_publish_script.py` | fake发布版本与标签预期对齐 |
| 修改 | `features/search/src/telepiplex_search/service.py` | 优先级确定后结束海报等待，收回consumer |
| 新增 | `features/search/tests/test_candidate_poster_waiting.py` | 11项等待、取消、共享任务和立即确认回归 |
| 修改 | `features/search/tests/test_raw_search.py` | 恢复全局测试配置，消除顺序污染 |
| 修改 | `features/search/tests/test_config_schema_contract.py` | Search版本合同 |
| 修改 | `features/search/tests/test_feature_service.py` | Search版本合同 |
| 修改 | `features/search/manifest.yaml` | Search版本2.2.1 |
| 修改 | `features/search/pyproject.toml` | Search包版本2.2.1 |
| 修改 | `features/search/README.md` | 当前版本与候选海报等待边界 |
| 修改 | `features/rename/src/telepiplex_rename/snapshot_reader.py` | 本地存储下放线程，去重复读与重复完整校验 |
| 修改 | `features/rename/tests/test_snapshot_reader.py` | 读取计数、完整性、响应与取消回归 |
| 修改 | `features/rename/tests/test_feature_processor.py` | Rename版本与包名合同 |
| 修改 | `features/rename/manifest.yaml` | Rename版本2.1.1 |
| 修改 | `features/rename/pyproject.toml` | Rename包版本2.1.1，SDK依赖不变 |
| 修改 | `features/rename/README.md` | 当前版本与快照线程、恢复边界 |
| 新增 | `docs/superpowers/plans/2026-09-11-current-performance-round.md` | 本轮分包计划与验收条件 |
| 新增 | `docs/audits/2026-09-11-optimization-ledger.md` | 所有权、红绿证据、审查与执行记录 |
| 新增 | `docs/audits/2026-09-11-optimization-results.md` | 本报告、实际验证、逐文件用途与迭代节奏 |

本地审计与原始数据：`/tmp/telepiplex-20260911/host-audit.md`、`search-audit.md`、`snapshot-audit.md`，同目录保留受控脚本、JSON、实施报告和无Git diff。源码基线为同目录的 `baseline/files` 与SHA-256清单。

## 分批生效节奏

本轮交付Host3.6.12、search2.2.1、rename2.1.1；SDK2.1.0、download2.1.0、sync2.0.1和caption0.1.4保持不变。协议、配置schema和快照默认关闭状态不变。

1. Syncthing显示 `Up to Date / 最新` 后，由用户在 `/mnt/user/archives/life hacker/telepiplex` 接续检查、发布。先更新Host，观察历史任务较多时启动、任务查询及旧消息清理；旧卡片不得影响当前任务。
2. 更新Search，以不同海报来源速度、无海报、取消、确认和 `/pr` 检查候选及片源选择。每批先覆盖至少10个代表性任务或受控用例，再进入常规使用；数量是观察门槛，不是穷尽证明。
3. 更新Rename，先小树再已有分页任务，检查完整计数、取消、损坏副本和重启重放。分页协议未改，仍需原来支持两端的版本组合；不开启新配置。
4. 任一批出现错误终态、越权旧按钮、重复文件变更或身份不符，停止该组件后续更新并保留同一会话日志。线上吞吐与网络延迟另记，不能从本机替身实验直接推算。

后续一轮优先收集同一会话的队列等待、callback确认和忙提示耗时，判断真实使用中是否重现本轮受控压力的长尾；重现后再以保持owner/顺序/取消为验收条件拆分忙提示投递。其次观察低优先级海报共享请求对确认阶段并发槽的占用；有实际等待证据后才调整调度。20k快照的各轮最大循环延迟中位数仍约34–39 ms，本轮单次峰值44.67 ms；只有更大树或并发恢复持续影响交互时，再评估分块计算或进程隔离。这些是后续观察与实施门槛，本轮未更改上述三处机制。

详细实施要求见[本轮计划](../superpowers/plans/2026-09-11-current-performance-round.md)，过程见[执行记录](2026-09-11-optimization-ledger.md)。
