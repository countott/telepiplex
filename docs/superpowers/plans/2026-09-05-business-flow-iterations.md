# telepiplex 业务流修复与性能优化迭代方案

> 执行追踪：用户随后已授权“执行落地”。本文保留审计时的方案与验收目标，当前实施结果、实际版本和交付节奏见 [迭代成果](../../audits/2026-09-05-iteration-results.md)。以下原始复选框不作为当前执行状态。

> 本文是分阶段交付与验收方案，本轮不执行业务代码修改。后续实施按批次使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans`；每个改动先用失败复现确定边界，再修复和验证。

**Goal:** 覆盖本轮审计的全部缺陷、测试缺口、性能问题和遗留路径，逐批恢复可靠业务流并减少可证明的重复工作。

**Architecture:** 保持 `search → download → rename` 的自动流程和 sync 的独立手动入口。先修局部功能和完整性，再对齐真实业务验收，最后按请求计数和受控延迟优化；大目录安全保护与分页协议分两步交付。

**Tech Stack:** Python 3.12、asyncio、SQLite、python-telegram-bot、Unix RPC、现有 Feature capability/event 合同、pytest。

**Spec:** [2026-09-05 业务流审计](../../audits/2026-09-05-business-flow-audit.md)。这里的性能阈值属于拟定验收目标，不是已经取得的优化结果或线上 SLO。

## 总体节奏

| 轮次 | 目的 | 本轮交付 | 进入下一轮的条件 |
|---|---|---|---|
| 1 | 恢复可靠交互 | sync 按钮、Host 封口、旧任务取消；并行补默认测试网络隔离 | 真实 Host/Feature 按钮链路可用；取消和封口回归通过 |
| 2 | 保证文件完整性 | 2A 大目录清理前安全停止；2B 完整快照与分页传输 | 超限不先改文件；支持的大目录无漏计；混合版本与恢复通过 |
| 3 | 让“全流通过”可信 | 真实 Feature 状态机、v2 handoff、分层 live 审计与失败分类 | 成功、合理拒绝、上游失败可区分；不再用旧辅助路径代替业务完成 |
| 4 | 减少搜索等待 | 独立查询并行 → 本地化预算 → 有证据时做根请求去重 | 候选身份/顺序/范围不变；请求量不增加；受控性能改善 |
| 5 | 减少远端重复工作 | Plex 作品缓存、批量路径索引与退避；有条件复用下载扫描 | 写入正确性不退步；调用量达到目标；取消及时生效 |
| 6 | 清理并验证真实使用 | 遗留入口封边、无调用代码清理、独立版本交付与真实样本复核 | 全量本地回归通过，线上部分取得真实结果后单独记录结论 |

“一轮”按验收条件推进，不绑定一个固定自然日。每轮分为下文的小改动包；一个包完成“失败复现 → 最小修改 → 针对性验证 → 审查”后即可交付，不等全部六轮完成。

第 1 轮的 Host 和 sync 可并行开发，涉及同一个 Feature 文件的改动串行进行。第 2 轮必须先交付 2A 再开放 2B。第 3 轮的状态机测试准备可与前两轮并行，但它的最终验收依赖前两轮修复。第 4、5 轮可以独立开发，实际更新逐个观察。

每个运行版本由用户完成 Unraid 更新后，建议先走一次该批关键入口，再积累至少 10 次代表性任务或控制用例，包含取消、失败恢复和重启中的适用项。发现新增漏文件、重复写入、错误终态、无响应按钮或协议不兼容，暂停该组件后续更新；可继续其他组件的 Mac 本地工作。样本数是观察门槛，不代表穷尽证明。

## 全局边界与版本

- 只在 Mac `/Users/young/Documents/telepiplex` 读取、编辑和测试；不使用 Git、`.git`、`.worktrees` 或连接本项目 GitHub。
- 产品文案使用小写 `telepiplex`；保留 `plugin_id`、`library.sync`、`storage.provider`、事件名、`plex_*` MCP 工具名及既有配置/数据库身份。
- 自动流程止于 rename；不恢复自动 Plex 联动。caption 维持占位，本轮不扩展字幕产品能力。
- 保留 metadata v2、片源硬校验、单用户单活动任务、稳定对象身份、移动前后校验、空目录清理验证和实际取消语义。
- 本轮源码版本核对为 Host `3.6.8` / Host API `1.7`、SDK `2.0.0`、download/rename `2.0.1`、search `2.1.0`、sync `2.0.0`。这是本地源码身份，不是生产安装状态；实施时重新核对，按独立改动包递增版本，不提前占用版本号。
- 功能修复按实际影响提升对应组件 patch；新增分页协议按兼容性决定 Feature 版本和依赖声明。每次对齐 manifest 与 pyproject；没有改动的 Feature 不一起升版。
- 本轮只新增本方案。以下命令和测试是后续实施要求，未在本轮重新执行；前一轮的 1,783 passed / 393 subtests / 3 skipped 仅作为审计基线。

## 第 1 轮：交互修复与测试隔离

**1A — sync 正常按钮与旧任务取消。** sync 内先完成按钮合同，再完成恢复取消的最小修复；两项分别验证，组成一个可交付的 sync 修复包。

- [ ] 将 `feature.py` 与 `config_wizard.py` 中扫描、翻页、选择、配置、取消按钮的生成/解析统一到 `sync:`，同步 README 与 `/sync` 用法。
- [ ] 用真实 manifest、Feature command、Host `_keyboard_markup` 和 callback 路由验证所有现役按钮，而不是继续只断言 Feature 内部字面量。
- [ ] 旧 `plex:*` 按钮沿用或补齐过期反馈；不重写成新按钮执行，不注册可写的旧前缀别名，不清除新任务的键盘。需要补 Host 反馈时单独放入 Host 包。
- [ ] 在 `sync_service.py` 把取消检查传入 target、part 和每次外部写入边界；完成、等待选择前再检查。`PlexOperationCancelled` 不得被普通错误处理转为 warning，已完成的远端变更先记录再停后续写入。
- [ ] 在 `feature.py` 防止旧任务的 `cancelling` 被迟到的 `completed` 覆盖。

验收：`/scan` 全部/单库/翻页/退出、`/sync_config` 选择/保存/退出和 `/sync <Job ID>` 的选择按钮均可经 Host 使用。旧按钮产生 0 次业务写入。两目标字幕案例首个写入期间取消，第二个写入为 0，最终为 cancelled；海报、音轨和选择恢复验证同类边界。已发生修改不得显示“已回滚”。

主要文件：`features/sync/src/telepiplex_sync/{feature.py,config_wizard.py,sync_service.py}`、`features/sync/README.md`、对应 `test_feature_runtime.py`、`test_config_wizard.py`、`test_sync_service.py`。跨模块按钮合同测试拟新增 `tests/test_feature_action_contracts.py`。

**1B — Host 封口竞态。**

- [ ] 将前轮 `/tmp` 封口复现固定为 `tests/test_interaction_handler.py` 中基于事件的测试，覆盖 text、caption、media 编辑在途时 seal。
- [ ] 在 `_render_operation_segment_locked` 区分“本次请求已清空按钮”和“请求期间才进入 sealing”；后者只对原消息补清按钮并确认，再持久化 sealed。
- [ ] 清按钮失败时保留可恢复状态，不能返回封口成功；重试仍绑定同一个 segment/message，不重新发送业务消息。

验收：数据库 sealed 的消息按钮为空；封口重试、迟到编辑、重复回调和重启均不重复下载/整理、不覆盖新段。正常 report+seal 合并仍保持一次必要内容编辑，竞态补偿调用单独计数。

主要文件：`app/handlers/interaction_handler.py`、必要时 `app/handlers/plugin_handler.py`，对应 Host handler、operation pipeline 与 pressure 测试。Host API 保持 1.7，不为此重做调度体系。

**1C — 默认测试网络隔离，和 1A/1B 并行。**

- [ ] 修正 search 的 `test_work_discovery.py`、`test_direct_link.py` 漏替换的 provider；新增测试级网络保护。
- [ ] 网络保护记录违规请求并在 teardown 失败，避免 adapter 捕获网络异常后测试仍通过；显式 live 测试通过独立开关和实际所需配置运行。
- [ ] 先覆盖本轮发现的 3 个测试，再检查默认 Host/Feature 测试中其他意外出网；Unix socket、loopback 集成测试按用途保留。

验收：已发现的 6 次请求尝试归零；刻意漏一个 provider 替身时，即便业务层捕获异常，测试仍失败。稳定性和请求隔离是门槛，耗时改善另记，不承诺固定减少 13 秒。

## 第 2 轮：先停止部分整理，再支持大目录

**2A — 保留现有小树格式，增加完整性和容量检查。**

- [ ] `get_file_tree` 只允许返回完整树或明确错误。暂保留 1,000 节点边界，按 offset 分页验证尾页，检测重复页、目录环、缺 ID、异常响应及深度越界。
- [ ] 顺序为：下载完成 → 完整扫描 → 计算清理计划 → 检查预计事件/RPC 编码容量 → 清理 → 完整复验 → 发布。这里“修改前”指后续清理删除、整理改名和移动前；云端下载已经完成。
- [ ] 条数、深度、完整性或容量不满足时保留现有文件，给出明确失败原因，不交接 rename。编码检查覆盖完整消息封装，不能只量 `file_tree` 字段；RPC 上限保持 1,048,576 字节。
- [ ] 新事件显式声明完整性与传输格式；rename 在修改文件前验证。旧事件缺完整性标记时严格补扫验证，已完成任务依旧按原幂等结果返回。未知传输格式明确拒绝，不回落到现场猜测。
- [ ] 清理后远端变化导致复验失败时，准确保留已执行的清理记录，不能宣称全程零修改。

验收：999、1,000 节点完整成功；1,001 节点、多目录合计超限、深度截断、重复页和畸形响应在清理/改名/移动前拒绝。长中文路径触及 RPC 临界值时同样提前停止。包含字幕的原清理规则保持现状。

主要文件：download `client.py/service.py/failure.py`；rename `service.py/processor.py`；对应 Feature 运行、文件事实和完整性测试，以及 Host 交接测试。先交付 download 严格读取，再交付 rename 入口保护，两者仍兼容原小树 inline。

**2B — 新增持久化完整快照和分页读取，单独交付。**

推荐使用 download 持有清理后不可变快照，rename 经现有 `storage.provider` 的新增分页方法读取。现有 inventory 的 `snapshot_id` 只是 UUID/文件事实标记，并不是已有快照服务；这一轮需要真实实现存储和生命周期。

- [ ] 在 download 持久化快照及 `job_id`、根身份、节点/文件数、内容摘要，分页游标绑定快照；同游标始终返回相同内容。
- [ ] `download.completed` 对大树发送带版本的快照引用；小树保留 inline。分页同时限制条数和编码字节，建议每页数据最多 262,144 字节，完整 RPC 帧仍小于 1 MiB。
- [ ] rename 拉齐全部分页并验证连续性、总数与摘要，持久化完整副本后才调用原 file-first 整理。缺页、摘要不符、失效引用、取消时文件修改为 0；不得隐式重新扫描另一份树代替。
- [ ] 消费者持久化完成后才确认副本可用。快照生命周期保留下载/rename 重放、等待确认与恢复所需数据，不以短 TTL 删除仍被活动任务引用的快照。
- [ ] 快照传输与远端实时校验分开：快照完整不代表云端此后未变，继续保留对象身份和移动前后检查。

新增模块建议：download `snapshot_store.py`、rename `snapshot_reader.py`；调整双方 jobs/service/models，按公共合同需要更新 SDK 校验。新增存储若改变 state schema，迁移和回退边界单独测试；不把新版数据库直接交给未验证的旧版本。

兼容顺序：先部署快照提供方但仍拒绝大树 → 部署可读取分页且兼容 inline 的 rename → 开启引用发送。2A 的未知格式拒绝必须先到位；旧消费者不得收到可被误认为完整 inline 的引用载荷。回退时先停止新引用任务，处理/保留活动快照，再回退；不能一边生产新格式一边退回不支持的消费者。

验收：999/1,000/1,001 节点和 10,000 文件/500 目录均无漏计；输入数量等于整理、原位保留及明确失败数量之和。丢失分页响应、重复交接、双方重启、分页中取消、超长文件名、未知格式与跨快照错游标均覆盖。传输快照不得为每一页重新扫描 115。2A 验收后即可独立使用，不等待 2B。

## 第 3 轮：全链路验收对齐生产

- [ ] 将前轮临时复现固化进相应测试，所有独立修复均有跨边界断言。
- [ ] 调整 `features/search/src/telepiplex_search/live_pipeline_audit.py` 和 `tools/run_live_pipeline_audit.py`，经真实 `SearchFeature.command/callback` 驱动确认与季集选择，捕获最后的 `download.provider` 投递。
- [ ] 组合 Host/RPC 测试接真实 Feature service/state，外部 provider、Telegram 和文件服务替换；成功路径验证冻结身份、范围、release、v2、幂等键及最终 rename 状态，不能仅用 SDK roundtrip 代替。
- [ ] 输出分开计数：`business_success`、`safe_rejection`、`source_failure`、`unexpected_failure`。合理拒绝可通过预期用例，但不计入业务完成率；依赖缺失和未执行项明确为 skipped。
- [ ] 分开配置默认离线模式、公共元数据只读模式、真实 Prowlarr 查询模式。在线真实下载/文件改动不因开启审计而发生，仍按用户实际任务执行。删除不再适用的旧 AI/TVDB 配置门槛，保留实际所需项。

验收样本固定同名电影/剧集、明确年份、单季/连载季、Sxx/SxxExx、目录缺失、部分片源、取消、投递响应丢失、重启及大树。重点比较稳定身份和候选集合，不只比较数量。想见你 2019/2022、Fargo 1996/2014、Westworld 1973/2016 都覆盖到明确 v2 或预期拒绝。

该轮没有线上凭证也能完成离线合同验收；线上结果留在独立结果栏，不拖延已完成的本地修复，也不冒充线上通过。

## 第 4 轮：分三步优化搜索等待

**4A — 独立查询并行，先不同时改缓存。** 在 `work_discovery.py` 让 Wikidata title-search 与独立 Wikipedia 根发现重叠；保留所有结果的确定性合并及英文 fallback 条件。若进一步并行中英 HTTP，另作小包，遵守现有 provider 并发上限。

验收：候选稳定身份、排序、年份/类型/范围和失败语义不变；请求总数不增加；“先返回者获胜”不作为选择规则。用同机同延迟的至少 30 次基线/修改后实验，结构上证实等待重叠；中位数下降至少 20% 作为初始本地目标，p95 不劣化。前轮 359.83 ms 单次人工延迟不能直接当线上基准。

**4B — 本地化整体预算。** 修改 `service.py::_localize_exact_douban_candidates` 和 `candidate_locale.py`，先区分展示补丁与来源绑定，再控制等待时长。当前 locale 会改中文名、别名、Douban ID 和 source_links，不可整段随意后台化。

候选阶段冻结 ID、顺序、按钮对应关系、根身份和范围；返回结果按候选 ID 与展示 revision 应用。用户确认后冻结已验证身份、来源引用、季集坐标、查询及最终 v2，晚到 locale 不改命名/下载合同。纯海报补丁也只写仍有效的展示。保留现有强匹配验证；超时后使用已有有效身份，缺少必要身份则明确停止。

验收：正常返回、超时、冲突、先选择后补全、翻页、取消及重启交错均不污染已确认状态。等待预算用假时钟验证准时结束；停止等待后仍在底层执行的网络请求必须受自身超时约束，不能把未结束线程误报为已停止 I/O。

**4C — 根查询去重，按证据启动。** 单用户单任务情况下，先检查任务内重复来源读取和连续重试日志；确认有重复成功读取才复用 `SourceScheduler`。先任务内共享，再考虑有界短 TTL；不缓存包含 owner/plan_id/revision 的整份计划，不缓存临时失败。

验收：相同 provider、查询、语言、范围、配置语境只共享应共享的读取；不同范围不误合并；取消一个等待者不破坏其他消费者。没有实际重复路径时，本项结论记录为“无需改动”，不为双并发合成样本额外增加常驻复杂度。

## 第 5 轮：Plex 与文件 I/O，分别交付

**5A — Plex 旧任务恢复。** 依赖第 1 轮取消修复。先在 `sync_service.py` 做单次 Job 内的作品元数据缓存，再在 `adapters/plex.py` 做路径索引，最后引入轮询退避；每一步分开比较调用量。

- 同一 24 集作品的 TMDB details 与 show 读取，从各 24 次降到各 1 次；音轨/字幕选择状态不套用长缓存，写入后刷新。
- 每轮归一化媒体路径一次，批量匹配目标；精确文件路径使用索引，目录/多 part 匹配保留相应规则和重复候选拒绝。
- 首次立即查找，未命中后按 5、10、20、30、30…秒等待，限制总预算 300 秒。按假时钟验收全库定位最多 13 次（具体末次边界以实现测试锁定），从当前 61 次减少；末段可能多等约 30 秒才能发现新入库媒体，日志和配置说明呈现这一取舍。
- 睡眠拆成可取消等待；取消不等到最长退避结束。索引复用不跨越必须刷新远端状态的写入边界。

验收：5,000 媒体/50 目标的精确路径用例不再逐条产生 250,000 次匹配；缺路径、重复路径、多 part、目录路由和最终一致性均正确。保持独立 `/scan` 只提交扫描，不附带全库增强定位。

**5B — download/rename 扫描复用，条件实施。** 依赖第 2 轮完整快照。将扫描、清理复验、目标冲突检查、移动后验证的请求分别计数，只消除同一可信时点的重复扫描。

无清理删除且能证明本次完整快照满足所需时效时，评估省去一次重复全树读取；有清理、外部变化迹象或缺少可用校验证据时仍复验。对于没有可靠变更标识的 115 返回，保留第二次读取，并将此项记录为当前不能安全省略，不用缓存冒充新鲜状态。

验收：普通无变化用例产生明确的减少请求收益；注入源文件变化、目标冲突、同名不同身份、部分移动失败、空目录清理失败时仍正确阻止成功。32 批 native move、134 次目录读取只是前轮样本的计数基线，不是统一的删减配额。

**Host 请求预算并行补强。** 在 `tools/pressure_telegram_pipeline.py` 和测试中使用 0/50 ms 及 500 ms busy 场景。标准非竞态路径先要求不高于同场景基线的 10 次 Telegram API/任务；若确有可合并投影，再追求 9 次。竞态补偿不为了预算而省略，所有场景仍要求正确终态、空按钮、task/FD 增量 0。

## 第 6 轮：遗留收敛与真实使用验收

- [ ] 用真实 manifest/runtime.dispatch/capability 测试固定 `media.organized` 无自动订阅、`library.sync` 只允许 get_job/list_jobs；不恢复已退役入口。
- [ ] inactive metadata_id 批次合并逻辑不改旧数据库键、不重建旧 Job。当前以不可调用测试和兼容范围说明关闭此项；未来若新增增强任务入口，按操作/请求身份另立幂等契约，媒体 ID 仅标识内容。
- [ ] 从现役 `/sync`、`/scan`、配置、MCP retry 和旧 Job 恢复入口画出调用范围；仅删除已证实无调用且无持久数据依赖的辅助路径。保留旧任务、步骤结果和可用的恢复能力，清理不夹带 schema 迁移。
- [ ] 将旧 `/plex` 用法、旧 v1 全流表述、错误性能口径同步到文档与测试。字幕清理策略与 caption 占位记录为现状，不擅自改变产品范围。
- [ ] 本地全量回归与组合压力通过后逐组件交付。用户更新后核对安装版本，跑代表样本并提供同一会话的机器日志，记录首次候选、确认、Prowlarr、下载、整理及 sync 独立入口的真实结果。

真实验收分层记录：外部服务不可用不能归为本地通过；稳定候选和完整文件计数不能因总耗时变快而放宽。后续性能结论至少同时提供请求数、阶段耗时、样本数与异常比例，再设线上目标。

## 全部审计项的归属

| 审计项 | 处理批次 |
|---|---|
| sync 按钮前缀错误、残留 /plex 提示 | 1A |
| Telegram 编辑/封口竞态 | 1B |
| 旧增强 Job 取消后继续写入 | 1A，必须先于 5A |
| 默认测试漏出网 | 1C |
| 大目录截断与默认完整标记 | 2A |
| 1 MiB 帧限制与真正大目录支持 | 2B |
| live audit 偏离生产/v2、合理拒绝误算成功 | 3 |
| Wikipedia/Wikidata 独立请求串行 | 4A |
| locale 阻塞及晚到结果 | 4B |
| 根查询重复 | 4C，条件实施 |
| Plex 重复 show/TMDB 读取、全库定位与轮询 | 5A |
| 下载两次整树读取、整理验证的请求分类 | 5B，保留必要验证 |
| Host 只有零延迟的 9 次 API 验收 | 5 的预算补强 |
| 旧 metadata_id 去重、遗留入口与无调用代码 | 6，以兼容封边为先 |
| 线上尚未验证、caption 与字幕策略边界 | 6 的分层结果与现状记录 |

## 实施时的验证与交付

每个小包先跑所改测试及相邻合同测试；涉及 download/rename 或 Host 交接时，再跑组合链路。整轮交付前按影响范围复核；第 6 轮跑全量。所有新增回归必须先证明能捕获原问题，不能仅断言新实现自己构造的结果。

下面是计划采用的命令，不是本轮已运行结果：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_interaction_handler.py tests/test_plugin_handler.py \
  tests/test_operation_pipeline_e2e.py tests/test_pressure_telegram_pipeline.py

# 对应 Feature 在各自目录单独运行，避免同名 tests 包互相影响。
for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

# 最终完整 Host/SDK 回归
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

# 组合链路：真实 RPC，业务与外部服务为受控替身
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_operation_pipeline.py --pipelines 100 --concurrency 8 --milestone-faults 30
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_telegram_pipeline.py --pipelines 10 --concurrency 1 \
  --frontend-mode queue --telegram-latency-ms 50 --duplicate-clicks 2 --timeout-seconds 60

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

每次交付列出文件、用途、实际命令与结果、版本变化及观察重点。等待 Syncthing 显示 `Up to Date / 最新`，同步至 `/mnt/user/archives/life hacker/telepiplex`；Git、发布、容器/Feature 更新由用户在 Unraid 执行。单纯给出本方案不创建定时任务，也不代表已经开始修改或发布。
