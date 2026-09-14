# Telepiplex 品牌命名调整交付

本次将对外品牌统一为 `Telepiplex`，代码身份保持 `telepiplex`，环境变量和常量保持 `TELEPIPLEX_*`。仅本地修改和验证，未升版、未执行真实 Git、未连接项目 GitHub 仓库、未发布。

## 文件清单

修改 33 个现有文件，新增本报告 1 个文件；无删除、无重命名。

| 文件 | 修改目的 |
| --- | --- |
| [.github/workflows/release-feature.yml](../../.github/workflows/release-feature.yml) | 更新 Feature 工作流、发布说明、目录 README 模板与 Release 展示标题。 |
| [.github/workflows/release.yml](../../.github/workflows/release.yml) | 更新 Host 工作流展示文案与 Release 标题；tag、镜像、job ID 和依赖不变。 |
| [AGENTS.md](../../AGENTS.md) | 确立品牌/代码身份分离规则，并明确协议与历史资料保留范围。 |
| [README.md](../../README.md) | 统一中文产品称呼，说明 Release 展示标题与 tag 的区别。 |
| [README_EN.md](../../README_EN.md) | 统一英文产品称呼，说明 Release 展示标题与 tag 的区别。 |
| [app/115bot.py](../../app/115bot.py) | 更新启动通知、错误提示和自然语言启动日志。 |
| [app/config.yaml.example](../../app/config.yaml.example) | 更新配置模板注释。 |
| [app/handlers/interaction_handler.py](../../app/handlers/interaction_handler.py) | 更新默认海报占位标题。 |
| [app/runtime/command_catalog.py](../../app/runtime/command_catalog.py) | 更新 /start 帮助中的两处品牌标题。 |
| [app/runtime/message_cleanup.py](../../app/runtime/message_cleanup.py) | 更新消息清理日志和模块说明。 |
| [app/utils/logger.py](../../app/utils/logger.py) | 更新日志写入失败提示，保留 logger 与日志文件身份。 |
| [config/config.yaml.example](../../config/config.yaml.example) | 更新公开配置模板注释，与 app 模板保持一致。 |
| [examples/echo_feature/pyproject.toml](../../examples/echo_feature/pyproject.toml) | 更新示例包描述，保留包名、版本与依赖。 |
| [examples/echo_feature/src/telepiplex_echo/__init__.py](../../examples/echo_feature/src/telepiplex_echo/__init__.py) | 更新示例模块说明。 |
| [features/caption/README.md](../../features/caption/README.md) | 统一现行模块文档中的品牌称呼，保留命令、路径和技术标识。 |
| [features/caption/pyproject.toml](../../features/caption/pyproject.toml) | 更新 caption 包描述，保留包名、版本与依赖。 |
| [features/download/README.md](../../features/download/README.md) | 统一现行模块文档中的品牌称呼，保留命令、路径和技术标识。 |
| [features/rename/README.md](../../features/rename/README.md) | 统一现行模块文档中的品牌称呼，保留命令、路径和技术标识。 |
| [features/search/README.md](../../features/search/README.md) | 统一现行模块文档中的品牌称呼，保留命令、路径和技术标识。 |
| [features/search/tools/run_live_pipeline_audit.py](../../features/search/tools/run_live_pipeline_audit.py) | 更新现行审计工具的 docstring。 |
| [features/sync/README.md](../../features/sync/README.md) | 统一现行模块文档中的品牌称呼，保留命令、路径和技术标识。 |
| [features/sync/THIRD_PARTY_LICENSES/plex-mcp-server-MIT.txt](../../features/sync/THIRD_PARTY_LICENSES/plex-mcp-server-MIT.txt) | 仅更新本项目的改编用途说明；上游版权与许可原文不变。 |
| [features/sync/src/telepiplex_sync/sync_service.py](../../features/sync/src/telepiplex_sync/sync_service.py) | 更新 Plex 位置重试的自然语言日志。 |
| [scripts/unraid/telepiplex-publish.sh](../../scripts/unraid/telepiplex-publish.sh) | 更新 User Scripts 展示名称、描述、默认提交文案及提示；执行逻辑不变。 |
| [sdk/pyproject.toml](../../sdk/pyproject.toml) | 更新 SDK 包描述，保留包名、版本和构建配置。 |
| [sdk/src/telepiplex_plugin_sdk/logging_utils.py](../../sdk/src/telepiplex_plugin_sdk/logging_utils.py) | 更新诊断传输失败提示。 |
| [sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py](../../sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py) | 更新模块 docstring 的品牌称呼；合同内容不变。 |
| [tests/test_deployment_contract.py](../../tests/test_deployment_contract.py) | 同步中英文 README 品牌文案及 Release 展示标题断言。 |
| [tests/test_release_workflow.py](../../tests/test_release_workflow.py) | 同步步骤展示名称和 Host Release 标题断言。 |
| [tests/test_unraid_publish_script.py](../../tests/test_unraid_publish_script.py) | 同步默认提交文案断言。 |
| [tools/build_tpx.py](../../tools/build_tpx.py) | 更新 CLI 帮助中的产品称呼。 |
| [tools/generate_release_catalog.py](../../tools/generate_release_catalog.py) | 更新 CLI 帮助中的产品称呼。 |
| [tools/pressure_telegram_pipeline.py](../../tools/pressure_telegram_pipeline.py) | 更新模拟 Bot 的展示名称，保留 username 和结构化标识。 |
| 本报告 | 记录逐文件清单、实际验证与交付边界。 |

## 实际验证

以下命令在项目根目录执行；Sync 测试单独在其模块目录执行。

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_command_catalog.py tests/test_bot_runtime_startup.py \
  tests/test_interaction_handler.py tests/test_logger.py tests/test_message_cleanup.py \
  tests/test_deployment_contract.py tests/test_technical_identity_migration.py \
  tests/test_release_workflow.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_deployment_contract.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_unraid_publish_script.py tests/test_diagnostics.py tests/test_pressure_telegram_pipeline.py

(cd features/sync && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_sync_service.py)

/bin/bash -n scripts/unraid/telepiplex-publish.sh
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

- 首组：173 passed、69 subtests passed，另有 3 个旧 README 文案断言失败；均定位为品牌大小写/Release 展示标题变化。
- 修正部署文档断言后，仅复跑对应文件：12 passed、24 subtests passed。未将首组描述为全量复跑通过。
- 发布脚本、SDK 诊断、Telegram 压测工具测试：30 passed、5 subtests passed。发布相关测试使用临时 fake Git 记录器，不调用真实 Git。
- Sync 服务测试：60 passed、32 subtests passed。
- Bash 语法检查与工作区标记检查通过。未运行全项目所有测试。

附加只读/临时验证：

- SHA-256 比对：258 个历史文件保持原样，包括 docs 中的旧计划、审计、归档和 `.superpowers` 快照。
- 6 个协议相关文件保持原样：MCP 服务、Host 海报 User-Agent、115 客户端及 Douban/Wikipedia/Wikidata 适配器。
- 修改的 Python 文件 AST 解析通过；修改前后 NAME token 序列完全一致，未改函数名、变量名、类名或导入身份。
- 两份工作流的触发条件、job ID 和 needs 与基线一致。提取 Release 创建命令，用 shell 函数替代 gh 做本地模拟：`telepiplex-v3.6.12` 对应标题 `Telepiplex 3.6.12`，`search-v2.2.1` 对应标题 `Telepiplex search 2.2.1`。
- 直接调用帮助渲染函数验证 `/start` 标题为 `Telepiplex`。
- 在临时源码副本中调用 setuptools `prepare_metadata_for_build_wheel`：SDK、caption、echo 的 Summary 含 `Telepiplex`，Name/Version 与各自 pyproject 完全一致。未手工改写本地旧 build、egg-info 或已发布包。

## 交付

等待 Syncthing 显示 `Up to Date / 最新`，同步至 `/mnt/user/archives/life hacker/telepiplex`。由于修改了 `scripts/unraid/telepiplex-publish.sh`，用户还需将其同步替换到 Unraid User Scripts 的实际脚本；展示名称为 `Telepiplex Publish`，脚本文件名不变。版本升级与正式发布由用户在 Unraid 按既有不可变版本流程处理，本次未升版，也未覆盖旧发布内容。
