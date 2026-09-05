# telepiplex 业务流迭代成果

日期：2026-09-05。依据用户“执行落地”，完成可在 Mac 实施的修复、协议扩展与性能优化。本文区分本地验证与实际部署；没有操作 Unraid、Git、GitHub，也没有对真实 115/Plex 媒体执行修改。

## 分轮落地

| 批次 | 结果 | 验收重点 |
|---|---|---|
| 1 交互可靠性 | sync 按钮统一 `sync:`；旧 `plex:*` 按钮只反馈过期；取消检查覆盖每个外部写入边界和等待选择；Host 编辑期间进入封口时补清原消息按钮 | 取消不触发下一个写入、不虚报回滚；补清失败保留可重试状态 |
| 2 文件完整性 | 严格完整扫描取代截断树；清理前验证节点/深度/事实/整帧容量；清理后独立复验；新增双方持久化分页快照 | 默认 1,000 节点超限明确失败；显式开启分页后上限 20,000；缺页、摘要/身份错误在文件修改前拒绝 |
| 3 真实业务验收 | audit 经真实 SearchFeature command/callback 输出 v2；新增实际 Host/Feature Unix RPC 流；修复同一 Wikipedia 作品的明确季集选择误拒绝，并限制为同 QID 证据 | 区分业务成功、合理拒绝、上游失败和未执行；片源、身份、范围、重复投递与重启均有断言 |
| 4 搜索等待 | Wikipedia 首次成功后并行 Wikidata 标题读取；locale 整体 2 秒等待预算、事务副本与 revision 校验 | 候选身份/顺序冻结；迟到 locale 不污染已确认下载合同；保留底层请求自身超时 |
| 5 重复 I/O | sync 单 Job 作品缓存、批量路径索引、可取消退避；Host 正常调用与竞态补偿分别计数 | 作品读减少，精确路径不再逐目标扫全库；必要的远端新鲜度验证仍保留 |
| 6 边界与版本 | 实际 runtime 固定无自动 `media.organized` 订阅，管理 capability 仅本地读 Job；相关版本/依赖对齐 | 自动流程结束于 rename；旧任务和 MCP 恢复路径保留；caption 仍占位 |

严格扫描器与分页合同统一放入 SDK，避免 download/rename 各维护一套规则。4C 没有发现同一任务内完全重复的根读取，因此未新增常驻根缓存。5B 缺少可靠远端变更令牌，保留清理后的第二次整树读取。这两项按方案条件结案，不用减少验证换取调用量下降。

## 性能与正确性证据

| 场景 | 修改前 → 修改后 | 条件与代价 |
|---|---|---|
| 中文根发现 | 中位 129.09 → 97.56 ms，下降 24.43% | 同机受控延迟、前后各 30 次；完整候选与请求集合一致，p95 下降 |
| 英文 fallback 根发现 | 中位 162.87 → 99.67 ms，下降 38.80% | 前后各 30 次；不是线上延迟承诺 |
| locale | 整体等待最多 2 秒 | 超时放弃这一轮补丁；已有独立海报阶段的 12 秒预算仍存在，不能声称全流程 2 秒 |
| 同一作品 24 集 | TMDB details、show 各 24 → 各 1 次 | 仅 Job 内复用；下个 Job 重新取，音轨/字幕状态不长缓存 |
| 5,000 媒体 / 50 精确路径 | 250,000 次逐对比较 → 0；路径归一化 5,050 次 | 索引的路径深度成本仍存在；罕见非绝对路径保留兼容匹配 |
| 300 秒未入库定位 | 61 → 13 次 | 5、10、20、30…秒退避；末段发现新媒体可能增加约 30 秒等待；睡眠切片可取消 |
| Telegram 10 任务 / 50ms | 每任务正常 10 次，原始 11 次 | 每任务另有 1 次实际迟到投影恢复，保留全部调用；10 条 completed，30 段正确封存 |
| 强制编辑在途封口 | 原始 11 = 正常 9 + 两类补偿各 1 | 原消息按钮最终为空；500ms busy 另实测 500.822ms，业务 RPC 1.675ms；task/FD 增量 0 |
| 10,000 文件 / 500 目录快照 | 21 页，分页读取 0 次远端整树扫描 | 最大样本响应 67,372 字节；每页同时限制条数/字节，整帧上限仍为 1 MiB |
| 10,000 文件实际执行与重放 | 首轮 10,000 次移动；重建后 10,000 个已完成原位结果、0 追加移动 | 使用真实文件执行器与持久分页读取、受控文件服务；不代表 115 实机吞吐 |

并行读取有明确取舍：首次中文 Wikipedia 失败仍不启动标题读取；更后阶段失败时可能已有一个逻辑标题请求在途，沿用原请求超时与重试限制，并保留原失败优先级。成功路径 adapter 调用集合不增加，失败路径不能笼统声称完全不增加。辅助线程退出时会等待原本受超时限制的读取结束，因此根来源失败的返回也可能多等这段在途请求；不能宣称强制中止了 I/O。

1,001 节点实际 RPC 场景同时验证失败真实性：媒体已整理，但与媒体无关的空目录按旧规则受到保护，根目录未清空时最终状态为失败；不会把“移动了文件”误报成整体完成。另一个 10,000 文件场景检查目录清理的成功、保留和失败计数守恒。

## 本地验证

最终本地全量 **1,943 passed / 443 subtests / 3 skipped**，无失败。独立分包审查与最终跨模块审查均通过；计数不重复叠加针对性复测。

| 范围 | passed | subtests | skipped | 耗时 |
|---|---:|---:|---:|---:|
| Host / SDK / 组合 | 633 | 225 | 1 | 97.53s |
| download | 174 | 33 | 0 | 2.66s |
| search | 599 | 83 | 2 | 12.77s |
| rename | 379 | 22 | 0 | 7.69s |
| sync | 157 | 80 | 0 | 5.07s |
| caption | 1 | 0 | 0 | 0.04s |

每套测试都在保留违规记录的外网保护下运行；Unix socket/loopback 合同允许，真实外部 provider 测试跳过。Search 全量后仅增强一条 validator 返回值断言，相关 11 测试 / 2 subtests 再次通过（0.29s）；无生产变动。

实际 Feature RPC 定向 3 passed（5.87s）；真实 10k 执行器分页用例 1 passed（3.12s）；Host 版本/SDK builder 定向 43 passed / 1 skipped / 16 subtests（7.18s），这些针对性结果不重复计入最终总数。

组合压力：100 条任务、8 路并发、30 次里程碑故障，100 条 completed、0 failures，30 次全部恢复；重复里程碑 400 次，终态 owner 均为 rename。该工具使用受控业务替身；真实 Feature 状态机由前述独立 RPC 测试覆盖。

Host 全量首轮检出 6 个旧版本断言/发布脚本模拟标签未更新；已将现役 SDK/Feature/Host 版本对齐，保留原断言强度。定向版本合同 37 passed / 24 subtests，发布脚本模拟 5 passed / 3 subtests；后者只使用临时假命令记录器，没有调用真实 Git 或发布。修正后重新运行 Host 全套，633 passed / 1 skipped / 225 subtests（97.53s），没有失败。

命令使用 `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，设置 `PYTHONDONTWRITEBYTECODE=1`，pytest 使用 `-q -p no:cacheprovider`。根目录 `PYTHONPATH=.:sdk/src`，各 Feature 目录 `PYTHONPATH=src:../../sdk/src`；本轮额外通过临时 pytest plugin 对全部测试保留外网违规记录。

复跑项目默认全量与本轮压力场景的命令：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
  )
done
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" tools/pressure_operation_pipeline.py \
  --pipelines 100 --concurrency 8 --milestone-faults 30
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" tools/pressure_telegram_pipeline.py \
  --pipelines 10 --concurrency 1 --frontend-mode queue --telegram-latency-ms 50 \
  --duplicate-clicks 2 --timeout-seconds 60
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

本轮保留外网违规的完整命令是在相应 `PYTHONPATH` 前加 `/tmp/telepiplex-iterations-work:`，pytest 增加 `-p local_network_audit`；日志位于该临时目录的 `final-*.log`。跳过项分别为 2 个显式 live Search 测试，以及未提供实际 `.tpx` 成套产物的 release matrix；不计为通过。

## 当前源码版本与实际更新节奏

| 组件 | 原版本 → 当前源码 | 说明 |
|---|---|---|
| Host | 3.6.8 → 3.6.9 | Host API 1.7 不变 |
| SDK | 2.0.0 → 2.1.0 | 新增共享完整性/快照合同 |
| download | 2.0.1 → 2.1.0 | 分页引用默认关闭 |
| rename | 2.0.1 → 2.1.0 | 支持 inline 与分页消费 |
| search | 2.1.0 → 2.1.1 | 季集证据修复、审计与等待优化 |
| sync | 2.0.0 → 2.0.1 | 交互、取消、缓存与索引 |
| caption | 0.1.4 → 0.1.4 | 无变更 |

1. 等待 Syncthing 显示 `Up to Date / 最新`，用户在 `/mnt/user/archives/life hacker/telepiplex` 接续检查与发布。先更新 Host，检查旧按钮过期、新按钮、取消与封口。
2. 更新 download，保持 `enable_tree_snapshot_references: false`；随后更新 rename，先验小树与超限明确停止。两个 Feature 各自携带匹配 SDK 依赖。
3. 确认两端均为新版本后再显式开启分页，先用 1,001 节点受控任务验证计数与失败反馈，再逐步增加规模。快照 sidecar 必须保留。
4. 独立更新 search 和 sync，各批至少观察 10 次代表任务，包含适用的取消、重复点击、源失败和重启。搜索看首次候选与确认耗时；sync 看重复作品请求量与退避等待。
5. 每批若出现漏文件、重复写入、错误终态、失效按钮或协议不兼容，停止该组件后续更新并保留日志。线上结果另记，不由本地测试代替。

当前快照不设 TTL，也没有自动垃圾回收；磁盘会随任务增加。回退前先关闭新引用发送，处理或保留活动任务和两端 `.snapshots.sqlite3` 文件，再回退消费者。旧 Job 表结构不变，但旧消费者不支持新版引用任务。

## 文件与审查证据

本轮新增 27、修改 61 个源码/测试/文档文件，没有删除或重命名；caption 源码哈希与基线一致，构建缓存由现有 Syncthing 忽略规则排除。逐文件清单见 [本轮改动清单](2026-09-05-iteration-files.md)。实现裁定、各批红绿验证和独立审查状态见 [执行记录](2026-09-05-iteration-execution.md)。审计发现与原始分轮验收要求保留在 [原审计](2026-09-05-business-flow-audit.md) 和 [原方案](../superpowers/plans/2026-09-05-business-flow-iterations.md)。
