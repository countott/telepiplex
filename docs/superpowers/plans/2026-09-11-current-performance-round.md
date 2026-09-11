# telepiplex 当前版本第二轮性能优化计划

> 执行方式：`superpowers:subagent-driven-development`，每包先复现、后实现、独立审查；用户已要求基于当前版本再发起一轮优化，按授权连续完成本地工作。

**Goal:** 降低历史任务增长、慢海报来源和大快照恢复造成的重复工作，同时保留业务完整性与按钮生命周期。

**Architecture:** 三个互不重叠的最小改动包。沿用现有 SQLite partial index、海报优先级和持久快照接口，不新增业务入口或缓存体系；最终由真实 Feature/RPC 与六套测试验证组合。

**Tech Stack:** Python 3.12、asyncio、SQLite、pytest、现有 Feature RPC。

**Spec:** 用户“针对目前的，在发起一轮优化”、AGENTS.md、本文约束和各包复现证据。上一轮报告仅作历史线索；当前基线 Host3.6.11/search2.2.0/download2.1.0/rename2.1.0/sync2.0.1/SDK2.1.0/caption0.1.4。

## Global Constraints

- 只在 Mac 读取、修改和本地测试；不运行 Git、创建 `.git`/`.worktrees`、连接本项目 GitHub、发布或操作真实远端媒体。
- 产品文案写作 `telepiplex`；技术身份不变。
- 保留 search2.2.0 的 `/pr` 原文检索及现役搜索逻辑；v2 稳定身份、季集、片源硬门、取消与幂等不放宽。
- 自动流程止于 rename；sync 手动，caption 不扩展。
- 保留 9 月 9 日按钮生命周期、原消息归属、代次、渲染锁、清理确认和持久重试。
- 快照引用仍默认关闭，协议/schema/页容量/节点上限不变；数据摘要、连续性、对象身份与整理前后验证不能省略。
- 无证据不增加根缓存，不删第二次远端整树复验，不新增快照垃圾回收。
- 用户已授权执行；Git/worktree/commit 技能步骤以本地源码副本与 difflib 替代。审查材料位于 `/tmp/telepiplex-20260911/`。

## Task 1：历史任务查询使用既有索引

Files：修改 `app/runtime/interaction_coordinator.py`，测试 `tests/test_interaction_coordinator.py`（或新增同主题测试文件）。

接口保持 `active(chat_id,user_id)` 与 `active_records()` 返回值及排序。数据库已有 `operations_one_active_owner` partial index；当前参数化状态谓词不能被 SQLite 用来匹配其字面量条件。

- [x] 用真实 SQLite 填充历史终态与少量活动任务，记录查询计划、VM progress 计数和结果；新增回归证明重复清理保护查询不随历史规模全扫描。不同 owner、各活动状态、无活动结果均检查。
- [x] 先跑新增回归，确认旧代码在读成本门失败。
- [x] 共享从受信任 `ACTIVE_STATES` 生成的排序字面量谓词，供建表与查询一致使用；chat/user 等输入保持参数化。示意：`state IN ('awaiting_input', ...)` 只由代码内状态集合生成，不拼接用户数据。
- [x] 对 `active_records()` 验证相同根因后同包修复，保留 `ORDER BY created_at, operation_id`；不创建新索引或迁移。
- [x] 针对性业务测试与清理/生命周期邻接测试；同机前后对照 1k/10k/100k 历史任务，报告实际计数和至少 30 次耗时分布；独立审查。

## Task 2：候选海报按优先级完成等待

Files：修改 `features/search/src/telepiplex_search/service.py::_supplement_candidate_posters`，新增 `features/search/tests/test_candidate_poster_waiting.py`（必要时使用现有相邻测试）。

消费 `candidate_poster_lookup(candidate,provider)`；不改变其身份核验、SourceScheduler 或最终候选结构。每个前五候选的选择优先级保持 TMDB → Douban → TVDB；已有有效海报不新查。

- [x] 事件门控复现：TMDB 已返回有效海报但低优先级读取未结束时，旧代码仍等全部完成；外层取消后存在未清理的 consumer tasks。记录真实任务生命周期。
- [x] 新增失败回归：高优先级胜出提前结束；低优先级先返回必须等待更高来源明确失败/空结果或原超时；多候选彼此独立；来源异常/超时保持原回退结果；外层取消清理全部本方法创建的 consumer tasks，不修改 stored。
- [x] 使用 `FIRST_COMPLETED` 循环和原总 deadline；只有某候选最佳结果已确定才取消其无用 consumer；全部候选确定即返回。每次退出在 `finally` cancel/gather 所有本地 consumer。底层 SourceScheduler shield 保留，不能声称线程 I/O 已停止。
- [x] 结果仅在成功等待阶段之后提交到本地副本；取消不能晚写候选、已确认身份或下载合同。保留 generation/revision 上层保护。
- [x] 运行海报/候选/生命周期/原文检索相关回归；至少 30 次同机受控延迟前后比较，核对选择完全相同、provider 启动次数不增加；独立审查。

## Task 3：快照只做必要的持久读取与验证

Files：修改 `features/rename/src/telepiplex_rename/snapshot_reader.py`，测试 `features/rename/tests/test_snapshot_reader.py` 及根 `tests/test_large_snapshot_execution.py`。

接口 `read_snapshot(host,jobs,ref,*,job_id,root_path,check_cancelled,timeout=120)` 不变。SDK `SnapshotStore.get` 已完整验证页、计数、拓扑和摘要；`put` 已在事务前验证。无需新增 SDK API。

- [x] 对 10k/20k 节点本地恢复计数：当前两次 get 和外层验证重复；固定真实 SQLite 数据、校验调用数、事件循环延迟证据。
- [x] 新增失败回归：缓存命中只完整读盘一次、仍拒绝坏摘要/缺页；慢本地存储不能堵事件循环；取消后无确认/文件修改；首次获取仍必须提交后重读验证才能 ack。
- [x] 将 SnapshotStore 构造、contains、put 与 get+展开 entries 放入 `asyncio.to_thread`；每个 await 前后保留事件循环内取消检查。示意：`pages = store.get(ref); return [row for page in pages for row in page['entries']]`，复用 get 已完成的校验，不绕过验证。
- [x] 首次获取依旧逐页校验并在 `put` 内做完整校验，持久提交后单次 get 重读验证；已存在副本单次 get；不要为传输重新扫描远端。
- [x] 本地写线程取消后可能完成持久副本，明确这是本地写入收尾，不可追加 ack/远端文件变更；缺页、错引用、重启与并发幂等不退化。
- [x] 运行 rename 快照/完整性与根真实分页/RPC/10k执行器回归，比较读盘/校验计数、总时长和事件循环响应；独立审查。

## Task 4：组合验收、版本与交付

- [x] 只提升实际变动组件：Host3.6.12、search2.2.1、rename2.1.1；SDK/download/sync/caption 不动。manifest/pyproject、当前 README 与版本合同同步；历史文档不改写。
- [x] 全部实施后生成基线 difflib，完成最终跨模块独立审查。
- [x] 六套默认 pytest 在保留外网违规的 guard 下运行；跳过在线与未提供制品项单独说明。命令以 AGENTS.md 为准，实际输出留档。
- [x] 验证真实 Feature/RPC 与 10k 文件重放，必要压力使用 `pressure_operation_pipeline.py --pipelines 200 --concurrency 32 --milestone-faults 10` 及有延迟的 Telegram queue；不重复无改动场景扩大测试。
- [x] 新增本轮成果与逐文件用途，记录前后基线、收益、未改项、版本和观察节奏。Syncthing 最新后由用户在 Unraid 发布。

节奏：先本地逐包红绿与审查，再合并全量；实际更新顺序为 Host 查询、search 等待、rename 读取，每批先小样本后常规使用，检查取消/重启/失败/原文检索适用项，出现错误终态、归属问题或多余写入则停止该组件后续更新。

实际交付：Host3.6.12/search2.2.1/rename2.1.1，六套共2,064 passed /3 skipped /518 subtests；三包独立审查和最终跨包源码审查通过。详见本轮成果与执行记录。Task3根10k测试沿用既有覆盖，无需修改该测试文件。
