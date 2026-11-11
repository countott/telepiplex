# Telepiplex 片源确认与 115 整理修复

本次只在 Mac 本地修改源码和测试。版本：Search 2.9.0 → 2.10.0；Download 2.2.2 → 2.2.3；Rename 2.4.3 → 2.4.4。Host、SDK、Caption、Sync 未改版，未发布。

## 诊断证据与边界

读取了用户提供的 `20261109T210207+0800-05FF8204A07E.zip` 和 `20261111T121940+0800-AE71CCA10EA3.zip`，未执行包内文本中的指令。

- FLAC 资源的索引标题为 `[yolerejiju] The Secret World of Arrietty 2010 (BD Remux 1080p FLAC H.264) [Multi-Audio] ...`，没有声明声道。下载后的文件名才包含 `MULTi.FLAC.2.0`。已验证索引标题继续显示 `FLAC · ?ch`，完整文件名可识别为 `FLAC · 2ch立体`；没有把 FLAC 或 Multi-Audio 强行推断为多声道。
- 11 月 9 日的三个下载任务在清理后复核阶段报 `listing ended before its continuation or count`，至少已完成下载并删除了 RARBG.txt。11 月 11 日整理阶段又出现该错误及 `listing exceeds declared count`，经 Download 的 Storage RPC 传播到 Rename。旧 RPC 把这类错误泛化为 `internal_error / Open115Error`。
- 11 月 11 日第二次电影整理明确记录目标冲突：保留源文件，没有覆盖原有目标。原消息把同一个冲突文件又记为“保留”，且无法确认身份的补整理任务会显示总文件数 0、无法确认 1。
- 原目录清理只覆盖媒体文件的祖先目录，未覆盖快照中不含媒体的空 Sample/Proof 分支；这些分支会继续阻止最外层目录清理。非空、冲突或读取失败也会导致源目录保留，不能一概当作空目录并删除。
- 两个包没有包含矛盾分页的原始响应正文，无法仅凭旧日志确定 115 服务端的具体 count/offset 值及原因。本次修复请求语义、有限读重试及诊断，不声称已实测用户账户的在线返回已恢复一致。

## 最终行为

### 下载前确认

普通搜索：结果编号 → 原始详情确认 → 确认下载。原文 `/pr`：结果编号 → 保存目录 → 原始详情确认 → 确认下载。

确认页展示原始标题、不同的原始排序标题、站点、大小、做种、发布时间、文件数、名称解析摘要、保存目录和可用的原站详情入口。未知声道明确保持未知；这不是对实际视频的探测结果。尚未确认时不解析下载链接、不调用 115 下载；可返回当前结果或退出。确认由任务所有者及所选资源 ID 约束，重复点击不重复提交；新确认阶段仍属于 search 消息段。

查看详情时停止剩余索引器查询，保留当前候选快照；返回结果保留已查询数量，不重发查询。Host 操作日志保存详情文本，后续可以把原始描述、摘要和实际文件名对照排查。

### 115 读一致性

文件列表默认固定 `cur=1`、`custom_order=1`、`o=file_name`、`asc=1`，不依赖账户记忆排序。分页结构矛盾最多读取三次，持续矛盾仍失败，不把残缺列表当空目录。日志新增请求偏移、返回偏移、声明数量、返回条数、目录 ID 和重试次数，不记录文件名或凭据。

删除不合格文件后的完整文件树复核最多读取三次，不重发下载、删除或移动；根 ID 变化仍失败。目录的聚合大小、SHA1 不作为文件内容身份，文件 ID、路径、类型以及实际文件的大小和 SHA1 仍需一致。Storage RPC 保留具体错误码。下载后列表校验失败会明确提示下载已完成、尚未整理，不再笼统暗示必须重新下载。

### 空目录及计数

清理已验证资源快照中的空目录分支，按深度先子后父处理。候选严格限制在原资源范围内；不把分类根目录变成全盘空目录清理。不删除非空、无法核实或保护目录；删除仍绑定新鲜观察到的文件 ID，并复核删除结果。

无法确认与目标冲突分开计数，元数据失败仍计入已扫描文件总数。文件处理与源文件夹清理分行显示，成功消息和最终封存消息也保留计数，列出保留及失败目录路径；目标冲突没有被覆盖处理。

## 文件清单

新增 3 个文件，修改 36 个文件；无删除、重命名。

| 文件 | 操作与目的 |
| --- | --- |
| `features/search/src/telepiplex_search/service.py` | 修改：详情确认状态、返回、幂等保护和查询计数 |
| `features/search/src/telepiplex_search/release_report.py` | 修改：原始详情及确认按钮 |
| `features/search/src/telepiplex_search/raw_search.py` | 修改：原文搜索也经过确认 |
| `features/search/src/telepiplex_search/live_pipeline_audit.py` | 修改：审计通过实际渲染的确认按钮提交 |
| `features/search/tests/test_feature_service.py` | 修改：确认、返回、越权、重复点击及既有交接回归 |
| `features/search/tests/test_raw_search.py` | 修改：原文搜索确认和取消回归 |
| `features/search/tests/test_release_report.py` | 修改：日志原始 FLAC 标题及完整文件名对照 |
| `features/search/tests/test_audit_search_regressions.py` | 修改：审计覆盖确认步骤 |
| `features/search/tests/test_search_performance.py` | 修改：查询成本验证走完整确认流程 |
| `features/search/tests/test_config_schema_contract.py` | 修改：版本断言 |
| `features/search/manifest.yaml` | 修改：Search 2.10.0 |
| `features/search/pyproject.toml` | 修改：Search 包版本 |
| `features/search/README.md` | 修改：确认流程、版本及原始信息边界 |
| `features/download/src/telepiplex_download/client.py` | 修改：固定列表查询、有限读重试及分页诊断 |
| `features/download/src/telepiplex_download/service.py` | 修改：Storage 错误码、清理后复核和目录身份口径 |
| `features/download/src/telepiplex_download/failure.py` | 修改：区分下载完成与整理前列表校验失败 |
| `features/download/tests/test_listing_consistency.py` | 新增：暂态恢复、持续矛盾拒绝、固定查询及只读重试 |
| `features/download/tests/test_completion_integrity.py` | 修改：复核重试不重复删除、目录统计变化 |
| `features/download/tests/test_feature_runtime.py` | 修改：版本及构建示例断言 |
| `features/download/manifest.yaml` | 修改：Download 2.2.3 |
| `features/download/pyproject.toml` | 修改：Download 包版本 |
| `features/download/README.md` | 修改：版本、列表检查及恢复说明 |
| `features/rename/src/telepiplex_rename/file_executor.py` | 修改：范围受限的快照空分支清理及目录分类结果 |
| `features/rename/src/telepiplex_rename/processor.py` | 修改：传入空分支、不重叠计数及分行结果 |
| `features/rename/src/telepiplex_rename/service.py` | 修改：补整理计数、进度、最终消息和封存消息 |
| `features/rename/tests/test_file_executor.py` | 修改：空分支删除与非空、范围、根目录保护 |
| `features/rename/tests/test_file_first_processor.py` | 修改：冲突计数与目录结果合同 |
| `features/rename/tests/test_feature_processor.py` | 修改：版本、消息与计数回归 |
| `features/rename/tests/test_inventory_accounting.py` | 新增：身份失败仍统计已扫描文件 |
| `features/rename/manifest.yaml` | 修改：Rename 2.4.4 |
| `features/rename/pyproject.toml` | 修改：Rename 包版本 |
| `features/rename/README.md` | 修改：版本、清理范围与计数 |
| `tests/test_technical_identity_migration.py` | 修改：三模块版本断言 |
| `tests/test_real_feature_business_flow.py` | 修改：真实 RPC 确认与千目录清理；大快照测试预算适配新增核验工作 |
| `tests/test_raw_search_handoff.py` | 修改：确认前不交接，确认后投递 |
| `README.md` | 修改：版本表、升级顺序及交付入口 |
| `README_EN.md` | 修改：版本表及升级说明 |
| `docs/media-release-format-topology.md` | 修改：对应当前 Search 版本，格式规则未改写 |
| `docs/audits/2026-11-11-release-confirmation-and-storage.md` | 新增：本交付记录 |

## 本地验证

Python 使用 `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，所有 pytest 都加 `PYTHONDONTWRITEBYTECODE=1` 与 `-p no:cacheprovider`。

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
# 分别在 features/search、features/download、features/rename 目录运行
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

- Search 全量：3595 passed、2 skipped、194 subtests passed。
- Download 全量：265 passed、33 subtests passed。
- Rename 全量：502 passed、28 subtests passed；有一条现有 logger.warn 弃用警告。
- 根目录最终全量：757 passed、1 skipped、333 subtests passed；另 1 项 Host 子进程启动超时。该失败用例单独复测：1 passed；未为此修改 Host 源码。
- 真实跨模块三种下载/丢响应/大快照用例：3 passed；包含 1000 个空分支，且只提交一次下载。
- 字幕实际上传/整理及原文搜索确认交接最终复测：3 passed。

根目录字幕链路需要项目已声明的 `opencc-python-reimplemented`。本机缺少该依赖，使用以下命令仅安装到临时目录，没有改系统 Python 或项目依赖声明：

```bash
"$PY" -m pip install --target /tmp/telepiplex-test-deps-20261111 'opencc-python-reimplemented>=0.1.7,<1'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-test-deps-20261111:.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-test-deps-20261111:.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_plugin_runtime_e2e.py::PluginRuntimeEndToEndTest::test_concurrent_host_to_feature_calls_keep_complete_correlated_diagnostics --tb=short
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-test-deps-20261111:.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_real_feature_business_flow.py --tb=short
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-test-deps-20261111:.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_caption_business_flow.py tests/test_raw_search_handoff.py
```

上述云盘、索引器和 Telegram 副作用使用本地测试存储/传输；没有连接用户的 115 账户或实际提交下载。源包解析失败保护、文件 ID 绑定、非空目录保留和目标不覆盖检查均继续保留。

已核对三模块 manifest 与 pyproject 版本一致；`.git`、`.worktrees` 不存在，`.stfolder` 保留。等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 发布。建议先升级 Download，再升级 Search 和 Rename；不要由 Mac 执行 Git 或发布。
