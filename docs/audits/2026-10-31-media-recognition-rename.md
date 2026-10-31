# Telepiplex 音视频规格识别与 Rename 阻塞修复

日期：2026-10-31。仅完成 Mac 本地源码修改与测试，尚未发布，也未操作真实 115 媒体文件。

## 日志证据与结论

输入为 `/Users/young/Downloads/20261029T065723+0800-BD3F4CF0BB3E.zip`。压缩包中的日志仅作为故障证据使用。

- 搜索报告没有展示已有的视频编码、位深与音频编码。日志中的 `The.Wire.S01.2160p.x265.10bit.DTS-HD.MA.5.1[TheUpscaler]` 原来显示 `2160p · 6ch环绕`；本次显示 `2160p · HEVC · 10bit · DTS-HD MA · 6ch环绕`。
- 全剧候选标题截在 `1080p BluRay x265 HEVC 10bit`，没有音轨信息；下载后的目录名称才包含 `AAC 5.1`。搜索报告不能使用尚未得到的下载目录内容。本次会显示已知的视频规格，声道仍为 `?ch`；只有同一候选附带更完整且不矛盾的排序标题或 magnet `dn` 时才补缺。
- 10:26:38 的 Rename 调用先成功将 The Wire 的一集改名为 `The Wire S05E10.mkv`，随后查询 `/真人剧集/火线 (2002) ⋯ The Wire/The Wire Season 05`。该查询在 Download 的 `_file_info_value()` 抛出 `115 file identity lookup failed`，阻断季目录创建与文件移动。后续补整理同样失败，终态记录为 2 个作品、56 个媒体文件失败，已整理为 0。
- 旧日志只有 HTTP 状态分类，未保留 115 业务码，因此无法证明当次一定返回 `430004`。修复对明确的缺失响应与其他查询错误做出区分，并增加数字业务码观测，供重试后核对。

`430004` 的兼容处理参考 [MoviePilot 的 115 存储实现](https://github.com/jxxghp/MoviePilot/blob/v2/app/modules/filemanager/storages/u115.py)，其 `get_info` 单独接受此码。本次仅在 `state: false`、空数据、无身份且没有矛盾状态时视为不存在；其他错误、超时、残缺数据及矛盾响应继续失败关闭，不允许据此覆盖或删除文件。

## 实现范围

Search 识别下划线、全角字符、中英文相邻技术标记、常见尺寸与分辨率别名、HDR10+/HLG/SDR、HEVC/AVC/AV1/VP9、显式位深及常见音频编码。声道支持 `5_1`、`DD51`、`6CH`、Mono/Stereo 和多轨显式声道，避免把片名前的小数或 `Atmosphere` 等子串当作音频能力。音频编码本身不推导声道数，10bit 本身不推导 HDR。结果继续使用每个选项两行的紧凑报告。

Download 将明确不存在返回为 `None`，查询失败继续抛错。批量查询不再把失败吞成 `None`；Rename 也拒绝缺少请求路径的批量响应。移动后的文件 ID 验证与原有缓存失效机制继续执行。

本次源码版本：Download `2.2.2`、Search `2.7.0`、Rename `2.4.3`。Host `3.9.3`、SDK `2.2.1`、Sync `2.1.1`、Caption `1.3.3` 保持。没有数据库、配置或共享元数据协议迁移。

## 文件清单

新增 2 个文件，修改 24 个文件；无删除或重命名。下表逐项列出源码、测试和文档。

| 文件 | 目的 |
| --- | --- |
| `features/search/src/telepiplex_search/release_report.py` | 增强技术参数解析、同资源补缺与编码展示，保留未知状态。 |
| `features/search/tests/test_release_report.py` | 覆盖日志实例、别名、多音轨、误识别、补缺冲突及报告长度。 |
| `features/download/src/telepiplex_download/client.py` | 区分明确缺失与查询失败，批量失败上报，保留受限业务码。 |
| `features/download/src/telepiplex_download/observability.py` | 观测日志仅接受有长度限制的数字业务码。 |
| `features/download/tests/test_client_move_safety.py` | 更新批量查询错误契约，验证成功查询缓存与失败上报。 |
| `features/download/tests/test_file_info_absence.py`（新增） | 覆盖缺失目录创建、未知/矛盾响应与日志隐私。 |
| `features/rename/src/telepiplex_rename/service.py` | 批量响应遗漏路径时拒绝缓存为不存在。 |
| `features/rename/tests/test_feature_processor.py` | 验证批量遗漏、明确缺失、服务错误，以及当前版本。 |
| `features/rename/tests/test_file_executor.py` | 验证 The Wire 部分已改名后重试与文件 ID 保持。 |
| `tests/test_storage_safety_rpc.py` | 通过真实 Host RPC、Download 客户端与 Rename 执行器验证缺失目录、异常中止和移动后身份检查。 |
| `features/download/manifest.yaml` | Download 发布身份提升到 2.2.2。 |
| `features/download/pyproject.toml` | 对齐 Download 包版本。 |
| `features/search/manifest.yaml` | Search 发布身份提升到 2.7.0。 |
| `features/search/pyproject.toml` | 对齐 Search 包版本。 |
| `features/rename/manifest.yaml` | Rename 发布身份提升到 2.4.3。 |
| `features/rename/pyproject.toml` | 对齐 Rename 包版本。 |
| `features/download/tests/test_feature_runtime.py` | 更新发布身份及构建示例的既有断言。 |
| `features/search/tests/test_config_schema_contract.py` | 更新发布身份断言。 |
| `features/search/tests/test_feature_service.py` | 更新版本及构建示例的既有断言。 |
| `tests/test_technical_identity_migration.py` | 对齐三个 Feature 的当前版本。 |
| `features/download/README.md` | 说明路径缺失处理、失败语义与新包版本。 |
| `features/search/README.md` | 说明识别升级、未知值策略与新包版本。 |
| `features/rename/README.md` | 说明批量身份检查、重试整理与新包版本。 |
| `README.md` | 更新当前源码版本、升级顺序与本次交付入口。 |
| `README_EN.md` | 同步英文版本与交付说明。 |
| `docs/audits/2026-10-31-media-recognition-rename.md`（新增） | 保存故障证据、逐文件清单、验证与交接说明。 |

## 本地验证

以下测试使用 `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`。

首次根目录测试发现运行环境缺少 Caption 已声明的 `opencc-python-reimplemented`，导致两个字幕集成用例拒绝字幕，原因是 `script_detector_unavailable`。将该依赖安装到临时目录 `/tmp/telepiplex-test-deps-20261031` 后重跑；未因此修改 Caption 源码或依赖声明。

最终完整测试结果：

| 范围 | passed | skipped | subtests passed |
| --- | ---: | ---: | ---: |
| 根目录 Host / SDK / 跨 Feature 回归 | 758 | 1 | 333 |
| Download | 259 | 0 | 33 |
| Search | 731 | 2 | 175 |
| Rename | 499 | 0 | 28 |
| 合计 | 2247 | 3 | 569 |

Rename 只有一条既有 `logger.warn` 弃用提示。Sync 和 Caption 的独立完整套件未运行；根目录包含 Caption 真实 RPC 集成用例。

实际命令：

```bash
cd /Users/young/Documents/Telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
EXTRA=/tmp/telepiplex-test-deps-20261031

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src:"$EXTRA" \
  "$PY" -m pytest -q -p no:cacheprovider --tb=short --show-capture=no tests

for module in download rename; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src:"$EXTRA" \
      "$PY" -m pytest -q -p no:cacheprovider --tb=short --show-capture=no tests
  )
done

(cd features/search && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests)

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

根目录新增的真实 RPC 场景验证：缺失季目录可创建；已改名文件继续移动；仅剩余原名文件执行改名；未知或矛盾的身份响应在写前停止；移动成功回执若对应的文件 ID 改变，整理仍判失败。模拟链路不调用 copy/delete。

另外完成 14 个修改/新增 Python 文件的 AST 语法解析，三个 Feature 的 manifest/包版本一致性检查，以及 README 交付入口检查。工作区边界检查通过：无 `.git`、无 `.worktrees`，`.stfolder` 保留。

所有存储副作用均发生在本地模拟提供方。未执行真实 115 写入、线上剧集整理、Plex 扫描或发布，尚需更新运行中的 Feature 后重试确认。

## 交接

等待 Syncthing 显示 `Up to Date / 最新`，同步到 Unraid `/mnt/user/archives/Life Hacker/Telepiplex`。由用户在 Unraid 检查、提交及发布。运行端按 Download 2.2.2 → Search 2.7.0 → Rename 2.4.3 更新后，重新发起失败作品的补整理。已进入失败终态的旧任务不会由本次源码修改自动恢复。
