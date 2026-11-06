# Telepiplex Search 2.9.0 评分交付

本次为 Mac 本地源码修改与测试记录，未发布。Search 从 2.8.1 提升到 2.9.0。

## 行为

- `reject_keywords` 命中即排除，不再作为默认扣分入口；明确分辨率也匹配像素尺寸别名。
- 显式 `keyword_scores` 是完整分数表，不隐式恢复 `prefer_*` 或拒绝列表。旧配置显式列出的 `prefer_*` 仍兼容。
- 来源关键词 BluRay / BDRip / WEB 归一计一次；WEB-DL、WEBRip、WEBMux 都是 WEB。明确的蓝光 REMUX 才应用 Remux 分，WEB REMUX 不减分，未知来源的 REMUX 不推断蓝光。
- 示例来源分为 BluRay 12、BDRip 10、WEB 6、蓝光 Remux -1。其他条件相同时：BluRay 12 > BluRay REMUX 11 > BDRip 10 > BDRip REMUX 9 > WEB 6。
- 其他关键词仍按标题匹配，不自动消除所有同义词叠加；原有做种和体积分保留。未知分辨率不会仅因低清拒绝列表被排除。
- [完整可复制配置](../../features/search/examples/scoring.yaml) 替换运行配置 `search` 下整个 `scoring` 段，需要 Search 2.9.0。

## 文件清单

无删除或重命名。新增 3 个文件，修改 13 个文件：

| 文件 | 操作与目的 |
| --- | --- |
| `features/search/src/telepiplex_search/release_score.py` | 修改：硬拒绝、显式分数表、来源条件计分 |
| `features/search/src/telepiplex_search/release_formats.py` | 修改：共享来源家族及 REMUX 识别 |
| `features/search/src/telepiplex_search/release_report.py` | 修改：展示复用共享来源识别 |
| `features/search/config.default.yaml` | 修改：仅保留拒绝列表与两张分数表 |
| `features/search/config.schema.json` | 修改：描述新语义，标记旧 prefer 字段弃用 |
| `features/search/examples/scoring.yaml` | 新增：用户可复制的完整评分片段 |
| `features/search/tests/test_release_scoring.py` | 新增：来源排序、硬拒绝、尺寸别名、兼容性及示例验证 |
| `features/search/manifest.yaml` | 修改：版本 2.9.0 |
| `features/search/pyproject.toml` | 修改：包版本 2.9.0 |
| `features/search/tests/test_feature_service.py` | 修改：同步版本及构建示例断言 |
| `features/search/tests/test_config_schema_contract.py` | 修改：同步版本断言 |
| `tests/test_technical_identity_migration.py` | 修改：同步 Search 技术身份版本断言 |
| `features/search/README.md` | 修改：版本、升级行为与配置示例入口 |
| `README.md` | 修改：当前版本和本次交付入口 |
| `docs/media-release-format-topology.md` | 修改：共享来源识别及评分边界 |
| `docs/audits/2026-11-06-search-scoring.md` | 新增：本交付记录 |

## 实际验证

使用运行时 Python：

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
```

在 `/Users/young/Documents/Telepiplex/features/search`：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests
```

结果：3591 passed、2 skipped、194 subtests passed。

随后补充可复制示例与降分辨率用例、整理拒绝辅助函数后，在同目录定向复测：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_release_scoring.py tests/test_release_report.py tests/test_config_schema_contract.py
```

结果：70 passed、63 subtests passed。

在 `/Users/young/Documents/Telepiplex`：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider tests/test_technical_identity_migration.py tests/test_feature_builder.py tests/test_real_feature_business_flow.py
```

结果：37 passed、1 skipped、16 subtests passed。

通过 Python 文件系统断言确认 `.git`、`.worktrees` 不存在，`.stfolder` 保留。未运行 Git 或发布。

等待 Syncthing 显示 `Up to Date / 最新`，由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 手动发布、升级 Search 2.9.0 后使用新评分配置。
