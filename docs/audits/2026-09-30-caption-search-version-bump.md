# Caption / Search 补丁版本提升

日期：2026-09-30。本次按用户要求仅提升相关插件版本标识：Caption **1.1.0 → 1.1.1**，Search **2.6.0 → 2.6.1**。业务源码、配置/API 合同和依赖版本没有变化。历史审计、实测记录与旧构建产物保留原版本。

## 文件清单

| 类型 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | [features/caption/manifest.yaml](../../features/caption/manifest.yaml) | Caption 插件版本更新为 1.1.1。 |
| 修改 | [features/caption/pyproject.toml](../../features/caption/pyproject.toml) | Caption Python 包版本同步为 1.1.1。 |
| 修改 | [features/search/manifest.yaml](../../features/search/manifest.yaml) | Search 插件版本更新为 2.6.1。 |
| 修改 | [features/search/pyproject.toml](../../features/search/pyproject.toml) | Search Python 包版本同步为 2.6.1。 |
| 修改 | [features/caption/README.md](../../features/caption/README.md) | 同步当前 Caption 版本与配套 Search 版本。 |
| 修改 | [features/search/README.md](../../features/search/README.md) | 同步当前版本、补丁范围及构建产物示例。 |
| 修改 | [features/caption/CHANGELOG.md](../../features/caption/CHANGELOG.md) | 新增 1.1.1 版本记录，保留 1.1.0 历史条目。 |
| 修改 | [README.md](../../README.md) | 同步中文版本表与升级组合。 |
| 修改 | [README_EN.md](../../README_EN.md) | 同步英文版本表与升级组合。 |
| 修改 | [tests/test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 同步两个插件的版本合同断言。 |
| 修改 | [features/search/tests/test_config_schema_contract.py](../../features/search/tests/test_config_schema_contract.py) | 同步 Search 版本断言，配置 schema 仍为 v2。 |
| 修改 | [features/search/tests/test_feature_service.py](../../features/search/tests/test_feature_service.py) | 同步 manifest、包版本和当前构建示例的断言。 |
| 新增 | [本交付记录](2026-09-30-caption-search-version-bump.md) | 记录本轮文件变化、实际验证和交付边界。 |

无删除或重命名文件。

## 实际验证

使用 `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，以下三组针对性测试实际通过，合计 **36 passed，42 subtests passed**：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_technical_identity_migration.py tests/test_product_name_casing.py tests/test_caption_host_contract.py
# 17 passed, 42 subtests passed

cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_config_schema_contract.py tests/test_feature_service.py::FeatureSourceContractTest
# 18 passed

cd /Users/young/Documents/telepiplex/features/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_runtime.py
# 1 passed
```

当前版本文件、当前 README 与版本合同测试中未残留旧版本引用。以下检查在项目根目录实际通过：

```bash
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

本次没有重跑完整业务测试、联网字幕实测、压测或重新构建插件包；2026-09-28 的结果保留为对应版本的历史验证，不冒称本轮结果。

等待 Syncthing 显示 **Up to Date / 最新**，同步到 Unraid `/mnt/user/archives/life hacker/telepiplex` 后，由用户检查并自行发布。本次未执行 Git 或发布。
