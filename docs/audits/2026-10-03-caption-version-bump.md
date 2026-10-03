# Caption 补丁版本提升

日期：2026-10-03。用户反馈原版本 Release 已存在，本次将 Caption **1.1.1 → 1.1.2**，供后续使用新版本发布。仅更新版本标识及相应文档、测试断言；Search 仍为 2.6.1，业务源码、配置/API 合同及依赖版本保持。未查询远端 Release，未执行发布。

## 文件清单

| 类型 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | [features/caption/manifest.yaml](../../features/caption/manifest.yaml) | 插件版本提升为 1.1.2。 |
| 修改 | [features/caption/pyproject.toml](../../features/caption/pyproject.toml) | Python 包版本同步为 1.1.2。 |
| 修改 | [features/caption/README.md](../../features/caption/README.md) | 同步当前源码版本。 |
| 修改 | [features/caption/CHANGELOG.md](../../features/caption/CHANGELOG.md) | 新增 1.1.2 记录，说明版本提升原因及范围。 |
| 修改 | [features/search/README.md](../../features/search/README.md) | 同步配套 Caption 版本引用。 |
| 修改 | [README.md](../../README.md) | 同步中文版本表、能力说明和升级组合。 |
| 修改 | [README_EN.md](../../README_EN.md) | 同步英文版本表、能力说明、升级组合及依赖说明。 |
| 修改 | [tests/test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 同步 Caption 版本合同断言。 |
| 新增 | [本交付记录](2026-10-03-caption-version-bump.md) | 记录变更、实际验证和交付边界。 |

无删除或重命名文件。历史审计、旧更新记录和构建产物保留原版本。

## 实际验证

本轮两组针对性测试合计 **18 passed，42 subtests passed**：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_technical_identity_migration.py tests/test_product_name_casing.py tests/test_caption_host_contract.py
# 17 passed, 42 subtests passed in 1.10s

cd /Users/young/Documents/telepiplex/features/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_runtime.py
# 1 passed in 0.11s
```

项目根目录的以下检查通过：

```bash
test ! -e .git && test ! -e .worktrees && test -d .stfolder
```

本次未重跑完整业务测试、联网字幕实测、压测或插件包构建。

等待 Syncthing 显示 **Up to Date / 最新**，同步至 Unraid `/mnt/user/archives/life hacker/telepiplex` 后，由用户检查并发布 Caption 1.1.2。本次未执行 Git 或发布操作。
