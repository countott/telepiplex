# Telepiplex 115 日志核对与分页终止修正

本轮使用三份新增诊断 ZIP，并对照前两份日志与本地源码。只把日志作为证据，没有执行包内内容，没有连接用户的 115 账户，没有进行 Git 或发布。

## 已确认的故障链

日志运行版本是 Download 2.2.2、Rename 2.4.3，不是此前本地尚未发布的版本。不能把当前源码的诊断字段当成旧日志已经记录的事实。

- `20261105T061350+0800-16A2193CD702 (1).zip`：Download/Rename 各只有 3 条生命周期记录，没有 115 下载整理业务。该包搜索中的 TMDB/TVDB 鉴权失败、Prowlarr 401 不属于后面的 115 故障。
- `20261106T101247+0800-345400F80E65.zip`：The Wire 五季的 60 个视频完成下载；60 次改名成功，5 批移动成功提交。目标列表回读已能逐一找到全部相同文件 ID，父目录是目标目录，大小和 SHA1 字段与移动前一致。最终却显示失败 60，原因是后续完整列表核验没有通过。
- `20261106T115703+0800-3D97D450DCC6.zip`：另两个任务的 13 和 8 个文件同样已出现在目标目录，随后分页核验失败，最终记为失败 13 和 8。

这些证据证明文件在所记录的目标回读时点已经改名并移动，不能把“失败 60/13/8”解释为文件都没有移动；它们没有完成整个整理流程的严格核验。SHA1 是 provider 返回字段，未实读视频重新计算。

### 60 个文件的可复核证据

以下行号均为 `20261106T101247+0800-345400F80E65.zip` 内 `feature-download.machine.jsonl` 的 1-based 行号，时间为 +08:00。

| 季 | 移动前源列表 | 移动回执 | 目标列表 | 文件数 | 目标回读时间 |
| --- | --- | --- | --- | --- | --- |
| 1 | 110 | 900 | 907 | 13 | 11:27:00.755 |
| 2 | 118 | 926 | 929 | 12 | 11:27:02.201 |
| 3 | 126 | 952 | 955 | 12 | 11:27:04.021 |
| 4 | 134 | 978 | 981 | 13 | 11:27:05.722 |
| 5 | 142 | 1003 | 1010 | 10 | 11:27:07.698 |

逐项比对：源 fileID 集合 = 移动输入 fileID 集合 = 目标 fileID 集合；目标每个 `pid` 都指向对应目标目录；`fs` 和 `sha1` 字段一致。60 个文件名已为规范的 `The Wire SxxExx.mkv`。

第一季时间线：

1. 887–893 行：移动前源目录第一页有 13 条/count13，offset13 空尾页仍 count13，正常。
2. 894–897 行：移动前目标目录为空/count0，正常。
3. 898–900 行：13 个文件的批量移动返回 submitted。
4. 901–903 行：移动后源目录 offset0 报 `listing ended before its continuation or count`。
5. 904–907 行：目标第一页有全部 13 个文件/count13，ID 与父目录均正确。
6. 908–911 行：仍请求目标 offset13，即已经到达已知总数，随后报 `listing exceeds declared count`。

其余四季重复该模式。五次源 offset0 错误为 903、937、963、989、1006 行；五次目标额外尾页错误为 911、933、959、985、1014 行，offset 分别为 13、12、12、13、10。

后一个 ZIP 的同类证据：13 文件的移动与目标列表为 download machine 291–300 行，额外 offset13 请求及失败为 301–304 行；8 文件为 487–492 行，额外 offset8 请求及失败为 493–496 行。对应 rename machine 209、303 行记录最终失败统计。

## 三类问题须分开

### 1. 本地分页终止缺陷，已修正

SDK 树扫描、Rename 的移动核验目录枚举、补整理库存枚举，在已经读满 `count` 时仍要求额外空尾页。这是可以从源码、成功分页和下一次 RPC 请求共同证明的本地缺陷。

本轮改为：先完成响应包装、计数、偏移、文件身份去重及必要递归等原有验证；累计条数等于已声明总数时结束。无总数时不因短页结束，继续读取。已知总数变更、缺页、超额、重复 ID、continuation 矛盾和容量限制继续拒绝；不增加重试。独立审查发现末页省略 count 时仍需对照前页 count 验证 has_more；已在 Download 扫描适配层补齐，两项失败回归先复现后通过，避免计数终止吞掉矛盾。

参考实现也按 `count <= offset + len(data)` 终止：[p115client 维护者分页实现](https://github.com/ChenyangGao/p115client/blob/main/p115client/tool/fs_files.py#L272)。这不是以忽略异常代替完整性校验。

### 2. 移动后源目录列表矛盾，未确认底层原因

源目录 offset0 的错误发生在移动后，不能由“额外尾页”解释。后一个 ZIP 的 download machine 305–312、501–508 行显示源目录详情已经 count0/size0，但列表确认仍报矛盾；前一个 ZIP 稍后 1018/1022、1034/1038 行又能读到空目录/count0。

失败响应本身没有记录原始 `data/count/offset/has_more`，无法最终区分服务端变更后计数不一致、筛选参数或协议适配问题。不能把“115 暂态延迟”当成已证明结论。当前源码此前已增加分页事实诊断，但本轮不再补偿重试或放宽空目录删除。

本轮修正仅消除多读尾页这一条确定失败路径。源目录 offset0 矛盾仍可能阻断严格移动核验和空目录清理，不能保证旧任务因此全部恢复。

### 3. 770004 是独立的 API 拒绝

前一个 ZIP 的 download machine 1023–1027 行：第一季空源目录删除成功。1039–1043 行：第二季删除返回 770004。1044–1059 行：第三、四、五季及源根目录身份查询相继返回同码。

对应 HTTP 均为 2xx，但业务码不是 0，操作并未成功。日志没有原始 provider message，不能直接把 770004 定性为限流。它发生在分页异常之后，不是前述十次分页异常的统一解释。该错误使删除/身份核实失败，源文件夹按保护规则保留。

此外，后一个 ZIP 的 `probe_inventory_mismatch missing=all` 是早期身份确认问题；Telegram get_updates 的网络异常属于机器人轮询；40140125 后刷新成功属于已恢复的授权过程。均不能混入同一个 115 分页根因。

## 本次版本

| 组件 | 本次版本 | 原因 |
| --- | --- | --- |
| SDK | 2.2.2 | 树扫描 count 终止 |
| Download | 2.2.5 | 打包修正后的 SDK |
| Rename | 2.4.5 | 两处分页终止及 SDK 对齐 |
| Search | 2.11.1 | 精确 SDK 依赖与不可变包版本对齐，业务代码未改 |
| Sync | 2.1.2 | 同上 |
| Caption | 1.3.4 | 同上 |

Host 仍为 3.9.3。所有 Feature 都精确锁定 SDK，构建又使用同一 SDK 源目录，因此同步修改依赖和包版本，避免新 SDK 与旧精确依赖不匹配。SDK 随包，无需用户单独安装。

## 本轮文件清单

新增 3 个，修改 36 个，无删除、重命名；相对于上一轮交付的增量如下。

| 文件 | 操作与目的 |
| --- | --- |
| `sdk/src/telepiplex_plugin_sdk/storage_tree.py` | 修改：读满已声明 count 后结束，先完成子目录递归 |
| `tests/test_storage_tree_termination.py` | 新增：两个扫描器的终止、递归及完整性回归 |
| `features/download/src/telepiplex_download/storage_listing.py` | 修改：末页省略 count 时也验证 has_more 与先前总数一致 |
| `features/download/tests/test_tree_integrity.py` | 修改：上述跨页矛盾在当前页拒绝的回归 |
| `features/rename/src/telepiplex_rename/directory_listing.py` | 修改：目录核验 count 终止及跨页 continuation 矛盾保护 |
| `features/rename/src/telepiplex_rename/service.py` | 修改：仅库存枚举采用相同终止规则 |
| `features/rename/tests/test_directory_listing_safety.py` | 修改：完整总数、跨页和未知总数保护回归 |
| `features/rename/tests/test_inventory_directory_integrity.py` | 修改：异步库存终止及恶意分页回归 |
| `features/rename/tests/test_regression_pressure.py` | 修改：调用数扣除已省掉的尾页请求，保留业务结果断言 |
| `sdk/pyproject.toml` | 修改：SDK 版本 |
| `features/download/manifest.yaml` | 修改：Download 版本 |
| `features/download/pyproject.toml` | 修改：包版本和 SDK 精确依赖 |
| `features/rename/manifest.yaml` | 修改：Rename 版本 |
| `features/rename/pyproject.toml` | 修改：包版本和 SDK 精确依赖 |
| `features/search/manifest.yaml` | 修改：Search 版本 |
| `features/search/pyproject.toml` | 修改：包版本和 SDK 精确依赖 |
| `features/sync/manifest.yaml` | 修改：Sync 版本 |
| `features/sync/pyproject.toml` | 修改：包版本和 SDK 精确依赖 |
| `features/caption/manifest.yaml` | 修改：Caption 版本 |
| `features/caption/pyproject.toml` | 修改：包版本和 SDK 精确依赖 |
| `examples/echo_feature/pyproject.toml` | 修改：活跃构建示例 SDK pin |
| `tests/test_deployment_contract.py` | 修改：SDK 当前版本断言 |
| `tests/test_technical_identity_migration.py` | 修改：五模块版本与 SDK 版本断言 |
| `tests/test_feature_builder.py` | 修改：缺失 SDK 用例从新 pin 制造不匹配依赖 |
| `features/download/tests/test_feature_runtime.py` | 修改：当前包版本、SDK 与构建示例断言 |
| `features/rename/tests/test_feature_processor.py` | 修改：同上 |
| `features/search/tests/test_feature_service.py` | 修改：同上 |
| `features/search/tests/test_config_schema_contract.py` | 修改：当前 Search 版本断言 |
| `features/sync/tests/test_feature_runtime.py` | 修改：当前包版本、SDK 与构建示例断言 |
| `features/download/README.md` | 修改：修复边界、当前版本、SDK 和构建示例 |
| `features/rename/README.md` | 修改：同上 |
| `features/search/README.md` | 修改：SDK 依赖版本说明和构建示例 |
| `features/sync/README.md` | 修改：同上 |
| `features/caption/README.md` | 修改：SDK 依赖版本说明 |
| `README.md` | 修改：当前交付、版本表和升级说明 |
| `README_EN.md` | 修改：对应英文说明 |
| `docs/media-release-format-topology.md` | 修改：对应当前 Search 版本，识别规则未改 |
| `docs/superpowers/plans/2026-11-11-115-pagination-termination.md` | 新增：实施范围与验证计划 |
| `docs/audits/2026-11-11-115-pagination-diagnosis.md` | 新增：本证据、边界及交付记录 |

## 实际验证

Python：`/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`。pytest 使用 `PYTHONDONTWRITEBYTECODE=1`、`-p no:cacheprovider`。

- 三个新 ZIP 的 CRC 及全部 JSONL 逐行解析通过，共 224、4643、3479 条机器日志。
- 主负责人复核五季 60 个文件的 ID 集合、父目录、大小和 SHA1 字段，全部一致。
- 修改前失败回归：SDK 6 failed/10 passed；Rename 新终止回归 15 failed，均由多余尾页请求触发。修改后 SDK 16 passed，Rename 定向 126 passed。
- Search 最终全量：3605 passed、2 skipped、246 subtests passed。
- Download 最终全量：267 passed、33 subtests passed。
- Rename 最终全量：523 passed、28 subtests passed；保留一条已有 logger.warn 弃用警告。
- Sync 最终全量：160 passed、80 subtests passed。
- Caption 最终全量：500 passed、6 skipped。
- 根目录 Host/SDK/真实 Feature 交接定向：66 passed、24 subtests passed，命令如下；没有宣称 Host 全量通过。
- 版本、manifest、SDK 精确依赖与 README 一致；`.git`、`.worktrees` 不存在，`.stfolder` 保留。
- 已完成独立只读审查；末页省略 count 的 continuation 问题补齐后，审查者复测 2 passed，未发现新的重要问题。

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
# 分别在五个 features/<module> 目录执行
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-test-deps-20261111:src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --tb=short

# 项目根目录
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-test-deps-20261111:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_storage_tree_termination.py tests/test_large_snapshot_execution.py \
  tests/test_real_feature_business_flow.py tests/test_caption_business_flow.py \
  tests/test_raw_search_handoff.py tests/test_storage_safety_rpc.py \
  tests/test_technical_identity_migration.py tests/test_deployment_contract.py --tb=short
```

临时依赖目录是此前为本机缺少的已声明 OpenCC 依赖建立的测试目录，没有改系统 Python。一次未使用该路径的根全量尝试在 2 个字幕用例失败后被中止，不能计作全量通过；加上该路径单独重跑这两个用例为 2 passed。另一次根定向命令把部署测试文件名写错，没有运行测试；已使用正确文件名重跑。

没有测试用户的实时 115 返回，也没有把本地假设尾页 count0 的复现输入当作线上原响应。保留旧审计原文，新的确认与限制以本记录为准。

等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 发布。优先升级 Download、Rename；其他三个 Feature 为 SDK 包版本对齐。Mac 不执行 Git 或发布。
