# Telepiplex 品牌补丁升版交付

本报告接续品牌文案调整。此前报告中的“未升版”描述保留为当时记录；本轮按用户要求准备新版本，未执行真实 Git、未访问项目 GitHub 仓库、未发布。

## 版本

| 组件 | 修改前 | 当前源码 |
| --- | --- | --- |
| Host | `3.6.12` | `3.6.13` |
| SDK | `2.1.0` | `2.1.1` |
| download | `2.1.0` | `2.1.1` |
| search | `2.2.1` | `2.2.2` |
| rename | `2.1.1` | `2.1.2` |
| sync | `2.0.1` | `2.0.2` |
| caption | `0.1.4` | `0.1.5` |
| echo 示例 | `1.0.0` | `1.0.1` |

SDK 日志文案和包描述发生变化，因此 SDK 升补丁版；五个 Feature 和 echo 示例均锁定 `telepiplex-plugin-sdk==2.1.1`。caption 从旧的 SDK 1.3.1 对齐至当前 SDK，已验证预留运行时和实际 .tpx 打包。echo 只更新示例身份，不属于五个正式 Feature 发布标签。Host API、配置/状态 schema、capability、命令、callback、User-Agent 和 MCP 服务名均未调整。

## 逐文件清单

修改 37 个已有文件（含构建测试从源码重新生成的 6 个文件）；新增本报告 1 个文件；无删除或重命名。

| 文件 | 用途 |
| --- | --- |
| [README.md](../../README.md) | 补充当前源码版本表、品牌补丁说明和部署更新方式。 |
| [README_EN.md](../../README_EN.md) | 补充当前源码版本表、品牌补丁说明和部署更新方式。 |
| [app/115bot.py](../../app/115bot.py) | Host 版本升至 v3.6.13-host。 |
| [examples/echo_feature/build/lib/telepiplex_echo/__init__.py](../../examples/echo_feature/build/lib/telepiplex_echo/__init__.py) | 本地打包测试从当前源码重新生成的构建副本；未手工改写。 |
| [examples/echo_feature/manifest.yaml](../../examples/echo_feature/manifest.yaml) | 同步 Feature/示例补丁版本，其他合同字段不变。 |
| [examples/echo_feature/pyproject.toml](../../examples/echo_feature/pyproject.toml) | 同步补丁版本与 SDK 2.1.1 依赖。 |
| [examples/echo_feature/src/telepiplex_echo.egg-info/PKG-INFO](../../examples/echo_feature/src/telepiplex_echo.egg-info/PKG-INFO) | 本地打包测试从 pyproject 重新生成的版本、描述或依赖元数据；未手工改写。 |
| [examples/echo_feature/src/telepiplex_echo.egg-info/requires.txt](../../examples/echo_feature/src/telepiplex_echo.egg-info/requires.txt) | 本地打包测试从 pyproject 重新生成的版本、描述或依赖元数据；未手工改写。 |
| [features/caption/README.md](../../features/caption/README.md) | 更新当前版本、SDK 说明和可执行构建示例。 |
| [features/caption/manifest.yaml](../../features/caption/manifest.yaml) | 同步 Feature/示例补丁版本，其他合同字段不变。 |
| [features/caption/pyproject.toml](../../features/caption/pyproject.toml) | 同步补丁版本与 SDK 2.1.1 依赖。 |
| [features/download/README.md](../../features/download/README.md) | 更新当前版本、SDK 说明和可执行构建示例。 |
| [features/download/manifest.yaml](../../features/download/manifest.yaml) | 同步 Feature/示例补丁版本，其他合同字段不变。 |
| [features/download/pyproject.toml](../../features/download/pyproject.toml) | 同步补丁版本与 SDK 2.1.1 依赖。 |
| [features/download/tests/test_feature_runtime.py](../../features/download/tests/test_feature_runtime.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [features/rename/README.md](../../features/rename/README.md) | 更新当前版本、SDK 说明和可执行构建示例。 |
| [features/rename/manifest.yaml](../../features/rename/manifest.yaml) | 同步 Feature/示例补丁版本，其他合同字段不变。 |
| [features/rename/pyproject.toml](../../features/rename/pyproject.toml) | 同步补丁版本与 SDK 2.1.1 依赖。 |
| [features/rename/tests/test_feature_processor.py](../../features/rename/tests/test_feature_processor.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [features/search/README.md](../../features/search/README.md) | 更新当前版本、SDK 说明和可执行构建示例。 |
| [features/search/manifest.yaml](../../features/search/manifest.yaml) | 同步 Feature/示例补丁版本，其他合同字段不变。 |
| [features/search/pyproject.toml](../../features/search/pyproject.toml) | 同步补丁版本与 SDK 2.1.1 依赖。 |
| [features/search/tests/test_config_schema_contract.py](../../features/search/tests/test_config_schema_contract.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [features/search/tests/test_feature_service.py](../../features/search/tests/test_feature_service.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [features/sync/README.md](../../features/sync/README.md) | 更新当前版本、SDK 说明和可执行构建示例。 |
| [features/sync/manifest.yaml](../../features/sync/manifest.yaml) | 同步 Feature/示例补丁版本，其他合同字段不变。 |
| [features/sync/pyproject.toml](../../features/sync/pyproject.toml) | 同步补丁版本与 SDK 2.1.1 依赖。 |
| [features/sync/tests/test_feature_runtime.py](../../features/sync/tests/test_feature_runtime.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [sdk/build/lib/telepiplex_plugin_sdk/logging_utils.py](../../sdk/build/lib/telepiplex_plugin_sdk/logging_utils.py) | 本地打包测试从当前源码重新生成的构建副本；未手工改写。 |
| [sdk/build/lib/telepiplex_plugin_sdk/media_metadata_v2.py](../../sdk/build/lib/telepiplex_plugin_sdk/media_metadata_v2.py) | 本地打包测试从当前源码重新生成的构建副本；未手工改写。 |
| [sdk/pyproject.toml](../../sdk/pyproject.toml) | SDK 版本升至 2.1.1。 |
| [sdk/src/telepiplex_plugin_sdk.egg-info/PKG-INFO](../../sdk/src/telepiplex_plugin_sdk.egg-info/PKG-INFO) | 本地打包测试从 pyproject 重新生成的版本、描述或依赖元数据；未手工改写。 |
| [tests/test_bot_runtime_startup.py](../../tests/test_bot_runtime_startup.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [tests/test_deployment_contract.py](../../tests/test_deployment_contract.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [tests/test_feature_builder.py](../../tests/test_feature_builder.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [tests/test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| [tests/test_unraid_publish_script.py](../../tests/test_unraid_publish_script.py) | 同步当前版本、SDK 依赖、README 示例或发布脚本夹具断言。 |
| 本报告 | 记录本轮版本、文件、验证与交付边界。 |

## 实际验证

所有 pytest 命令均使用 Python 3.12：`/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，并设置 `PYTHONDONTWRITEBYTECODE=1`、`-q -p no:cacheprovider`。打包相关命令设置 `PIP_NO_INDEX=1`，只使用本地源码与已有依赖。

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_bot_runtime_startup.py tests/test_technical_identity_migration.py \
  tests/test_feature_builder.py tests/test_unraid_publish_script.py \
  tests/test_deployment_contract.py tests/test_release_workflow.py tests/test_diagnostics.py

# 修正旧 SDK 版本断言后复测此文件
PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_deployment_contract.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_plugin_sdk_runtime.py tests/test_release_catalog_generator.py

# 以下分别在 features/<module> 下执行，PYTHONPATH=src:../../sdk/src
# download: tests/test_feature_runtime.py
# search: tests/test_config_schema_contract.py tests/test_feature_service.py
# rename: tests/test_feature_processor.py
# sync: tests/test_feature_runtime.py
# caption: tests/test_placeholder_runtime.py
```

| 测试组 | 实际结果 |
| --- | --- |
| Host/构建/发布/诊断首组 | 96 passed、1 failed、1 skipped、78 subtests passed；失败为旧 SDK 2.1.0 断言 |
| 部署合同修正后复测 | 12 passed、24 subtests passed |
| SDK 运行时与 catalog | 27 passed、6 subtests passed |
| download | 85 passed、31 subtests passed |
| search | 165 passed、21 subtests passed |
| rename | 125 passed、3 subtests passed |
| sync | 46 passed、11 subtests passed |
| caption | 1 passed |

跳过项是未提供完整成套 .tpx 的 artifact matrix。首组修正后只复跑失败文件，未声称重跑全套；未执行全部项目测试。发布脚本测试使用临时 fake Git 记录器，不调用真实 Git。

附加验证：

- 7 个项目均在临时副本中用 setuptools `build_wheel` 离线构建成功；wheel 的 Name/Version 与 pyproject 一致，六个 Feature/示例均精确依赖 SDK 2.1.1。
- caption 0.1.5 通过实际 `build_feature_artifact` 和 `verify_tpx`，确认内含 SDK 2.1.1 wheel。此包使用本地测试来源标记，不作为正式发布包。
- 五个 Feature 的 manifest 与 pyproject 版本一致；manifest 除 version 外与本轮基线完全一致。
- 259 个历史文件和 6 个协议相关文件 SHA-256 与本轮基线一致。
- `.git`、`.worktrees` 不存在，`.stfolder` 保留。

## 交付

等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 `/mnt/user/archives/life hacker/telepiplex` 执行检查和发布。此前品牌任务更新的 `scripts/unraid/telepiplex-publish.sh` 仍需替换到 Unraid User Scripts 实际脚本；本轮未再次修改该脚本。

预期新标签：`telepiplex-v3.6.13`、`download-v2.1.1`、`search-v2.2.2`、`rename-v2.1.2`、`sync-v2.0.2`、`caption-v0.1.5`。这里的版本基于本地源码，未查询远端；发布脚本在 Unraid 检查标签并逐个推送待发布标签。正式发布完成后更新 Host 镜像，并在 `/plugin` 确认更新已安装模块。SDK 随包构建，不新增 SDK 独立发布标签。
