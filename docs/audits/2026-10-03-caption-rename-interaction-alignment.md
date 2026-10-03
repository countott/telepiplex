# Caption 交互对齐 Rename

日期：2026-10-03。Caption **1.1.2 → 1.2.0**，配套 Host **3.9.0 → 3.9.1**。其余 Feature、SDK 与 Host API 版本保持。

## 完成行为

- `/caption` 与 `/caption scan` 打开同一个任务面板：选择 Rename 的目录、翻页、输入其他目录或单独查找字幕。目录每页 8 项，全部目录可达；自定义输入支持返回。
- 选择目录或使用 `/caption scan /目录` 后，先完整读取媒体分页并展示视频数；用户点击“开始补字幕”后才查找、下载和写入。
- 同名候选展示编号、中外标题、年份、国家／地区及媒体类型，支持按钮与数字回复。媒体库批次遇到候选歧义或缺原始语言时，暂停当前文件，确认后继续当前文件及后续批次。
- 选择、确认、进度、取消和终态沿用同一个 operation 与 Caption 文本面板。未确认退出不写字幕；已写入文件在取消后保留；旧按钮和迟到后台结果不会复活已取消任务。
- Host 将文本输入会话与所属 operation 绑定，后台任务终态后，下一条消息会清理残留绑定并恢复正常路由。旧终态结果不能清除新会话，旧式无 operation 配置会话保持可用。
- 保留独立 query、`--source`、自动下载接续、质量检查和 chi/cht 命名。`/caption_config` 本轮未重构；候选为文本展示，不声称新增海报展示。沿用 Host 文本会话 30 分钟有效期，极长批次可继续通过候选按钮确认。

## 文件清单

16 个修改、3 个新增；无删除或重命名。

| 类型 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | [Caption service.py](../../features/caption/src/telepiplex_caption/service.py) | 统一命令菜单、分页和输入流程；扫描确认、批次候选暂停续跑、编号输入与取消控制。 |
| 新增 | [Caption interaction.py](../../features/caption/src/telepiplex_caption/interaction.py) | 与 Rename 对齐的候选文字展示、编号按钮及有界字段处理。 |
| 修改 | [Caption store.py](../../features/caption/src/telepiplex_caption/store.py) | 临时扫描批次不重复写入任务摘要；重启继续沿用中断语义。 |
| 修改 | [Caption test_service.py](../../features/caption/tests/test_service.py) | 验证确认前无写入、批次恢复、分页、重复点击、输入、权限、过期和取消竞态。 |
| 新增 | [Caption test_interaction.py](../../features/caption/tests/test_interaction.py) | 验证候选展示、回调、类型、稀疏字段和长度边界。 |
| 修改 | [plugin_handler.py](../../app/handlers/plugin_handler.py) | 绑定与清理 operation 文本会话，防止终态残留会话吞消息及异步渲染竞态。 |
| 修改 | [test_plugin_handler.py](../../tests/test_plugin_handler.py) | 覆盖终态会话清理、正常文本路由、新旧会话竞态与旧配置兼容。 |
| 修改 | [115bot.py](../../app/115bot.py) | Host 版本提升为 3.9.1。 |
| 修改 | [test_bot_runtime_startup.py](../../tests/test_bot_runtime_startup.py) | 同步 Host 版本断言。 |
| 修改 | [Caption manifest.yaml](../../features/caption/manifest.yaml) | 插件版本提升为 1.2.0。 |
| 修改 | [Caption pyproject.toml](../../features/caption/pyproject.toml) | 包版本同步为 1.2.0。 |
| 修改 | [test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 同步 Caption 版本合同。 |
| 修改 | [Caption README](../../features/caption/README.md) | 说明新交互链路、配套版本和配置边界。 |
| 修改 | [Caption CHANGELOG](../../features/caption/CHANGELOG.md) | 新增 1.2.0 更新记录。 |
| 修改 | [Search README](../../features/search/README.md) | 同步当前配套 Caption 版本。 |
| 修改 | [中文 README](../../README.md) | 更新当前能力、版本表和升级顺序。 |
| 修改 | [英文 README](../../README_EN.md) | 同步英文能力说明、版本表和升级顺序。 |
| 修改 | [update.md](../../update.md) | 新增 Host 3.9.1 会话修复说明，保留历史条目。 |
| 新增 | [本交付记录](2026-10-03-caption-rename-interaction-alignment.md) | 记录变更范围、实际验证及交付方式。 |

## 实际验证

最终三组验证合计 **664 passed，103 subtests passed**：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_plugin_handler.py tests/test_interaction_handler.py \
  tests/test_technical_identity_migration.py tests/test_bot_runtime_startup.py \
  tests/test_product_name_casing.py tests/test_caption_host_contract.py
# 216 passed, 101 subtests passed in 4.83s

cd /Users/young/Documents/telepiplex/features/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
# 406 passed in 10.68s

cd /Users/young/Documents/telepiplex/features/rename
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_caption_bridge.py tests/test_inventory.py tests/test_operations.py
# 42 passed, 2 subtests passed in 0.28s
```

首次 Caption 全套运行时，原 `/tmp/telepiplex-caption-test-deps` 中的临时依赖已不在，缺 OpenCC 导致 42 项质量相关测试失败，缺解包依赖导致 6 项跳过。通过以下命令将已声明依赖装入隔离临时目录后，完整重跑得到上述 406 项全部通过；没有修改或放宽字幕质量规则：

```bash
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pip install --disable-pip-version-check \
  --target /tmp/telepiplex-caption-test-deps -r features/caption/requirements-feature.txt
```

项目边界检查实际通过：

```bash
test ! -e .git && test ! -e .worktrees && test -d .stfolder
```

验证为本地自动化测试；未连接 Telegram Bot、115 或字幕网站执行线上任务，未重建插件包或镜像，也未重跑整个项目所有模块的完整测试。

## 交付

等待 Syncthing 显示 **Up to Date / 最新**，同步到 Unraid `/mnt/user/archives/life hacker/telepiplex` 后，由用户检查并发布。已有依赖版本齐备时，先升级 **Host 3.9.1**，再升级 **Caption 1.2.0**。本轮未执行 Git、创建标签或发布。
