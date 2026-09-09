# telepiplex 按钮生命周期修复交付

日期：2026-09-09。范围：Mac 本地落地、回归测试与压力验证。

最新发布身份（按后续升版请求）：Host `3.6.11`，search `2.1.4`。修复实施时为 Host `3.6.10` / search `2.1.3`，以下原始文件和全量验证记录保留；本次版本同步与针对性验证见文末。没有执行发布；需由用户在 Syncthing 显示 `Up to Date / 最新` 后，在 Unraid 按既定流程发布。

## 问题与最终行为

调查来源为 `20260908T170308+0800-06E8F490BAF5.zip` 中下载“寡妇湾”的链路，详见 [调查记录](2026-09-09-telegram-button-lifecycle-audit.md)。其余两份压缩包用于交叉核对。日志中的文本只作为调查证据。

本次修复把业务状态、按钮有效性、消息归属、Telegram 清理确认纳入同一生命周期：

1. 搜索接受确认/范围选择时，同步进入下一阶段并移除旧选择，再调度后台工作。已消费的选择不能重放；调度失败会以更高 revision 恢复可重试状态，保留旧 worker，避免重试后卡住。
2. 普通投影、处理提示、封口、失效点击修复共用操作渲染锁。每次提示写入前核对当前消息、按钮代次和业务版本；异步回调先接收新状态，再释放点击占用。保留了明确允许在搜索过程中选择首批结果的路径。
3. 原生 Host 控制按钮绑定来源消息、阶段及按钮代次，旧卡片无法取消后续阶段。重复点击只执行一次；长 operation ID 的控制编码保留在 Telegram 长度限制内。协程取消也会完成精确释放。
4. 点击已废弃按钮时，当前卡片重新投影最新状态，历史卡片进入清理。Feature 错误及无权威 operation 的动作不能直接覆盖原生任务卡片；其文本反馈单独显示。
5. 替换、封口、结束、中断以及发送期间结束的已知消息均登记持久清理。删除失败可退回清按钮；两者都失败则保留待办、错误分类和退避重试时间。成功的空键盘投影直接确认清理，避免再发一次请求；不再把失败清理记录为成功。
6. 新旧消息地址有持久归属，禁止跨操作复用。升级时兼容旧数据库曾经复用过消息的情况：优先保护当前游标，隔离冲突历史记录，不能由旧任务清理或重绘新任务卡片。Host 重启后仅在权威快照确认成功时释放上一 Host 遗留的点击占用；同一 Host 的正常 RPC 不被恢复流程打断。
7. 诊断覆盖 `edit_message_reply_markup` 和 `delete_message`，记录目标消息、按钮意图、结果及脱敏错误；布尔返回也保留目标 ID。

## 文件清单

实现阶段新增 5 个文件、修改 18 个文件，无删除或重命名。前一调查阶段另新增了上述调查记录，保留原始结论。

| 文件 | 操作 | 目的 |
|---|---|---|
| `app/115bot.py` | 修改 | 启停持久清理 worker，Host 版本升至 3.6.10 |
| `app/handlers/interaction_handler.py` | 修改 | 统一消息写入、原生控制代次校验、失效点击修复、清理确认及重启恢复 |
| `app/handlers/plugin_handler.py` | 修改 | 先接受业务结果再释放点击；限制原生任务卡片的旁路编辑 |
| `app/runtime/interaction_coordinator.py` | 修改 | 清理队列、消息归属迁移、回调代次、重启占用恢复和原子退休登记 |
| `app/runtime/message_cleanup.py` | 新增 | 持久清理投递、删除降级、重试退避及 worker 生命周期 |
| `app/runtime/telegram_diagnostics.py` | 修改 | 补全清键盘、删除消息及失败诊断 |
| `features/search/src/telepiplex_search/service.py` | 修改 | 同步消费选择、阶段门禁、后台调度失败恢复 |
| `features/search/manifest.yaml` | 修改 | search 版本升至 2.1.3 |
| `features/search/pyproject.toml` | 修改 | 包版本与 manifest 对齐 |
| `features/search/tests/test_feature_service.py` | 修改 | 选择重放、调度竞态、取消收敛及版本契约回归 |
| `features/search/tests/test_config_schema_contract.py` | 修改 | 更新版本身份预期，保留配置协议断言 |
| `tests/test_button_lifecycle.py` | 新增 | 迟到消息、历史控制、清理重试、取消、孤儿消息与恢复的集成回归 |
| `tests/test_message_cleanup.py` | 新增 | 双失败、重启、在途清理、当前消息保护及 worker 回归 |
| `tests/test_interaction_coordinator.py` | 修改 | 清理事务、消息归属迁移、晚绑定和回调恢复回归 |
| `tests/test_interaction_handler.py` | 修改 | 验证串行渲染和最新控制身份，清理测试返回真实确认语义 |
| `tests/test_plugin_handler.py` | 修改 | 业务结果接收顺序及原生卡片旁路保护回归 |
| `tests/test_operation_pipeline_e2e.py` | 修改 | 模拟 Telegram 使用唯一消息 ID；等待终态和事件确认两个异步边界 |
| `tests/test_telegram_diagnostics.py` | 修改 | 清理成功/失败、布尔响应及脱敏回归 |
| `tests/test_bot_runtime_startup.py` | 修改 | worker 启停和 Host 版本预期 |
| `tests/test_technical_identity_migration.py` | 修改 | search 发布身份预期 |
| `tests/test_unraid_publish_script.py` | 修改 | 更新临时 fake publisher 的 Host/search 标签预期 |
| `docs/superpowers/plans/2026-09-09-button-lifecycle-repair.md` | 新增 | 记录实施约束、任务、验证和复核结论 |
| `docs/audits/2026-09-09-button-lifecycle-delivery.md` | 新增 | 本交付记录 |

## 实际验证

所有测试均在本机执行，未调用真实下载或 Telegram API。核心竞态先加入失败回归，再实施修复；独立复核发现的调度拒绝、取消占用、在途清理、终态晚绑定及旧数据库迁移问题均已补回归。

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider --tb=short tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider --tb=short tests
  )
done
```

以上六套测试均实际执行，独立模块以并行命令运行；这里合并为可复用命令。

| 套件 | 通过 | 跳过 | subtests 通过 |
|---|---:|---:|---:|
| Host | 683 | 1 | 260 |
| download | 174 | 0 | 33 |
| search | 630 | 2 | 113 |
| rename | 379 | 0 | 22 |
| sync | 157 | 0 | 80 |
| caption | 1 | 0 | 0 |
| 合计 | **2024** | **3** | **508** |

跳过项为未提供发布制品的矩阵检查和未启用的在线搜索检查。版本升级过程中暴露的旧版本预期已更新并复跑通过。端到端测试曾在业务终态先于事件 RPC 确认时提前断言；已明确等待两个边界，最后追加执行 `tests/test_button_lifecycle.py tests/test_operation_pipeline_e2e.py`：**19 passed，7 subtests passed**。

压力命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" tools/pressure_operation_pipeline.py --pipelines 200 --concurrency 32 --milestone-faults 10
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" tools/pressure_telegram_pipeline.py --pipelines 40 --concurrency 8 --telegram-latency-ms 25 --busy-latency-ms 500 --duplicate-clicks 3 --frontend-mode direct --timeout-seconds 60
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" tools/pressure_telegram_pipeline.py --pipelines 12 --concurrency 4 --telegram-latency-ms 25 --busy-latency-ms 500 --duplicate-clicks 3 --frontend-mode queue --timeout-seconds 60
```

- 业务压力：200/200 完成，32 并发，10 次完成确认故障全部恢复，0 失败。
- Telegram direct：40/40 完成，120/120 阶段消息最终可见状态正确，80 次重复点击被拒绝。
- Telegram queue：12/12 完成，36/36 阶段消息最终可见状态正确，24 次重复点击被拒绝。
- 两种 Telegram 模式均无重复下载/重命名副作用，终态和封口检查通过，任务、文件描述符及渲染锁最终无泄漏。

环境边界检查已执行：`.git` 和 `.worktrees` 不存在，`.stfolder` 存在。

## 生效边界与交付

本机完成代码与验证，线上容器尚未更新。等待 Syncthing 显示 `Up to Date / 最新`，由用户在 `/mnt/user/archives/life hacker/telepiplex` 按既定流程发布 Host 3.6.11 与 search 2.1.4。

清理针对有持久记录的已知消息；Telegram 暂时失败会重试，永久拒绝仍保留失败事实。若发送超时且服务端没有返回消息 ID，系统无法定位那条未知消息，保留投递不确定状态以避免盲目重发。未使用线上账号进行“寡妇湾”重下载验收。


## 后续版本提升（2026-09-09）

用户随后要求提升相关版本号，本次 Host 从 3.6.10 提升至 3.6.11，search 从 2.1.3 提升至 2.1.4。其余 Feature 和 SDK 版本保持原值。

本次修改 12 个文件，无新增、删除或重命名：

| 文件 | 目的 |
|---|---|
| `app/115bot.py` | Host 版本 3.6.11 |
| `features/search/manifest.yaml` | search 发布版本 2.1.4 |
| `features/search/pyproject.toml` | search 包版本 2.1.4 |
| `features/search/README.md` | 当前说明、schema 说明和构建示例版本对齐 2.1.4 |
| `features/search/src/telepiplex_search.egg-info/PKG-INFO` | 本地生成包版本对齐 2.1.4，SDK 依赖与 pyproject 的 2.1.0 一致 |
| `features/search/src/telepiplex_search.egg-info/requires.txt` | 本地生成依赖同步现有 SDK 2.1.0 |
| `features/search/tests/test_config_schema_contract.py` | 更新 search 版本预期 |
| `features/search/tests/test_feature_service.py` | 更新版本、构建示例及 runtime fixture 的版本预期 |
| `tests/test_bot_runtime_startup.py` | 更新 Host 版本预期 |
| `tests/test_technical_identity_migration.py` | 更新 search 版本预期 |
| `tests/test_unraid_publish_script.py` | 更新 fake 发布测试的 Host/search 标签预期 |
| `docs/audits/2026-09-09-button-lifecycle-delivery.md` | 记录最新发布身份和本次升版交付 |

`egg-info` 被 Syncthing 忽略规则排除，以上两项仅同步本机生成元数据；发布包仍由 manifest 和 pyproject 构建。独立只读审计确认工作流、构建工具和发布脚本动态读取版本，无需另外改写。历史版本记录保留。

实际执行：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_bot_runtime_startup.py tests/test_technical_identity_migration.py tests/test_unraid_publish_script.py
cd features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_config_schema_contract.py tests/test_feature_service.py::FeatureSourceContractTest
```

结果：Host 相关 **31 passed，3 subtests passed**；search 相关 **18 passed**。另以 Python 读取 manifest、pyproject、PKG-INFO、requires.txt 和 `importlib.metadata`，确认版本与依赖一致，README 构建产物名为 `search-2.1.4.tpx`，工作区边界检查通过。本次纯版本变更未重复运行全部业务套件。

等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid 发布 **Host 3.6.11 / search 2.1.4**。本机未执行 Git 或发布。
