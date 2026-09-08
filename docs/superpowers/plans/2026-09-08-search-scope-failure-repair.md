# telepiplex 搜索范围与失败收尾修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking. 用户已授权实施，并追加 Wikipedia → TVDB → TMDB 的缺值回退规则。

**Goal:** 让已经展示为可下载的剧集范围保留正确集数，并让搜索失败在正确的消息段结束，留下可定位的异常证据。

**Architecture:** 季集库存与日期按 Wikipedia → TVDB → TMDB 仅缺值回退，保留第一份有效编号体系。复用 `series_scope.py` 已有的逐集播出状态判断，统一展示和选择的口径。在 search 内区分元数据准备、消息段交接、片源请求的失败；继续遵守 Host 的消息段所有权和类型检查。使用现有 SDK 日志的异常与脱敏能力。

**Tech Stack:** Python 3.12、asyncio、pytest/unittest、现有 SDK diagnostics、SQLite InteractionCoordinator。

**Spec:** 用户已要求“把这个加进去一起落地修复”，来源顺序为“Wikipedia优先，其次TVDB，再次TMDB，只有不可用/无值N/A的时候才回退”；本文下列证据与验收条款；项目 `AGENTS.md`。

## Global Constraints

- 私人 Mac 上的 `/Users/young/Documents/telepiplex` 是唯一开发工作区。
- Mac 本地项目彻底不使用 Git。不要在此工作区执行任何 `git` 命令，不要创建、修改或依赖 Git 元数据。
- 产品名称在新增或修改的文案、文档、日志和生成元数据中写作小写 `telepiplex`。
- 当前执行已获用户授权；来源顺序新增要求优先于原计划中的同级来源合并。
- 修复集中在 search；download 无独立失败证据，不预先修改其实现。
- Host 的 `segment_role_conflict` 校验保留，新增跨边界测试验证 search 遵守它。
- 本地测试不访问真实 Prowlarr、115 或 Telegram，不提交真实下载。
- 同步链路为 Mac → Syncthing → `/mnt/user/archives/life hacker/telepiplex`；发布仍由用户在 Unraid 操作。

## 证据、假设与既有验证

原始证据：`/Users/young/Downloads/20260908T140922+0800-1E9EAE9E050A.zip`，日志中的 search 为 2.1.1，download 为 2.1.0。

两次 `/s 寡妇湾` 均识别成功，显示 1 季 10 集已播；2026-09-08 14:19:02 与 14:20:07 点击全剧后分别约 19 ms、7 ms 抛出 ValueError。未记录 identity 段封存、Prowlarr 查询构建或下载投递。失败上报使用 search/text，而原段仍为 identity/photo，Host 拒绝后旧界面残留；计划已被删除，旧按钮返回过期。

已离线复现的缺陷：TVDB/TMDB 历史播出日期不同 → 合并后 `aired=""`，保留 `air_date_candidates` → `series_inventory()` 判定全部已播 → `apply_series_scope()` 只读 `aired`，把 10 集过滤成 0 集 → `confirm_media_metadata()` 抛出 `series_inventory_invalid`。相同日期的对照组保留 10 集，并通过 v1 确认和 v2 投影。

这证明当前代码存在该缺陷，但原日志未保存异常 message、stack 或逐集日期，不能把本次线上 ValueError 的具体字段视为已经唯一确定。计划须同时覆盖 v1 校验失败和 v2 投影失败。

已有验证（调查阶段实际运行）：

- `test_series_scope.py`、`test_media_metadata_v1.py`、`test_media_metadata_v2.py`：43 passed。
- `InteractionCoordinatorTest::test_active_segment_rejects_role_or_presentation_kind_conflict`：1 passed。
- 现有日期冲突测试只检查库存展示；现有 `FakeHost.report_operation()` 无条件接受，未验证真实 Host 的消息段约束。这解释了现有相关测试通过仍遗漏组合缺陷的原因。

## 文件职责

| 文件 | 计划改动 |
| --- | --- |
| `features/search/src/telepiplex_search/series_scope.py` | 统一全剧、整季、单集选择的播出状态判断 |
| `features/search/src/telepiplex_search/service.py` | 记录实际失败阶段，按消息段进度收尾，保留异常对象与会话上下文 |
| `features/search/src/telepiplex_search/search_logging.py` | 给事件日志增加可选异常参数，通过 SDK 生成 message/stack/causes |
| `features/search/tests/test_series_scope.py` | 覆盖历史日期冲突的展示与实际范围选择一致性 |
| `features/search/tests/test_feature_service.py` | 覆盖 v1/v2 失败、消息段切换前后、完整选范围与下载交接 |
| `features/search/tests/test_search_logging.py` | 验证异常进入现有诊断链且敏感信息已脱敏 |
| `tests/test_operation_pipeline_e2e.py` | 使用真实 Host 状态机验证终态上报和释放操作所有权 |
| `tests/test_plugin_handler.py` | 验证旧 callback 返回值不能覆盖已经接受的终态 |

SDK 和 Host 生产代码目前作为既有能力使用；只有新增回归测试证明它们自身也有缺陷，才另行说明证据与必要改动。执行时若准备 search 补丁版本，保持 `features/search/manifest.yaml` 与 `features/search/pyproject.toml` 版本一致；当前基线对应下一补丁版本为 2.1.2，发布由用户操作。

## Task 1: 统一范围选择的播出状态

**Files:** 修改 `series_scope.py`；测试 `test_series_scope.py`、`test_feature_service.py`。

**Interfaces:** 消费 `_item_airing_state(item: dict, today: date) -> str`；保留 `apply_series_scope()` 签名及 contract 结构。产出同一集在菜单库存和选择结果中一致的 aired/scheduled/unknown 判断。

- [x] **Step 1: 先新增失败测试。** 在 `test_series_scope.py` 增加下列测试，复用已有 `contract()` 工厂：

```python
def test_past_date_conflicts_survive_all_selectable_scopes(self):
    value = contract()
    for item in value["items"]:
        item.update(aired="", air_date_conflict=True,
                    air_date_candidates=["2026-06-01", "2026-06-02"])
    today = date(2026, 9, 8)
    self.assertEqual(series_inventory(value, today=today).state_by_season,
                     {1: "completed"})
    choices = [
        ("whole_series", {}, 3),
        ("season", {"season_number": 1}, 3),
        ("episode", {"season_number": 1, "episode_number": 2}, 1),
    ]
    for scope, coordinates, expected in choices:
        with self.subTest(scope=scope):
            selected = apply_series_scope(value, scope, today=today,
                                          **coordinates)
            self.assertEqual(len(selected["items"]), expected)
            self.assertTrue(all(item["air_date_conflict"]
                                for item in selected["items"]))
```

- [x] **Step 2: 用文末 search 命令运行 `tests/test_series_scope.py`，确认新增用例在当前代码失败。** 不改动测试期望来迁就空列表。
- [x] **Step 3: 修改 `apply_series_scope()` 的全剧、整季筛选条件。** 单集入口已通过同一播出状态判断验证，保留该验证；全剧、整季改用下面的逐集条件，保留季号、集号筛选以及 incomplete/unknown 拒绝规则：

```python
_item_airing_state(item, today) == "aired"
```

不为冲突日期任意选一天，不修改来源事实，不把未来或跨越当前日期的冲突认定为已播。保留既有明确允许的缺失库存降级路径。

- [x] **Step 4: 加入反向样例并运行测试。** 以同一工厂分别使用未来日期对、过去与未来混合日期对、空日期列表；全剧/整季应拒绝，未播单集应拒绝。全剧/季/单集的正常日期样例仍通过。
- [x] **Step 5: 在服务测试中用 TVDB/TMDB 两组不同历史日期构建真实 v1 contract，完成候选选择、全剧 callback、v1 确认、v2 投影、mock Prowlarr。** 断言保留全部 10 集、查询已发出、identity 封存先于 search 上报；这条测试补上工厂到消费者的组合覆盖。

## Task 2: 让失败在正确的消息段结束

**Files:** 修改 `service.py`；测试 `test_feature_service.py`、`tests/test_operation_pipeline_e2e.py`、`tests/test_plugin_handler.py`。

**Interfaces:** 消费现有 `_report_operation()`、`_operation_view()`、`stored["identity_segment_sealed"]`、`_host_report_rejected`。新建私有 `stored["release_search_phase"]` 字符串，仅用于该任务的故障定位，不改变 RPC schema。

- [x] **Step 1: 为封存前的元数据失败增加失败测试。** 在现有 `SearchFeatureTest` 中使用该测试骨架，故障由真实调用边界注入：

```python
async def test_metadata_failure_finishes_identity_segment(self):
    plan_id = await self._prepare_search()
    stored = self.feature.plans[plan_id]
    operation_id = stored["operation_id"]
    with patch("telepiplex_search.service.confirm_media_metadata",
               side_effect=ValueError("series_inventory_invalid")):
        await self.feature._release_search_task(plan_id, stored, operation_id)
    failed = self.host.reports[-1]
    self.assertEqual(failed["state"], "failed")
    self.assertEqual(failed["segment"],
                     {"role": "identity", "presentation_kind": "photo"})
    self.assertEqual(failed["details"].get("keyboard", []), [])
    self.assertEqual(self.search_queries, [])
    self.assertEqual(self.host.calls, [])
    self.assertNotIn(plan_id, self.feature.plans)
```

- [x] **Step 2: 运行新增服务测试，确认当前代码返回 search/text 导致失败。** 再用 `project_confirmed_media_metadata_v2` 注入 ValueError，覆盖同一交接前边界。
- [x] **Step 3: 在 `_confirm_and_search()` 的相应调用前更新阶段。**

```python
stored["release_search_phase"] = "metadata_validation"
stored["release_search_phase"] = "metadata_projection"
stored["release_search_phase"] = "identity_delivery"
stored["release_search_phase"] = "prowlarr_query"
stored["release_search_phase"] = "prowlarr_request"
stored["release_search_phase"] = "release_processing"
```

这些赋值分别放在 v1 确认、v2 投影、identity 上报/封存、查询构建、外部片源调用、结果处理之前，不连续放在一起。仅实际外部请求失败才归类为来源不可用；普通 ValueError 归类为内部处理失败。

- [x] **Step 4: 调整 `_release_search_task()` 的失败与取消收尾。** 元数据准备期间尚未封存 identity，使用 `identity_confirmation` 阶段与现有 identity/photo 段报告 failed；identity 已确认封存后的失败使用现有 search/text 段。文案分别为“媒体信息确认失败，请重新搜索。”和“资源搜索失败，请重新搜索。”，详细异常进入日志。终态清空业务键盘；按既有终态规则释放计划和所有权。

核心分支必须表达真实进度：

```python
failure_stage = (
    "prowlarr_search" if stored.get("identity_segment_sealed")
    else "identity_confirmation"
)
```

保留 Host 所有权拒绝后不反复报告的保护；封存响应不确定时沿用现有封存重试与拒绝规则，不能无条件把未确认状态标记为已封存。原始异常和终态报告异常分别记录，不能用后者替代前者。

- [x] **Step 5: 使用真实 InteractionCoordinator 加固验收。** 在既有 E2E 测试的临时 Host 中先登记 identity/photo，再送入修复后的失败报告；断言报告 accepted、operation failed、旧业务键盘消失、可开始新搜索。补充成功封存后 Prowlarr 失败、取消、Host 所有权拒绝的用例。
- [x] **Step 6: 重放旧 callback 与后台失败报告的两个到达顺序。** 在 `test_plugin_handler.py` 复用现有 stale-operation 测试设施，断言终态接受后 revision 更低的 awaiting_input 返回值不会重画“选择全剧”按钮。保留当前 Host 状态机校验，不通过放宽它使测试通过。

## Task 3: 记录原始异常和范围诊断

**Files:** 修改 `search_logging.py`、`service.py`；测试 `test_search_logging.py`。复用 SDK diagnostics，不复制另一套堆栈或脱敏实现。

**Interfaces:** `log_search_event(..., exception: BaseException | None = None, **fields)` 增加可选参数；兼容现有 `error`、`error_code` 等字段调用。SDK 的日志 Handler 已从 `record.exc_info` 构建 `error.message/stack/causes`。

- [x] **Step 1: 在日志测试中构造真实带 traceback 的异常，断言 logger 收到 exc_info。**

```python
def test_failure_preserves_exception_for_diagnostic_handler(self):
    logger = Mock()
    try:
        raise ValueError("series_inventory_invalid")
    except ValueError as exc:
        log_search_event(logger, "search.background_task_failed",
                         search_session_id="failure-1", level="warning",
                         exception=exc, stage="metadata_validation")
        self.assertIs(logger.warning.call_args.kwargs["exc_info"][1], exc)
```

- [x] **Step 2: 运行该测试确认当前实现未传递 exc_info。**
- [x] **Step 3: 把可选异常传给现有日志方法，并更新本次路径的调用方。** 保留既有可读事件文本；仅在异常存在时附加：

```python
kwargs = {}
if exception is not None:
    kwargs["exc_info"] = (type(exception), exception, exception.__traceback__)
method(" ".join(parts), **kwargs)
```

后台失败与 operation.report 失败调用均传入原始异常对象。`release_search_phase` 进入失败日志。先记录原始异常及上报结果，再输出一次终态事件，避免过早清除会话上下文。

- [x] **Step 4: 记录有界的范围选择诊断。** 使用现有 `log_search_measurement()` 记录 `scope`、`input_item_count`、`selected_item_count`、`aired_count`、`scheduled_count`、`unknown_count`、`date_conflict_count`、`inventory_source`，不打印完整 provider 返回值或认证配置。
- [x] **Step 5: 经过真实 SDK diagnostic Handler 检查结果。** 构造异常链并在异常信息中放入测试 token；断言 machine JSON 的 error.type/message/stack/causes 有值、error 与报告错误分开、token 在 human 和 machine 输出均已脱敏。继续运行根目录 `tests/test_diagnostics.py` 与 `tests/test_logger.py` 验证既有诊断能力。

## Task 4: 完整业务验收与本地交付

**Files:** 测试 `features/search/tests/test_feature_service.py`、`tests/test_operation_pipeline_e2e.py`；执行时更新 search 的两个版本声明文件。

**Interfaces:** 消费前三项修复后的范围选择、失败报告与日志能力。成功路径继续使用既有 download.provider 调用及幂等键，不引入下载端的新协议。

- [x] **Step 1: 跑一条正常链路。** 1 季 10 集、TVDB/TMDB 均已播但日期不同；候选确认 → 全剧 → identity 封存 → mock Prowlarr 返回合法片源 → 用户选择片源 → fake download.provider 接受。断言范围不缩水、public metadata v2 有效、只投递一次；成功与失败两条链都不调用真实服务。
- [x] **Step 2: 跑故障矩阵。** v1 失败、v2 失败、封存拒绝、片源超时、取消、重复点击和旧 callback；失败前禁止下载，终态不能被旧响应覆盖，日志能指出实际失败阶段。
- [x] **Step 3: 运行以下本地命令。** 新增回归先在修改前失败，再在修复后通过；保留真实测试输出。

在 `/Users/young/Documents/telepiplex/features/search`：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider tests --tb=short
```

在 `/Users/young/Documents/telepiplex`：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider \
  tests/test_interaction_coordinator.py tests/test_operation_pipeline_e2e.py \
  tests/test_plugin_handler.py tests/test_diagnostics.py tests/test_logger.py --tb=short
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

- [x] **Step 4: 更新补丁版本并交付。** 执行时检查 search 基线未被其他改动推进，再同步更新 manifest 与 pyproject 的补丁版本。列出实际改动文件、测试命令及结果；等待 Syncthing 显示 `Up to Date / 最新` 后由用户在 Unraid 发布。
- [ ] **Step 5: 部署后的用户验证。** 用户重新运行 `/s 寡妇湾`；查看新日志能否走到 Prowlarr 与候选片源。只有新的真实运行证据才能确认原始线上故障已消失；若仍失败，新增异常 message、stack、阶段与计数用于继续定位。

## 计划自检

- 已覆盖确定的消息段冲突和离线复现的日期筛选缺陷，并明确线上首个 ValueError 的证据局限。
- 已覆盖 whole_series、season、episode；没有只修菜单展示或仅增加重试。
- 以真实 Host 校验补足无条件接受的 FakeHost；保留原有状态机限制。
- 不新增下载实现改动、外部调用、Git 操作或自动发布步骤。
- 编码与本地验收已执行，最终结果见下方执行记录；43 + 1 项通过仅属于先前调查基线。部署后的真实运行由用户验证。

## 执行记录（2026-09-08）

本节是当前计划的执行台账；不用 Git/工作树脚本，依据 AGENTS.md 在 Mac 本地直接编辑和测试。

| 任务或交叉接口 | 检查与决策 |
| --- | --- |
| 来源优先级与原 Task 1 | 用户新增规则优先：Wikipedia → TVDB → TMDB，只有不可用、空值、N/A 才回退，撤销同级来源打分/日期冲突清空。主来源有内容时不因下游更多集数或不同日期改选来源。 |
| 来源选择与范围筛选 | 保留一套来源季集坐标；完整坐标一致或两侧唯一稳定单集 ID 能确认同一集时补缺值，不拼入下游多出来的季集。日期缺失按同一优先级补齐；有效的未来日期不是缺值。 |
| Task 2 与 Task 3 | service 传入 exception=exc；logging 提供可选 exception 参数并使用既有 SDK Handler。各自独占文件避免覆盖。 |
| Task 2 与 Task 4 | service 回归与真实 Host 终态测试由同一实施者完成；root 负责来源、范围与最终全量验证。 |
| 验证与交付 | 无 Git、worktree、真实下载或自动发布。版本与计划在所有实现完成后统一更新。 |

Ruling: 来源策略限定于本次讨论的剧集库存、编号与播出日期，不扩大到作品身份、片名、海报等其他字段；用户注释指向季集库存回退。
Ruling: 既有总集数打分不能推翻有效的高优先级库存；不以补齐为名混入不同编号体系。代价是高优先级来源内容不完整时会保留其不完整状态。
Ruling: 采用 subagent-driven-development 的独立实施和评审流程；其 Git/工作树步骤由用户 AGENTS.md 的本地直接编辑边界替代，执行台账保留于本计划。

- 来源优先级与范围筛选：初始红灯 17 failed，修复及评审补充后 55 passed、22 subtests passed；独立评审通过。
- 失败收尾与 Host 回归：初始故障矩阵 5 failed、2 passed；范围日志、真实 Runtime 重复点击、内部排序错误分别先红后绿。
- 原始异常日志：红灯 2 failed、7 passed；修复后 9 passed，SDK diagnostics/logger 回归 12 passed；独立评审通过。
- 整体业务与代码集成评审：已通过；最后的提前终态 P2 修复后定向复核批准。

Ruling: 来源评审 P2 已采纳：完整库存不同但有唯一稳定单集 ID 桥接时，允许逐集日期/ID 补缺；不覆盖主坐标、不补另一套季度总集数。同坐标但明确单集 ID 相矛盾时拒绝补缺。新增两项红灯，修复后通过；另补主来源重复跨站 ID 不得清空已有日期的红绿回归。
Ruling: 真实 FeatureRuntime 重复点击测试发现提交任务被第二次点击取消并触发 duplicate_task；纳入既有“只投递一次”验收，以 selection_frozen guard 修复，download 实现不改。

Ruling: 跨编号体系的单集 ID 桥接须在主、补充来源两侧均唯一；主来源重复 ID 不妨碍保留其自身有效日期，但不能把下游一集日期补给多个主来源集。新增缺日期重复主 ID 测试先红后绿，定向复审通过。

## 实际改动文件

以下 17 个文件均位于 `/Users/young/Documents/telepiplex`；计划文件为新增，其余为修改，没有删除或重命名。

| 文件 | 实际目的 |
| --- | --- |
| `features/search/src/telepiplex_search/series_topology.py` | 固定来源优先级，仅补缺值，保留主编号，验证单集 ID 桥接 |
| `features/search/src/telepiplex_search/media_metadata_v1.py` | 在真实合同工厂接入三来源选择，统一读取有效日期 |
| `features/search/src/telepiplex_search/series_scope.py` | 全剧、整季与菜单和单集共用已播判定 |
| `features/search/src/telepiplex_search/service.py` | 正确消息段收尾、阶段和范围日志、重复提交保护 |
| `features/search/src/telepiplex_search/search_logging.py` | 原异常通过 exc_info 接入既有脱敏诊断链 |
| `features/search/tests/test_series_topology.py` | 验证来源优先级、缺值回退、不同编号及稳定 ID 边界 |
| `features/search/tests/test_media_metadata_v1.py` | 验证实际工厂的来源选择和日期回退 |
| `features/search/tests/test_series_scope.py` | 验证历史冲突仍可选，未来和未知仍受限 |
| `features/search/tests/test_feature_service.py` | 服务失败矩阵、完整 10 集链路、重复点击、版本断言 |
| `features/search/tests/test_search_logging.py` | 验证异常堆栈、原因链、会话上下文及脱敏 |
| `features/search/tests/test_config_schema_contract.py` | 补丁版本断言同步为 2.1.2，配置 schema 仍为 v2 |
| `tests/test_operation_pipeline_e2e.py` | 真实 Host 接受正确终态并清理按钮、释放所有权 |
| `tests/test_plugin_handler.py` | 两种到达顺序下旧 callback 均不能覆盖终态 |
| `features/search/manifest.yaml` | search 版本同步到 2.1.2 |
| `features/search/pyproject.toml` | 包版本同步到 2.1.2 |
| `features/search/README.md` | 说明来源回退、失败收尾、日志及重复点击行为 |
| `docs/superpowers/plans/2026-09-08-search-scope-failure-repair.md` | 修复计划、证据局限、执行决策与交付记录 |

Ruling: 最终集成评审发现结果交付前已记录 success/no_match，以及真实取消入口提前记录 completed 的同一 P2。必须在结果/取消上报结果确定后输出一次终态，防止 Host 结果交付失败仍被统计成成功且丢失会话上下文；已纳入本次失败收尾验收。来源、范围、段交接和投递冻结的其他集成路径评审未发现阻断。

## 最终本地验证与交付

最终代码稳定后，实际运行 Task 4 Step 3 中的两组命令：

- search 全量：`626 passed, 2 skipped, 111 subtests passed in 13.15s`。
- 根目录指定五份 Host/diagnostics/logger 回归：`156 passed, 33 subtests passed in 1.97s`。
- 两项 skipped 为需显式开启环境变量的公共来源联网测试；本次只运行离线回归，没有提交真实下载。
- 本地边界检查：`.git`、`.worktrees` 不存在，`.stfolder` 存在。
- `manifest.yaml` 与 `pyproject.toml` 均为 search `2.1.2`；配置 schema 保持 v2。
- 来源/范围、异常日志和最终服务集成已分别完成独立评审。来源 ID 唯一性和最终报告前提前记录终态的 P2 均先复现、再修复，并完成定向复核。
- 最后的报告顺序、真实取消和未启动重试取消回归先红后绿；对应定向回归为 `16 passed, 6 subtests passed`，随后纳入上述最终全量。

全部修改仅在 Mac 本地完成，没有运行 Git 或发布。等待 Syncthing 显示 `Up to Date / 最新`，再由用户在 Unraid `/mnt/user/archives/life hacker/telepiplex` 检查和发布 search 2.1.2，并重新运行 `/s 寡妇湾`。本地回归已确认修复行为；原始线上 ValueError 的唯一根因仍受原日志缺少堆栈限制，发布后需用新的真实运行日志确认。
