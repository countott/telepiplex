# Telepiplex 集中配置交付

本轮按“代码清理 → 配置策略 → 集中化实现”推进，设计经用户确认后实施。所有工作在 Mac 本地完成；没有执行 Git、连接本项目 GitHub 或发布。

## 最终配置归属

唯一用户配置为 `/config/config.yaml`：

- Bot、日志：原有顶层字段。
- 插件安装和运行环境：`plugins`。
- 四分类目录/Plex 库映射与 TMDB 凭据：`shared`。
- 五个插件各自的服务参数和业务策略：`features.<plugin_id>`。

Telegram `/config` 保留插件向导，并增加公共设置；确认保存统一写入主文件。公共草稿按预期旧值校验，保存时遇到并发修改会拒绝覆盖。运行进程仅接收自己的有效配置快照，快照不是另一份用户编辑入口。

首次升级保留原主文件，Host 导入原插件 YAML 并保留原文件。非空公共值冲突时停止迁移、只报告字段和来源；不要先用新模板覆盖旧主文件。集中模式要求兼容 Host 写回协议的 Download；旧 Download 会提示更新，Host 本身仍可用于安装和管理。

代码版本回滚采用目标版本默认值并保留最新显式用户设置和授权。候选插件启动期间配置发生变化时拒绝切换，保留并发的新值。115 刷新和主动授权使用字段级比较与本地凭据代次保护；取消只恢复自己仍拥有的授权字段。

## 版本

| 组件 | 本地源码版本 |
| --- | --- |
| Host / Host API | 3.10.0 / 1.10 |
| SDK | 2.3.0 |
| Download | 2.3.0 |
| Search | 2.11.2 |
| Rename | 2.4.6 |
| Sync | 2.1.3 |
| Caption | 1.3.5 |

五个 Feature 的 SDK 精确依赖同步到 2.3.0，以匹配构建时随包打入的 SDK wheel。Search、Sync 只对齐依赖和说明，业务策略不变。

## 文件清单

下表仅列本次配置工作；同工作区另一个用户 chat 的消息卡片重试改动保留，不计为本轮配置实现。两个 chat 都编辑的文件，仅描述本轮负责的配置片段。没有重命名文件。

| 文件 | 操作与目的 |
| --- | --- |
| `app/runtime/central_config.py` | 新增：主 YAML、公共字段投影、稀疏默认合成、字段事务、旧配置迁入 |
| `app/runtime/plugin_store.py` | 修改：集中后端接入、候选迁移及 schema/default 校验，生产不再创建私有权威 YAML |
| `app/runtime/plugin_manager.py` | 修改：共享消费者事务、原始补丁、实例一致性、最新设置回滚、Download 写回 |
| `app/runtime/plugin_supervisor.py` | 修改：逐实例配置快照、写权限提升/撤销、重启快照 |
| `app/runtime/runtime_broker.py` | 修改：认证的配置读取与作用域写回接口 |
| `app/runtime/plugin_manifest.py` | 修改：配置写回协议声明与非法声明诊断 |
| `app/runtime/plugin_contract.py` | 修改：Host API 1.10 |
| `app/115bot.py` | 修改：生产集中后端接线、旧配置迁入、统一重载与失败恢复、Host 版本 |
| `app/handlers/config_handler.py` | 修改：公共配置入口、共享会话状态和绝对路径输入 |
| `app/handlers/shared_config_handler.py` | 新增：目录/Plex 映射、TMDB 凭据草稿与确认保存 |
| `app/handlers/plugin_handler.py` | 修改：集中模式直接转发原始配置补丁，避免旧完整快照覆盖新 Token |
| `app/config.yaml.example` | 修改：完整分区主配置模板 |
| `config/config.yaml.example` | 修改：与运行时模板一致的完整主配置示例 |
| `sdk/src/telepiplex_plugin_sdk/host_client.py` | 修改：配置读取及带预期值的补丁 RPC |
| `sdk/pyproject.toml` | 修改：SDK 2.3.0 |
| `features/download/src/telepiplex_download/config_store.py` | 修改：Host-backed 配置适配器、字段比较、授权恢复 |
| `features/download/src/telepiplex_download/runtime.py` | 修改：集中模式适配器与刷新来源凭据写回 |
| `features/download/src/telepiplex_download/client.py` | 修改：延迟刷新 CAS、延迟扫码授权提交、凭据代次保护 |
| `features/download/src/telepiplex_download/service.py` | 修改：主动授权事务与取消后使用实际当前凭据 |
| `features/download/manifest.yaml` | 修改：Host 写回协议、API 下限及 Download 版本 |
| `features/download/pyproject.toml` | 修改：版本与 SDK 精确依赖 |
| `features/download/README.md` | 修改：主文件分区、授权写回、当前版本与构建样例 |
| `features/rename/config.default.yaml` | 修改：删除闲置 TVDB 默认配置 |
| `features/rename/config.schema.json` | 修改：删除闲置 TVDB 区块与必填要求 |
| `features/rename/src/telepiplex_rename/config_wizard.py` | 修改：移除闲置 TVDB 流程，保留 AI 配置 |
| `features/rename/src/telepiplex_rename/runtime.py` | 修改：停止透传闲置 metadata 配置 |
| `features/rename/src/telepiplex_rename/tvdb.py` | 删除：无生产消费者的旧 TVDB API 适配器 |
| `features/rename/migrations/config-1-to-2.json` | 新增：删除旧私有 metadata 区块，保留其他用户设置 |
| `features/rename/manifest.yaml` | 修改：配置 schema 版本与包版本 |
| `features/rename/pyproject.toml` | 修改：版本与 SDK 依赖 |
| `features/rename/README.md` | 修改：集中配置、TVDB 清理及版本说明 |
| `features/caption/config.schema.json` | 修改：复用相同 provider 定义，并声明现有 caption_config 向导 |
| `features/caption/manifest.yaml` | 修改：Caption 版本 |
| `features/caption/pyproject.toml` | 修改：版本与 SDK 依赖 |
| `features/caption/README.md` | 修改：集中配置与向导入口说明 |
| `features/search/manifest.yaml` | 修改：Search 依赖对齐版本 |
| `features/search/pyproject.toml` | 修改：版本与 SDK 依赖 |
| `features/search/README.md` | 修改：公共字段、主文件分区、当前版本与样例 |
| `features/sync/manifest.yaml` | 修改：Sync 依赖对齐版本 |
| `features/sync/pyproject.toml` | 修改：版本与 SDK 依赖 |
| `features/sync/README.md` | 修改：公共字段与主文件分区说明 |
| `examples/echo_feature/pyproject.toml` | 修改：构建示例对齐 SDK 2.3.0 |
| `tests/test_central_config.py` | 新增：主存储、迁移、显式值与字段事务回归 |
| `tests/test_central_config_lifecycle.py` | 新增：集中生命周期、共享切换、失败恢复及草稿冲突回归 |
| `tests/test_shared_config_handler.py` | 新增：公共 Telegram 草稿、确认、路径、所有权和密钥保护 |
| `features/download/tests/test_central_config_store.py` | 新增：真实 Unix RPC、刷新/授权/取消并发及持久化失败回归 |
| `tests/test_plugin_store.py` | 修改：集中 Store 行为回归 |
| `tests/test_plugin_supervisor.py` | 修改：快照路径、实例权限及子进程环境回归 |
| `tests/test_runtime_broker.py` | 修改：认证配置协议、越权及退役实例回归 |
| `tests/test_plugin_manifest.py` | 修改：写回声明校验 |
| `tests/fixtures/plugin_processes/fake_python.py` | 修改：验证运行快照及配置模式的子进程 fixture |
| `tests/test_plugin_manager.py` | 修改：当前 Host API 版本断言 |
| `tests/test_bot_runtime_startup.py` | 修改：生产配置路径、集中重载与 Host 失败恢复 |
| `tests/test_config_handler.py` | 修改：Caption 配置可发现性 |
| `tests/test_caption_host_contract.py` | 修改：当前 Host API 的可安装依赖链契约 |
| `tests/test_telepiplex_surface.py` | 修改：主配置集中分区与旧模块表面契约 |
| `tests/test_plugin_handler.py` | 修改：原始补丁转发、防旧完整快照覆盖 |
| `tests/test_config_template_contract.py` | 修改：完整主配置及五插件 schema 投影契约 |
| `tests/test_deployment_contract.py` | 修改：当前 Host API/SDK 版本契约 |
| `tests/test_feature_builder.py` | 修改：当前 SDK 构建依赖契约 |
| `tests/test_technical_identity_migration.py` | 修改：当前 Feature/SDK 身份版本 |
| `features/download/tests/test_feature_runtime.py` | 修改：当前版本/API/SDK 构建契约 |
| `features/rename/tests/test_config_wizard.py` | 修改：AI 配置及旧 TVDB 回调拒绝 |
| `features/rename/tests/test_config_schema_contract.py` | 修改：无需旧元数据凭据的有效配置 |
| `features/rename/tests/test_feature_processor.py` | 修改：当前版本及构建示例契约 |
| `features/rename/tests/test_tvdb_adapter.py` | 删除：仅测试已移除闲置适配器的专用测试 |
| `features/search/tests/test_config_schema_contract.py` | 修改：当前 Search 版本 |
| `features/search/tests/test_feature_service.py` | 修改：当前版本/SDK 及构建契约 |
| `features/sync/tests/test_feature_runtime.py` | 修改：当前版本/SDK 及构建契约 |
| `README.md` | 修改：配置分区、迁移、兼容下限与版本 |
| `README_EN.md` | 修改：对应英文说明 |
| `docs/superpowers/specs/2026-11-16-centralized-config-design.md` | 新增：经确认的集中化策略与边界 |
| `docs/superpowers/plans/2026-11-16-centralized-config.md` | 新增：实施任务与接口 |
| `docs/superpowers/plans/2026-11-16-centralized-config-progress.md` | 新增：阶段验证与审查记录 |
| `docs/audits/2026-11-16-centralized-config.md` | 新增：本交付记录 |

生成的 build/egg-info、Python 缓存及测试临时文件不是源文件交付，不手工修改。Rename 在干净临时源码目录构建的 `.tpx` 已检查：携带删除迁移，不包含旧 tvdb.py，保留 tvdb_rename.py。

## 实际验证

使用 `/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，设置 `PYTHONDONTWRITEBYTECODE=1`，pytest 使用 `-p no:cacheprovider`。

| 范围 | 实际结果 |
| --- | --- |
| Search 全量 | 3605 passed，2 skipped，246 subtests |
| Rename 全量 | 518 passed，26 subtests；一条原有 logger.warn 废弃警告 |
| Sync 全量 | 160 passed，80 subtests |
| Caption 全量 | 500 passed，6 skipped |
| Download 最终全量 | 276 passed，33 subtests |
| 中央存储及 Store | 42 passed |
| 集中生命周期及 Manager | 67 passed，3 subtests |
| Host 最终全量 | 859 passed，1 skipped，353 subtests；171.31 秒 |
| 独立整合审查 | 最终审查及单次修复复审通过；无未解决的重要代码问题 |

Caption 首轮因测试运行时缺少项目已声明的 `opencc-python-reimplemented` 失败；已将 0.1.7 安装在 `/tmp/telepiplex-caption-validation-deps` 并加入该轮 PYTHONPATH，随后全量通过。未改项目依赖来掩盖失败。测试使用本地进程、传输和模拟存储，不连接用户 115、Telegram 或索引器服务。

Host 首轮全量为 853 passed、1 skipped、349 subtests、4 failed：两个 `test_caption_business_flow` 参数场景同样缺 OpenCC，加入临时依赖路径后定向 2 passed；另两个为 `test_caption_host_contract` 仍要求 API 1.9、`test_telepiplex_surface` 仍禁止主配置含业务区，与本次批准的 API 1.10/集中配置契约冲突。最终整合按新契约更新相应测试，再执行带完整已声明依赖的 Host 全量，不把首轮失败当作通过。

最终 Host 全量通过；五个 Feature 合计 5059 passed、8 skipped。跳过项保持现有测试条件，没有为本次集中化新增跳过。独立最终审查的两项反馈均已修正并复审确认，无未解决的重要代码问题。没有执行容器部署、用户账户上的业务操作或发布；另一 chat 的消息卡片改动保留，并包含在最终本地 Host 验证环境中。

本轮实际命令形式如下；Feature 命令分别在各自目录运行，Caption 加入上述临时 OpenCC 路径：

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

# 项目根目录：最终 Host 全量
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/tmp/telepiplex-caption-validation-deps:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --tb=short

# features/download、search、rename、sync 内分别运行
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --tb=short

# features/caption 内
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/tmp/telepiplex-caption-validation-deps:src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --tb=short

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

## 交接

等待 Syncthing 显示 `Up to Date / 最新`，再由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 检查并发布。旧 Download 的更新与其他 Feature 更新仍从 Telegram 或用户控制的发布流程执行；Mac 不代为发布。

## 自动迁移与版本号补充复核

用户再次确认升级是否需要手工整体迁移，并要求相关插件均提升版本号。本次只补齐文档残留，不重复增加已经提升的源码版本：Host 3.10.0/API 1.10、SDK 2.3.0、Download 2.3.0、Search 2.11.2、Rename 2.4.6、Sync 2.1.3、Caption 1.3.5；五个 Feature 的 manifest、pyproject 与 SDK 精确依赖一致。

实际代码在 `build_plugin_manager()` 创建集中存储后、启动插件前调用 `migrate_legacy()`。首次读取旧主文件时一次性导入旧插件配置，通过校验后原子写入 `config_version: 2`；后续启动检测到该标记便跳过导入。旧文件保留。公共值冲突或旧配置不合法时停止，不写入部分迁移结果；修正冲突项后再启动。升级前保留旧主配置，不用新模板替换或手工提前添加已迁移标记。

本次修改文件：

| 文件 | 目的 |
| --- | --- |
| `README_EN.md` | 当前集中化组合改为 Host 3.10.0/API 1.10、Caption 1.3.5、SDK 2.3.0 |
| `features/download/README.md` | 当前 SDK 描述从 2.2.2 对齐 2.3.0，历史发布说明保持原文 |
| `features/search/README.md` | 当前配套 Caption 从 1.3.4 对齐 1.3.5 |
| `features/caption/README.md` | 当前推荐组合对齐本轮 Host/Download/Search/Rename，保留插件最低 API 1.9 的真实声明 |
| `docs/audits/2026-11-16-centralized-config.md` | 记录本补充复核、文件用途与实际验证 |

实际运行：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider \
  tests/test_central_config.py tests/test_technical_identity_migration.py \
  tests/test_deployment_contract.py --tb=short
```

结果：48 passed、24 subtests passed，4.73 秒。本次没有重跑上一节的全量测试，不将上一轮全量结果描述为本次新跑结果。没有执行 Git 或发布；等待 Syncthing `Up to Date / 最新` 后交由用户在 Unraid 处理发布。
