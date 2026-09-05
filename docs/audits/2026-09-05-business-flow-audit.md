# telepiplex 业务流复测与性能审计

日期：2026-09-05。工作区：`/Users/young/Documents/telepiplex`。

结论：现有测试全绿，但补充跨模块边界复现确认了 **3 个现役缺陷**。本轮只做复测、源码审查和问题复现，未修复业务源码；不能据此宣称线上问题已消失。

当前自动流程是 `search → 身份/范围确认 → 片源选择 → download → download.completed → rename → completed`。sync 的 `/scan`、`/sync`、`/sync_config` 是独立入口；rename 已不自动触发 Plex。caption 为占位实现。

## 已确认缺陷

### 1. [P1] sync 生成的按钮与注册命名空间不匹配

- 实际入口：`/scan`、`/sync_config`，以及既有 Plex 任务的人工选择按钮。
- `features/sync/manifest.yaml` 和 `runtime.py:20-28` 只注册 `sync`，但 `feature.py:292-316` 仍生成 `plex:scan:*`，`config_wizard.py:72-75` 仍生成 `plex:config:*`；选择按钮也使用 `plex:choice:*`。
- Host `app/handlers/plugin_handler.py:1533-1551` 严格验证按钮前缀。使用真实 Feature command 和直接导入的真实 `_keyboard_markup` 复现，两个入口均返回 `False`；`_render_actions` 会将其作为 `action_data_invalid` 拒绝。菜单无法正常交互。
- 为什么现有测试漏掉：Feature 测试验证旧按钮字面量，没有把真实 manifest 和 Host 校验串起来。
- 修复方向：统一现役按钮生成和解析为 `sync:`，同步修正残留 `/plex` 用法提示；增加真实 manifest → command → Host 校验 → callback 的回归。不要通过放宽 Host 校验来掩盖身份不一致。

实际复现输出：

```json
{"command":"scan","actual_host_accepts":false,"registered":["sync"],"generated":["plex:scan:all","plex:scan:12","plex:scan:cancel"]}
{"command":"sync_config","actual_host_accepts":false,"registered":["sync"],"generated":["plex:config:plex","plex:config:tmdb","plex:config:fanart","plex:config:cancel"]}
```

### 2. [P2] Telegram 编辑与封口竞态留下旧按钮

- 触发：消息段处于 `open` 时开始编辑；Telegram 请求未完成期间 Feature 请求 seal；编辑随后返回。
- `app/handlers/interaction_handler.py:1899` 使用旧 `open` 快照发送带按钮的编辑；`1915-1929` 读到最新 `sealing` 状态后直接完成封口，没有确认刚刚发送的按钮是否已清空。
- 使用真实 `OperationReportSink`、renderer 和 SQLite coordinator，仅替换 Telegram 传输并用事件控制顺序。结果：`accepted=true`、数据库 `sealed`，可见消息仍保留“取消任务”；清按钮调用为 0。另一个审计代理独立复跑得到相同结果。
- 影响：阶段已经结束但旧操作按钮残留。未证明它会绕过 Host 门禁执行错误取消，因此不按重复下载或数据破坏定性。
- 修复方向：只有本次编辑实际以空按钮发出，才可直接完成 seal；若 seal 在请求期间发生，补清当前消息按钮并确认，再持久化封口。保留正常 report+seal 合并的少请求路径。

```json
{"seal_accepted":true,"segment_state":"sealed","visible_callback":"host-operation:cancel:op-1","clear_markup_calls":0,"edit_calls":1}
```

### 3. [P2] 下载文件树静默截断，导致漏整理并最终失败

- `features/download/src/telepiplex_download/client.py:1039-1091` 的 `get_file_tree` 默认最多 1,000 个节点、深度 8，每个目录只读取一页；达到上限时静默返回部分结果，目录也计入节点预算。
- `download/service.py:1811-1844` 清理后再读同样受限的树，并在 `download.completed` 中原样发送；`rename/service.py:2107-2122` 未收到明确 `snapshot_complete=false` 就视为完整快照。
- 纯内存存储、真实树读取和整理执行复现：**1,001 个视频 → 只发现 1,000 个 → 实际整理 1,000 个 → 源目录遗留 1 个 → cleanup.complete=false → terminal failed**。
- 最后的根目录非空检查阻止了误报全部成功，见 `rename/file_executor.py:1717-1729` 和 `rename/service.py:2424-2433`；问题仍会造成文件被部分移动后任务失败。
- 手动 inventory 自行分页，未受此 1,000 全树上限影响。旧 `processor.collect_storage_file_tree` 也存在每目录单页读取，需要在修复时检查实际调用范围。
- 修复方向：完整分页、显式完整性标记，并在开始远端修改前确认快照完整。不能只提高 `limit`：RPC 默认 1 MiB，本轮模拟 10,000 个节点的 JSON 已约 2.28 MB；还需限制或拆分传输、使用快照引用，或在副作用前明确拒绝超限。

```json
{"actual_source_file_count":1001,"download_file_tree_count":1000,"rename_media_files_total":1000,"organized_files":1000,"remaining_source_files":1,"cleanup_complete":false,"derived_terminal_state":"failed"}
```

## 验证结果与边界

| 范围 | passed | subtests passed | skipped | 耗时 |
|---|---:|---:|---:|---:|
| Host / SDK 集成 | 617 | 218 | 1 | 90.63 s |
| search | 552 | 72 | 2 | 26.74 s |
| download | 117 | 31 | 0 | 1.36 s |
| rename | 355 | 8 | 0 | 6.12 s |
| sync | 141 | 64 | 0 | 3.00 s |
| caption | 1 | 0 | 0 | 0.03 s |
| 合计 | **1,783** | **393** | **3** | 各套件独立计时 |

实际运行命令，Feature 的五次调用合并展示如下：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --durations=20

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests --durations=15
  )
done
```

额外执行了真实 Unix RPC、Host 持久化与真实 PTB handler/默认串行 update queue 的组合压测；Feature 业务和 Telegram 服务端使用受控替身，未操作真实下载、远端文件或 Plex：

| 场景 | 实际结果 |
|---|---|
| 100 条 RPC 链路，并发 8，30 次里程碑故障注入 | 100 完成、30 恢复、0 失败；100 次 download.completed，最终 owner 全为 rename |
| 10 条串行任务，Telegram 50 ms，每任务点击 1 次 | 正确性通过；前台完成平均 508.303 ms、p95 540.323 ms；点击到 Feature p95 5.356 ms；10 次 Telegram API/任务 |
| 10 条串行任务，Telegram 50 ms，每任务点击 2 次 | 正确性通过；10 次重复点击被拒绝，无重复下载/整理副作用 |
| 5 条串行任务，busy 编辑 500 ms，每任务点击 3 次 | 正确性通过；10 次重复点击被拒绝，最终各段内容可见；前台完成 p95 697.763 ms |

上述 Telegram 场景关闭后 task 与文件描述符增量均为 0。压测通过不覆盖第 2 项的特定在途编辑/封口交错，该缺陷由额外最小复现发现。

压测实际参数：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_operation_pipeline.py --pipelines 100 --concurrency 8 --milestone-faults 30

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_telegram_pipeline.py --pipelines 10 --concurrency 1 \
  --frontend-mode queue --telegram-latency-ms 50 --duplicate-clicks 1 --timeout-seconds 60
# 同参数再运行 duplicate-clicks=2；慢反馈场景为 pipelines=5、
# duplicate-clicks=3，另加 --busy-latency-ms 500。
```

本地没有 `/config/config.yaml` 或生产会话日志；没有验证线上 Telegram、115、Prowlarr、Plex 的实时状态和安装版本。3 个跳过项涉及需主动配置的 live 检查和提供发布工件的矩阵检查，不能算通过。

默认 search 测试还存在网络隔离缺口：`test_work_discovery.py:1128`、`:1380` 和 `test_direct_link.py:335` 漏替换附属 provider。额外拦截 `requests.Session.request` 后捕获 **6 次公共网络请求尝试，3 个测试仍通过**。首次完整 suite 中这三项共耗时 13.05 s。因此本轮默认测试不能统称为完全离线；后续应补齐 provider 替身，并让默认测试意外访问网络时显式失败。

现有 `features/search/tools/run_live_pipeline_audit.py --all-full` 也不代表真实业务全链：`live_pipeline_audit.py:464-523` 直接运行补全、hydrate 和 v1 roundtrip，与生产的身份确认顺序及 v2 handoff 有偏差，而且将部分安全拒绝计为通过。本轮没有把该工具的 full-pass 当成业务完成证据，也未执行其 live 请求。

## 性能与不必要的重复工作

**搜索侧优先优化候选展示前的外部等待。** `work_discovery.py:276-344` 先 Wikipedia → Wikidata 实体，再按片名查 Wikidata；`adapters/wikidata.py:281` 的中英查询串行；`service.py:4955-4966` 最后等待 Douban 本地化才返回候选。

真实 `_build_plan` 配合受控 provider 延迟，得到 `50 + 50 + 50 + 50 + 150 ms` 的串行时间线，合计实测 359.83 ms。两个相同查询并发时，实际 Wikidata title-search adapter 中英各请求两次，Douban 已合并为一次。**这是人工延迟实验，证明顺序与调用次数，不代表线上耗时。** 建议在保留同名作品和双语召回的前提下，提前并行独立的 title-search，并在有重入需求的根发现路径合并相同请求；给候选本地化设置整体等待预算。不要简单删掉一个信息源。

**本轮本地模拟链路中，Host 调度未成为主要等待。** 单任务点击到 Feature p95 为 5.356 ms，事件排队 p95 为 4.987 ms。50 ms Telegram 模型下每任务 10 次 API；现有“最多 9 次”回归只使用所有延迟归零的场景，未覆盖存在模拟网络延迟时的调用预算。后续应补有延迟的验收，再评估减少哪一次投影；不建议先大改 RPC 或放开同用户多任务并发。

**Plex 既有任务恢复有重复全库定位和同作品查询。** 通过现役 MCP `plex_retry_job` 的 prepare/apply 路径恢复一个失败 Job，使用模拟时钟，默认 300 s 窗口触发 61 次全库定位；5,000 条媒体 × 50 个目标产生 250,000 次路径比较/轮。24 集增强重复读取同一个 show 24 次、同一个 TMDB details 24 次。适合使用一次索引匹配多个路径、批次内作品缓存，并对索引刷新退避。这个成本属于既有增强 Job 恢复，独立 `/scan` 不执行这些定位与增强步骤。

**整理的安全校验不能当绕路直接删除。** 1,000 文件复现实执 32 批 native move、134 次目录列表请求；批次前后的源/目标验证承担收敛与防冲突职责。download 即使没有不合格文件也会读取两遍整树，可进一步评估快照复用，但必须保留下载完成、完整性和远端变化的证据。现有 10,000 文件 mock 整理清理压力测试为 1.96 s，文件名解析为 1.62 s，不能当远端性能指标。

## 遗留路径与修复顺序

sync 仍留有 `media_organized` 和整套增强 Job 实现，但 runtime 的 `events={}`、manifest 的 `subscribes=[]`，`library.sync` 只暴露 get_job/list_jobs。旧事件按 metadata_id 合并不同批次的问题不属于当前下载自动流程。旧任务恢复还复现了字幕末阶段取消后继续写入并完成的问题；应在处理旧任务兼容时单独修复或下线，不能通过删除仍被 MCP retry 使用的代码直接清理。

建议顺序：先修 sync 按钮与 Host 封口竞态，再修文件树完整性和传输边界；补足相应跨模块回归及默认测试网络隔离后，优化搜索请求等待；Plex 旧任务恢复的缓存与退避单独处理。

本轮补充复现文件仅在 `/tmp`，为当前 Mac 临时证据，不作为 Syncthing 交付物：

- `telepiplex-audit-20260905-seal.py`、`seal-output.json`
- `telepiplex-audit-20260905-tree-repro.py`、`tree-output.json`
- `telepiplex-audit-20260905-sync-repro.py`、`sync-repro.stdout`
- `telepiplex-audit-20260905-search-network-isolation.py`、对应 `.json`
- `telepiplex-audit-20260905-search-query-path.py`、对应 `.json`
- `telepiplex-audit-20260905-rpc.json`、`baseline.json`、`single.json`、`slow-busy.json`

项目内只新增本报告。未修改、删除或重命名业务源码；未进行 Git、远程发布或 Unraid 操作。交付时等待 Syncthing 显示 `Up to Date / 最新`，同步至 `/mnt/user/archives/life hacker/telepiplex`。
