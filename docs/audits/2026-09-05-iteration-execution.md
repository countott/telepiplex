# SDD ledger — plan: docs/superpowers/plans/2026-09-05-business-flow-iterations.md

执行授权：用户“执行落地”。范围为全部可在 Mac 完成的源码、测试、文档和性能验证；不执行 Git、发布或真实远端媒体修改。

基线副本与哈希：`/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-iterations-baseline-34jkutz5`。任务报告与无 Git diff 放在 `/tmp/telepiplex-iterations-work/`。

## 执行前检查与裁定

| 批次/共享范围 | 检查结论与安排 |
|---|---|
| 1A sync 按钮/取消 | 同一实现者串行修改 feature 与 sync_service，保留 MCP 身份 |
| 1B Host 封口 | 根代理实施，独立于 Feature 文件 |
| 1C 默认测试隔离 | search 测试先独立处理，随后交接给 3/4 的实现者 |
| 2A / 2B download、rename | 同一目录串行交接，先完成完整性保护的回归再扩展传输 |
| 3 / 4 search 服务、审计工具 | 先对齐真实状态机测试，再做性能；禁止同时写相同文件 |
| 1A / 5A sync_service | 先取消修复，再缓存、索引和退避；分开验证 |
| 1B / 5 Host 消息预算 | 先保证封口正确，补有延迟压力预算，不删竞态补偿 |
| 2B / 3 SDK 与交接 | 先定义分页读写合同，由双方共享验证，再接组合测试 |
| 4C 根去重 / 5B 扫描复用 | 条件项必须有实际重复与新鲜度证据；无证据不增加缓存 |
| 6 遗留/版本/总验证 | 最后核对所有改动，不删除仍服务旧 Job 恢复的路径 |

Ruling: 不运行技能中的 Git/worktree/commit 脚本，改用本地哈希副本和 diff — 用户 AGENTS.md 明确禁止 Mac Git — 失去提交级检查，使用文件差异、测试和独立审查补足。

Ruling: 允许互不重叠的 Feature 子任务并行实施 — 开发者要求主动并行且方案允许 — 根代理维护文件所有权，共享文件必须先交接。

Ruling: 本地实施逐包推进，不以等待用户 Unraid 发布作为后续本地开发的前置条件 — 用户已授权完整落地，方案允许继续本地工作 — 交付保留上线顺序与线上未验证说明。

## 状态

- 1A sync 按钮与取消：150 passed / 72 subtests + Host 7 passed；独立审查发现 pending_selection 等待期间取消未终结持久化 Job；第 1 轮已修复并复审通过。
- 1B Host 封口与旧按钮反馈：完成。round1正式diff独立复审通过；185 passed/20subtests，500ms实际busy与两类补偿已分开计数。
- 1C 网络隔离：完成并审查通过。已知外网尝试 6→0；AF_UNIX 已补真实 connect。
- 2A 完整性保护：完成。首轮3项审查问题已修复复审通过；download 163 passed / 31 subtests，rename 372 passed / 8 subtests。
- 2B 大树快照分页：完成并独立审查通过，实际分页→10k执行器与重建重放通过。
- 3 全流验收：完成。Search同QID证据及root片源穿透/重复任务drain两轮审查问题已修复复审通过。
- 4 搜索性能：完成并独立审查通过，4A/4B达成本地目标；4C未发现重复根读取，不增加缓存。
- 5 Plex / I/O / Host 预算：完成并独立审查通过。5B因缺远端变更令牌保留第二次扫描。
- 6 遗留、版本与最终验证：本地完成。最终Host633passed/225subtests/1skip；六套总计1943passed/443subtests/3skip，独立总审查通过。线上验收由用户部署后单独记录。

## 验证与审查记录

实施开始时只建立源码副本，未将前一轮测试结果作为本轮完成证据。各任务实际验证结果随完成追加。

Task 1C: complete (local hash diff; independent review approved).

Task 1A: complete (local diff; round1 reviewer approved).
Task 2A: fix round1 in progress (malformed falsey size, partial-cleanup cancellation accounting, shared scanner).
Ruling: 将两份严格扫描器合并到 SDK 公共模块 — 审查确认安全校验重复，2B 亦需共同合同 — 需要 download/rename 同步 SDK 依赖版本并保留旧格式回归。

Task 2A: complete (local diff; round1 approved; shared SDK version aligned to2.1.0).

验证追加：完整 Host 测试在保留外网违规的 autouse guard 下 629 passed / 1 skipped / 225 subtests (89.12s)，未发现出网违规。此为修改中的预检，最终版本对齐后再跑完整回归。
组合测试：真实 Feature/Host Unix RPC，metadata v2、下载完成、整理、投递响应丢失和重建 rename 重放 2 passed (0.89s)，文件身份稳定且重复投递零追加写入。
1C minor 已补真实 AF_UNIX bind/listen/connect/accept，用例2 passed。

Task 5A: complete (157 passed / 80 subtests + 8 Host contracts; independent review approved).
Task 2B: source reviewed clean; actual durable paging→FileExecutor 10000 files/500 directories:1 passed (3.12s), first move10000, restart replay no additional moves, count conservation checked.
Ruling: 将1001节点的空目录组合案例预期设为整理失败 — 原清理规则保护未涉及媒体的空目录，不能假报根目录已清空 — 本次不扩大清理删除范围；另用10000文件实际执行与重放验证大树成功计数。
Ruling: 活跃子代理槽位达到上限时复用已结束的独立审查者处理另一个互不相关包 — 分配新审查线程被工具拒绝 — 各包仍使用独立brief/report/diff，复用者不审自己实现。

版本包：Host3.6.9/API1.7、SDK2.1.0、download/rename2.1.0、sync2.0.1；search2.1.1。caption0.1.4不变。SDK示例与builder断言随新SDK对齐，历史audit版本记录不改。

Ruling: 4A 标题检索仅在第一次中文 Wikipedia 查询成功后启动，并与后续实体/英文 Wikipedia 查询重叠 — 保持成功路径请求数不变，同时缩短串行等待 — 首次 Wikipedia 失败仍不发标题请求；之后的失败最多留下一个已启动的逻辑标题查询，沿用原超时与重试上限、保留原错误优先级，单独报告这项预读成本。

Task 3: complete; same-QID scope and root integration round1 approved. Actual root integration 3 passed (5.87s) now checks exact selected release/link/path through RPC and drains duplicate callback/event work before asserting zero writes.
Task 4: complete; independent review approved. 30-sample/side median improvement24.43%/38.80%, p95 improved, success result/call sets equal. Optional validator-return assertion strengthened; only its relevant test rerun.
版本构建回归：test_bot_runtime_startup.py + test_feature_builder.py，43 passed /1 skipped /16 subtests (7.18s)。
最终Feature回归（外网违规保留）：download174/33subtests(2.66s)，rename379/22(7.69s)，sync157/80(5.07s)，caption1(0.04s)，search599/2skipped/83(12.77s)，均通过。Search已经对齐2.1.1/SDK2.1.0。

Task 1B round1: complete; independent review approved, reviewer scoped2 passed. Forced race raw11=normal9+inactive1+inflight-seal1; original clear remains normal. Busy500ms entered actual transport; no task/FD growth.
Final source review: approved, no Critical/Important; final-integration-review.md and host-fixes-round1-review.md.
最终Host首轮627passed/6failed/1skipped/225subtests(99.44s)：6失败均为当前版本表/SDK断言/模拟发布标签滞后，已修复这些测试，不改发布脚本。定向版本37passed/24subtests + 发布模拟5passed/3subtests，完整Host重新运行。
Telegram最终10任务/50ms/重复点击2：10completed、30durably sealed、0failures，raw11/任务=normal10+inactive recovery1，task/FD运行与teardown增量均0。

## 最终交付结论

Task 6: complete for local scope. Final Host rerun633 passed /1 skipped /225 subtests in97.53s after six stale version fixtures/assertions were aligned. Final combined suites1943 passed /443 subtests /3 skipped. Search final single assertion strengthening11passed/2subtests verified separately; no duplicate counting.
Final independent review approved, including Host round1 and late version-contract tests; no Critical/Important left. Source inventory:27 added/61 modified/0 deleted/0 renamed. caption unchanged. Local boundary checks passed: no .git/.worktrees, .stfolder retained. No actual Git, publication, or online media operation.
全部本地批次闭环；4C无相同根读取、5B缺远端变更令牌，按条件不新增缓存或删掉复验。完整成果、实际命令、跳过项及上线节奏见2026-09-05-iteration-results.md，逐文件用途见2026-09-05-iteration-files.md。等待Syncthing Up to Date / 最新后由用户在Unraid继续。
