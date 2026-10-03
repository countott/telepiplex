# Host 发布检查失败修复

日期：2026-10-03。Host **3.9.1 → 3.9.2**，Caption 保持 **1.2.0**。

## 根因和修复

用户提供的 `telepiplex-v3.9.1` 日志显示，失败发生在镜像构建前的 `Run Host tests`：`tests/test_caption_business_flow.py:242` 的两个参数化用例（简中 ASS、繁中 SRT）仍期待 `/caption scan` 扫描后直接完成。Caption 1.2.0 已改为扫描完成后等待用户确认，因此实际状态为 `awaiting_input`。日志结果为 2 failed、728 passed、1 skipped、333 subtests passed。

本地先运行简中 ASS 用例，复现同一断言失败。修复后测试先验证等待确认、目录视频数量及确认前没有新增元数据查询、字幕查询、下载、上传分块或存储写入，再从实际按钮取得 callback，通过 Host Unix-socket RPC 的 `callback.dispatch` 确认，验证同一 operation 完成及已有字幕幂等命中。没有回退确认交互，没有跳过测试，也没有改变 Caption 业务代码。

旧失败标签仍指向旧源码。新 Host 版本用于承载本次修复；没有查询远端新版本是否存在，也未代用户操作标签或发布。

## 文件清单

7 个修改、1 个新增；无删除或重命名。

| 类型 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | [test_caption_business_flow.py](../../tests/test_caption_business_flow.py) | 跨模块测试经过真实扫描确认回调，并验证确认前无副作用。 |
| 修改 | [115bot.py](../../app/115bot.py) | Host 版本更新为 3.9.2。 |
| 修改 | [test_bot_runtime_startup.py](../../tests/test_bot_runtime_startup.py) | 同步 Host 版本断言。 |
| 修改 | [README.md](../../README.md) | 同步中文当前版本、升级顺序及修复说明。 |
| 修改 | [README_EN.md](../../README_EN.md) | 同步英文当前版本、升级顺序及修复说明。 |
| 修改 | [Caption README](../../features/caption/README.md) | 当前配套 Host 更新为 3.9.2，保留历史能力说明。 |
| 修改 | [update.md](../../update.md) | 新增 3.9.2 发布检查修复记录，保留旧版本条目。 |
| 新增 | [本交付记录](2026-10-03-host-build-caption-confirmation-fix.md) | 记录失败证据、修复范围、实际验证及交付方式。 |

## 实际验证

修复后的两项跨模块用例先单独运行：**2 passed in 2.82s**。随后运行发布流程要求的 Host 与五个 Feature 完整测试：

| 测试范围 | 结果 | 用时 |
| --- | --- | --- |
| Host `tests` | 730 passed、1 skipped、333 subtests passed | 118.91 秒 |
| Download | 180 passed、33 subtests passed | 2.26 秒 |
| Search | 694 passed、2 skipped、152 subtests passed | 43.96 秒 |
| Rename | 413 passed、28 subtests passed | 7.12 秒 |
| Sync | 160 passed、80 subtests passed | 4.43 秒 |
| Caption | 406 passed | 6.22 秒 |
| 合计 | **2,583 passed、3 skipped、626 subtests passed** | |

Rename 另有一条现有 `logger.warn` 弃用提示，未导致失败。所有测试命令退出码均为 0。实际命令：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_caption_business_flow.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

set -e
for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

PYTHONPYCACHEPREFIX=/tmp/telepiplex-build-fix-pycache PYTHONPATH=.:sdk/src \
  "$PY" -m compileall -q app sdk tools tests

test ! -e .git && test ! -e .worktrees && test -d .stfolder
```

编译检查及工作区边界检查均通过。`/tmp/telepiplex-caption-test-deps` 提供项目已声明的字幕质量和解包测试依赖；编译缓存写入临时目录。本次验证在 Mac 本地执行，未执行 GitHub Actions、Docker 镜像构建或线上发布。

## 交付

等待 Syncthing 显示 **Up to Date / 最新**，同步至 Unraid `/mnt/user/archives/life hacker/telepiplex` 后，由用户从包含修复的源码发布 **Host 3.9.2**。直接重跑旧 3.9.1 标签任务不会包含本次修复。本轮未执行 Git、修改远端标签或发布。
