# SDD ledger — plan: docs/superpowers/plans/2026-09-11-current-performance-round.md

用户授权：针对当前版本再发起一轮优化。当前代码与上轮有差异，重新读取9/9交付及search2.2.0原文检索约定。实施前源码副本与SHA-256清单位于 `/tmp/telepiplex-20260911/baseline/`；没有Git。

## Preflight

| 检查范围 | 结论 |
|---|---|
| Host 查询与消息清理 | 查询返回/锁/排序不变，仅复用已有partial index；真实VM开销与清理计数验证 |
| Search 等待与上层候选提交 | 保留优先级、generation/revision、候选身份；取消本方法consumer，SourceScheduler共享IO语义不变 |
| Rename 快照与SDK | SDK get/put已有完整校验；reader删重复调用但保留提交后重读；线程取消不能到达ack/远端变更 |
| 三包文件互相独立 | Host coordinator、Search service、Rename reader无共享源码；根代理统一版本/README/最终测试 |
| 版本与当前新特性 | Host3.6.11/Search2.2.0基线，保留/pr；只在末尾为实际修改组件升patch |

Ruling: 使用本地副本/difflib，保留审查材料，跳过技能中的Git/worktree/commit与删除记录步骤 — AGENTS明确只允许Mac源码/测试 — 无提交历史，使用哈希与逐文件清单补足。
Ruling: 三个不重叠的改动包并行实施，版本最后串行对齐 — 开发者要求主动并行且文件独立 — 若出现共享文件改动必须重新分配所有权，避免覆盖。

## 状态

- Task1 Host：完成（implement_host_round2）。新增成本测试先2 failed、后2 passed；邻接109 passed/22 subtests。正式1k/10k/100k查询基准每档50样本，100k active VM35、active_records51。review_host_round2 独立 Spec ✅ / Quality approved，无修改请求。
- Task2 Search：完成（implement_search_round2）。新增11项测试，冻结基线7 failed/4 passed，正式11 passed；定向218 passed/38 subtests。9情景×30配对共270对，正式结果及调用多重集合全等。review_search_round2 独立 Spec ✅ / Quality approved，另独立32 passed/8 subtests，无修改请求。
- Task3 Snapshot：完成（implement_snapshot_round2）。新回归先4 failed/9 passed、后13 passed/16 subtests；关键3项连续25轮通过；跨包定向30/11/7 passed。正式冷读完整校验4→2、重启3→1，重启get2→1。review_snapshot_round2 独立 Spec ✅ / Quality approved，额外验证8路并发、冲突引用、真实task.cancel()后零ack，无修改请求。
- Task4 最终验证/版本/交付：完成源码和本地验收。Host3.6.12/search2.2.1/rename2.1.1及版本合同已精确对齐；六套共2,064 passed/3 skipped/518 subtests，0失败，全部exit0。200条RPC/10次注入中断、12条延迟Telegram队列/36次点击均0失败。review_final_round2 对源码、测试与版本独立approved，并验证SDK/download/sync/caption基线无差异；正式文档也已由同一审查者复核通过，唯一统计口径P3已修正关闭。

Ruling: 定向测试改变顺序暴露RawSearchTest全局config污染 — 冻结baseline同样21 passed/1 teardown error，且外网保护保留拦截记录 — root只在原fixture增加一行addCleanup恢复先前config；相同顺序转绿21 passed/8 subtests，不修改生产配置行为，不靠调整测试顺序掩盖。

Ruling: 最终全套按各组件文件冻结时间分别运行，版本敏感的Host/search/rename统一版本后运行 — download/sync/caption源码与SDK哈希未变 — 不重复无变化全套；所有定向、审查结果不加入最终2,064总数。

Ruling: 独立审查使用任务级规格与质量合并检查，最终gpt-6-astra跨包审查复核合同和版本 — 各实现者不审自己的实现，审查者只读 — 不以代理口头结论替代diff、原始日志和现场定向结果。

文档复核修正：最终审查指出快照心跳字段是三轮各自最大延迟的中位数，不是所有样本最大值；成果表与方法已明确该口径，并补充20k本轮单次峰值44.67 ms。数值与源码无变化，无需重复代码测试。

材料：`/tmp/telepiplex-20260911/` 下的三份 implementation.md / implementation.diff、三份 review.md、final-review.md、all.diff、files.json、基准脚本/JSON、final-*.log/json。交付文件与逐项用途在[本轮成果](2026-09-11-optimization-results.md)中列出；新增5、修改18，无删除/重命名。

基线Search650passed/2skipped/121subtests（14.95s）；Host完整基线680passed/4failed/1skipped/260subtests（126.66s），4项失败均为现役Search2.2.0与根测试仍写2.1.4不一致；已在最终版本合同包精确对齐，保留断言强度。性能数据为本机受控实验，不是线上性能承诺。当前长尾观察点及分批生效节奏见成果文档；本地完成后仍需等待Syncthing最新，由用户接续Unraid发布。
