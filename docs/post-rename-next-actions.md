# 整理后继续操作

适用版本：Host 3.8.1（Host API 1.8）、Search 2.4.0、Rename 2.3.0、Sync 2.1.0。SDK 仍为 2.2.0。Host 3.8.1 包含下文的发布测试修复。

## 交互

完整整理成功且 rename 消息段已封存后，Host 另发一张「请选择下一步」卡：

- 「继续找这部剧」：存在本用户已确认的剧集上下文时显示。创建新任务和新消息，恢复选择范围前的季集目录，重新选择全剧／季／集，随后实时搜索片源。
- 「扫描 Plex」：Sync 已启用且 Plex 地址、Token 均已配置时显示。点击才连接 Plex，优先扫描本次分类对应的库；没有映射时进入现有媒体库选择。仅提交扫描，不自动执行海报、音轨或字幕增强。
- 「退出（60s）」：按钮沿用项目通用「退出」文案并附剩余时间。倒计时每 5 秒刷新，截止时间固定为消息送达后 60 秒，刷新或重启不会延长。

点击任一有效按钮先持久化消费、删除选择卡，再执行下一步。同一张卡只能消费一次；到期自动退出和删除，超时后到达的点击不会启动任务。退出不回滚整理结果，rename 成功记录始终保留。用户已开始其他任务时，旧选择卡会关闭，不覆盖新任务。

选择卡和完成记录分开持久化：不把已完成的 rename 改回运行态，不重放文件操作。继续搜索和 Plex 扫描通过用户命令通道启动，并携带原任务关联 ID。失败、部分完成或源目录清理未完成时不显示此卡。

选择卡状态写入 Host 数据库的 `next_action_cards`。删除失败会每 5 秒重试，重启后继续清理；发送消息的结果不确定时不自动重发，以免出现重复卡片。旧完成记录不会在重启后重新生成选择卡。

## 元数据缓存

Search 的 `state/content_cache.db` 使用独立于下载任务的 JSON 缓存，最多 512 条，按最近访问淘汰：

| 内容 | 有效期 |
| --- | --- |
| 候选发现结果 | 15 分钟 |
| 已读取的作品／季集资料 | 连载或状态未知 15 分钟；完整已完结目录 24 小时 |
| 本用户已确认的剧集上下文 | 最多 7 天；季集目录仍受上面的有效期限制 |

查询缓存包含配置指纹；资料缓存包含来源锚点、媒体身份和季集范围，避免同名作品或不同范围串用。候选发现命中后仍按原流程确认作品。新任务重建计划 ID、搜索会话 ID 和消息；缓存不保存片源选择、下载链接或任务执行状态。

已确认上下文保存完整候选及范围选择之前的目录。目录过期后沿已确认来源重新读取，不按片名重新猜作品；重复使用不延长原目录有效期。已播状态在展示范围时按当前日期计算。

**Prowlarr 片源结果不缓存，每次实时查询。**

## 升级和本地验证

先更新 Host，再更新 Search 和已安装的 Sync，最后更新 Rename。三个 Feature 均要求 Host API 1.8；未安装 Sync 不影响下载和整理。升级后新完成的任务才使用此入口。

本地验证使用项目指定 Python、`PYTHONDONTWRITEBYTECODE=1` 和 `pytest -p no:cacheprovider`，覆盖选择卡消费／超时／重启清理、缓存隔离／时效、新任务范围恢复、Plex 配置与目标库路由，以及原有 Host 和 Feature 回归。

Mac 只修改和本地验证。等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid `/mnt/user/archives/life hacker/telepiplex` 检查并发布。

## 本次改动文件

新增 7 个文件、修改 26 个文件；无删除或重命名。

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| [README.md](../README.md) | 修改 | 更新当前版本、行为说明和升级／构建示例。 |
| [README_EN.md](../README_EN.md) | 修改 | 更新当前版本、行为说明和升级／构建示例。 |
| [app/115bot.py](../app/115bot.py) | 修改 | 接入选择卡后台清理、启动关闭生命周期，并升级 Host 版本。 |
| [app/handlers/interaction_handler.py](../app/handlers/interaction_handler.py) | 修改 | 验证并分发独立下一步卡的回调。 |
| [app/runtime/next_actions.py](../app/runtime/next_actions.py) | 新增 | 持久化选择卡、一次性消费、倒计时、删除重试和用户命令接力。 |
| [app/runtime/plugin_contract.py](../app/runtime/plugin_contract.py) | 修改 | 声明 Host API 1.8。 |
| [docs/post-rename-next-actions.md](../docs/post-rename-next-actions.md) | 新增 | 记录交互、缓存、升级顺序、逐文件改动和验证。 |
| [features/rename/README.md](../features/rename/README.md) | 修改 | 更新当前版本、行为说明和升级／构建示例。 |
| [features/rename/manifest.yaml](../features/rename/manifest.yaml) | 修改 | 升级 Feature 版本与 Host API 最低要求。 |
| [features/rename/pyproject.toml](../features/rename/pyproject.toml) | 修改 | 同步 Feature 包版本。 |
| [features/rename/src/telepiplex_rename/service.py](../features/rename/src/telepiplex_rename/service.py) | 修改 | 仅在完整成功回执中附加下一步上下文。 |
| [features/rename/tests/test_feature_processor.py](../features/rename/tests/test_feature_processor.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [features/rename/tests/test_next_actions.py](../features/rename/tests/test_next_actions.py) | 新增 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [features/search/README.md](../features/search/README.md) | 修改 | 更新当前版本、行为说明和升级／构建示例。 |
| [features/search/manifest.yaml](../features/search/manifest.yaml) | 修改 | 升级 Feature 版本与 Host API 最低要求。 |
| [features/search/pyproject.toml](../features/search/pyproject.toml) | 修改 | 同步 Feature 包版本。 |
| [features/search/src/telepiplex_search/content_cache.py](../features/search/src/telepiplex_search/content_cache.py) | 新增 | 提供有容量和时效边界的持久化元数据缓存。 |
| [features/search/src/telepiplex_search/runtime.py](../features/search/src/telepiplex_search/runtime.py) | 修改 | 接入 Search 状态目录的内容缓存数据库。 |
| [features/search/src/telepiplex_search/service.py](../features/search/src/telepiplex_search/service.py) | 修改 | 复用候选与季集目录，按原来源刷新，并创建同剧集新任务。 |
| [features/search/tests/test_config_schema_contract.py](../features/search/tests/test_config_schema_contract.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [features/search/tests/test_content_cache.py](../features/search/tests/test_content_cache.py) | 新增 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [features/search/tests/test_feature_service.py](../features/search/tests/test_feature_service.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [features/sync/README.md](../features/sync/README.md) | 修改 | 更新当前版本、行为说明和升级／构建示例。 |
| [features/sync/manifest.yaml](../features/sync/manifest.yaml) | 修改 | 升级 Feature 版本与 Host API 最低要求。 |
| [features/sync/pyproject.toml](../features/sync/pyproject.toml) | 修改 | 同步 Feature 包版本。 |
| [features/sync/src/telepiplex_sync/feature.py](../features/sync/src/telepiplex_sync/feature.py) | 修改 | 提供只读配置可用性查询，并处理用户确认后的目标库扫描。 |
| [features/sync/tests/test_feature_runtime.py](../features/sync/tests/test_feature_runtime.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [features/sync/tests/test_post_rename_scan.py](../features/sync/tests/test_post_rename_scan.py) | 新增 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [tests/test_bot_runtime_startup.py](../tests/test_bot_runtime_startup.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [tests/test_deployment_contract.py](../tests/test_deployment_contract.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [tests/test_next_actions.py](../tests/test_next_actions.py) | 新增 | 验证新增行为或同步当前版本／Host API 合同断言。 |
| [tests/test_plugin_manager.py](../tests/test_plugin_manager.py) | 修改 | 验证模块查询和升级解析使用当前 Host API 1.8。 |
| [tests/test_technical_identity_migration.py](../tests/test_technical_identity_migration.py) | 修改 | 验证新增行为或同步当前版本／Host API 合同断言。 |

## 实际验证结果（2026-09-16）

以下为修改后的最终运行结果，不包含中途用于定位问题的重复运行：

| 范围 | 结果 | subtests | 用时 |
| --- | --- | --- | --- |
| Host（排除两个 Git／发布模拟测试文件） | 677 passed、1 skipped | 272 passed | 82.04 s |
| Search 全量 | 677 passed、2 skipped | 152 passed | 13.65 s |
| Rename 全量 | 393 passed | 29 passed | 7.17 s |
| Sync 全量 | 160 passed | 80 passed | 4.45 s |

合计 1,907 项测试和 533 个 subtests 通过，3 项跳过。Rename 留有一条原有 `logger.warn` 弃用提示。两个排除文件是 `tests/test_unraid_publish_script.py` 和 `tests/test_release_workflow.py`，避免在 Mac 执行 Git／发布模拟。

实际测试命令：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests \
  --ignore=tests/test_unraid_publish_script.py \
  --ignore=tests/test_release_workflow.py --tb=short

for module in search rename sync; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done
```

另外已检查所有改动 Python 文件可解析，三个 Feature 的 manifest／pyproject 版本一致且均声明 Host API `>=1.8,<2.0`；确认本地无 `.git`、无 `.worktrees`，`.stfolder` 存在。未修改 Download、Caption 或 SDK，未重跑这两个 Feature 的独立测试。

已覆盖新任务独立消息、回调绑定、任意按钮消费、60 秒截止、重复点击、重启删除重试、完整季集目录恢复、目录过期刷新、用户／作品／范围隔离，以及 Prowlarr 重复实时调用。本地测试使用模拟 Telegram／Plex 和资料源，未进行真实 Telegram／Plex 端到端操作，也未声称获得生产速度测量结果。

## Host 3.8.1：发布测试修复（2026-09-16）

用户提供的 `telepiplex-v3.8.0` 发布日志显示：Host 测试 700 passed、3 failed、1 skipped，三个失败均在 `tests/test_unraid_publish_script.py`。测试将当前 Feature manifest／pyproject 复制到临时目录，却用固定旧版本断言发布标签；升级 Search、Rename、Sync 后，输入与预期不再一致。此前本地排除该测试文件，未发现这个问题。

发布脚本测试现使用独立的固定版本样本，保持远端标签、输入版本、断言处于同一场景。新增任意版本 `9.12.34` 的用例，验证脚本仍从样本文件读取版本；单 Feature 发布场景同时断言不会连带发布其他 Feature。

测试仅在临时脚本副本替换 VCS 命令和目录标记，采用封闭 PATH 与离线替身；不执行真实 Git、不创建 `.git`、不连接仓库。生产发布脚本和 CI 工作流保持原样。版本号升级为 Host 3.8.1，供下次发布创建新标签；不移动已经创建的 3.8.0 标签，Feature／SDK／Host API 版本保持不变。

本轮修改 6 个文件，无新增、删除或重命名：

| 文件 | 用途 |
| --- | --- |
| [tests/test_unraid_publish_script.py](../tests/test_unraid_publish_script.py) | 隔离版本样本、封闭离线测试环境、补充任意版本和仅发布目标 Feature 的验证。 |
| [app/115bot.py](../app/115bot.py) | Host 版本升为 `v3.8.1-host`。 |
| [tests/test_bot_runtime_startup.py](../tests/test_bot_runtime_startup.py) | 同步 Host 版本断言。 |
| [README.md](../README.md) | 更新中文版本表和升级顺序。 |
| [README_EN.md](../README_EN.md) | 更新英文版本表和升级顺序。 |
| [docs/post-rename-next-actions.md](post-rename-next-actions.md) | 记录本轮故障原因、修复和验证。 |

本轮 Host 回归分两条命令覆盖整个 `tests` 目录，未遗漏上次排除的发布测试：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests \
  --ignore=tests/test_release_workflow.py --tb=short

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_release_workflow.py
```

实际结果：第一组 683 passed、1 skipped、275 subtests passed（97.87 s）；第二组 21 passed、35 subtests passed（19.66 s）。合计 **704 passed、1 skipped、310 subtests passed**。失败测试所在文件另已单独运行，6 passed、3 subtests passed（4.00 s 左右），不重复计入合计。

改动 Python 文件语法解析通过；确认本地无 `.git`／`.worktrees` 且 `.stfolder` 存在。本轮未改动 Feature 业务代码，未重跑各 Feature 独立套件，也未代替 GitHub Actions 宣称远端验证已通过。等待 Syncthing `Up to Date / 最新` 后，由用户在 Unraid 发布 Host 3.8.1；直接重跑原 3.8.0 工作流仍会使用旧标签对应的源码。
