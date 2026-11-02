# Telepiplex 格式识别拓扑交付

日期：2026-11-02。对应 Search 2.8.0，仅修改本地源码、测试和文档，尚未发布。

## 完成范围

将此前散落在结果展示中的格式正则集中到 `release_formats.py`，按固定格式家族声明标准标签、匹配规则和别名示例。展示层按解析后的字段生成规格行，保留已有两行片源列表、活跃状态、按钮与资源身份。

覆盖目录与限制见 [格式识别拓扑](../media-release-format-topology.md)。覆盖项包括分辨率/扫描方式、来源、视频编码、位深、动态范围、封装、帧率、音频编码、声道和沉浸能力。不存在的资源信息继续未知；没有进行实际文件探测或联网补全资源规格。

同资源的排序标题和 magnet 显示名可以补缺，冲突的补充信息被拒绝。已经接受的补充事实参与下一次冲突检查，避免两个备选名称分别向空字段写入互相矛盾的规格。

## 文件清单

| 文件 | 类型 | 目的 |
| --- | --- | --- |
| `features/search/src/telepiplex_search/release_formats.py` | 新增 | 声明式格式目录、归一化、格式解析与声道语法。 |
| `features/search/src/telepiplex_search/release_report.py` | 修改 | 消费统一解析结果，兼容性补缺，展示封装与帧率。 |
| `features/search/tests/test_release_formats.py` | 新增 | 遍历目录别名与分隔符，验证真实命名和误识别拒绝条件。 |
| `features/search/tests/test_release_format_matrix.py` | 新增 | 使用独立预期值组合验证实际规格行、未知值、冲突和完整报告。 |
| `features/search/tests/test_release_report.py` | 修改 | 验证归一化版本标记、父子格式细化及独立音轨保留。 |
| `features/search/manifest.yaml` | 修改 | Search 版本提升至 2.8.0。 |
| `features/search/pyproject.toml` | 修改 | 包版本与 manifest 对齐。 |
| `features/search/tests/test_config_schema_contract.py` | 修改 | 更新既有版本断言。 |
| `features/search/tests/test_feature_service.py` | 修改 | 更新版本和构建示例断言。 |
| `tests/test_technical_identity_migration.py` | 修改 | 对齐当前 Search 源码版本。 |
| `features/search/README.md` | 修改 | 说明目录入口、识别边界与新版本。 |
| `README.md` | 修改 | 更新当前源码版本及本次交付入口。 |
| `README_EN.md` | 修改 | 同步英文当前版本与交付说明。 |
| `docs/media-release-format-topology.md` | 新增 | 列出识别路径、格式家族、别名和冲突规则。 |
| `docs/audits/2026-11-02-format-topology.md` | 新增 | 保存逐文件用途、本地验证和交接要求。 |

与开始前的文件 SHA-256 快照比对，共新增 5 个、修改 10 个文件，没有删除或重命名文件。Host、SDK 和其余四个 Feature 的版本与业务源码保持。

## 本地验证

格式目录最终包含 10 个维度、147 条规则、301 条登记别名样例。逐一检查文档覆盖所有标准标签和登记样例。

| 验证范围 | 实际结果 |
| --- | --- |
| Search 完整套件 | 3553 passed，2 skipped，181 subtests passed，45.03 秒 |
| 根目录相关身份、文档与跨 Feature 回归 | 46 passed，73 subtests passed，6.91 秒 |
| 格式解析、独立展示矩阵及既有报告的针对性测试 | 2836 passed，50 subtests passed；已包含在 Search 完整结果中，不重复计数 |
| 独立展示矩阵 | 1818 个用例；其中 1728 个为分辨率 × 视频编码 × 音轨布局 × 分隔符 × 大小写的完整代表组合，其余覆盖 HDR、旧格式、全角、冲突、未知值与 12 条结果的按钮/长度 |
| 文件与边界检查 | 8 个新增/修改 Python 文件通过 AST；版本声明一致；文档覆盖全部登记标签与别名；无 `.git`、无 `.worktrees`，`.stfolder` 保留 |

实际使用以下命令；没有运行其余 Feature 的独立完整套件，也没有重新构建或发布包：

```bash
cd /Users/young/Documents/Telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

(
  cd features/search
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider --tb=short --show-capture=no tests
)

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider --tb=short --show-capture=no \
  tests/test_technical_identity_migration.py tests/test_product_name_casing.py \
  tests/test_host_media_metadata.py tests/test_real_feature_business_flow.py \
  tests/test_raw_search_handoff.py

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

针对性测试发现并修复的实例包括：`AC3.5.1` 被编码数字吞掉、显式 `6CH` 漏识别、中文紧邻技术标记、`H264.L4.1` 与 DV Profile 被当成声道、码率被吃成高度声道、音频 16bit 冒充视频位深、`MPEG-4 AAC` 冒充视频编码、片名 Ape/Cam/Opus 被当作技术格式，以及带扫描模式的附着帧率漏识别。

回归包含实际 Search → Download → Rename 的本地模拟 RPC 链路、原文搜索交接和固定技术身份。没有执行真实媒体文件操作。

## 交接

等待 Syncthing 显示 `Up to Date / 最新`，同步到 `/mnt/user/archives/Life Hacker/Telepiplex`。由用户在 Unraid 检查、提交及发布，再更新运行端 Search 2.8.0。已经安装上一轮版本的 Download、Rename、Host、SDK、Sync 和 Caption 无需因本次再次更新。

未执行 Git、发布或真实媒体文件操作。上一轮审计交付记录保持原文。
