# Telepiplex 品牌校验修复与全套自检交付

本轮修复用户日志中 telepiplex-v3.6.13 的 Host 验证失败。根因是旧 tests/test_product_name_casing.py 将 Telepiplex 作为全仓库禁用字符串，与新规则冲突。此前针对性验证漏掉了该文件；本轮已完成全部 Host 和五个 Feature 本地测试。

## 版本范围

- Host：`3.6.13 → 3.6.14`，预期发布标签为 `telepiplex-v3.6.14`。
- SDK `2.1.1`、download `2.1.1`、search `2.2.2`、rename `2.1.2`、sync `2.0.2`、caption `0.1.5`、echo 示例 `1.0.1` 均沿用上一轮版本。本次只修改 Host 测试、Host 版本及文档，没有改变这些包的源码或依赖。
- 没有改动 `.github/workflows` 或发布脚本生产逻辑。旧 3.6.13 标签不移动、不覆盖；新提交需走新 Host 标签。未访问远端核验标签占用情况，Unraid 发布脚本会按现有规则检查。

## 文件清单

修改 11 个已有文件；新增本报告 1 个文件；无删除或重命名。

| 文件 | 用途 |
| --- | --- |
| [README.md](../../README.md) | 更新 Host 3.6.14 版本表，说明品牌校验修复与其余版本沿用。 |
| [README_EN.md](../../README_EN.md) | 同步英文版版本表与修复说明。 |
| [app/115bot.py](../../app/115bot.py) | Host 从 3.6.13 升补丁版本至 3.6.14，给修复提交提供新发布身份。 |
| [features/caption/README.md](../../features/caption/README.md) | 将“随 Host 3.6.13 发布”改为建议搭配 Host 3.6.14；模块版本不变。 |
| [features/download/README.md](../../features/download/README.md) | 将“随 Host 3.6.13 发布”改为建议搭配 Host 3.6.14；模块版本不变。 |
| [features/rename/README.md](../../features/rename/README.md) | 将“随 Host 3.6.13 发布”改为建议搭配 Host 3.6.14；模块版本不变。 |
| [features/search/README.md](../../features/search/README.md) | 将“随 Host 3.6.13 发布”改为建议搭配 Host 3.6.14；模块版本不变。 |
| [features/sync/README.md](../../features/sync/README.md) | 将“随 Host 3.6.13 发布”改为建议搭配 Host 3.6.14；模块版本不变。 |
| [tests/test_bot_runtime_startup.py](../../tests/test_bot_runtime_startup.py) | 更新当前 Host 版本断言。 |
| [tests/test_product_name_casing.py](../../tests/test_product_name_casing.py) | 移除过时的全仓库大写品牌禁令；分别校验展示品牌、包/模块/Python 技术标识、工作流标题/身份、User-Agent/MCP 和 README 版本表。只读取现行入口与源码，不扫描历史资料或生成目录。 |
| [tests/test_unraid_publish_script.py](../../tests/test_unraid_publish_script.py) | 更新 Host 发布夹具和待发布标签断言；保留 Feature 版本和发布算法。 |
| 本报告 | 记录根因、版本、修改范围和完整本地验证结果。 |

## 同类问题自检

- 现行 tests、app、SDK、tools、Feature 源码/测试、工作流及 AGENTS 中未发现第二处全小写品牌禁令；修复后无旧禁用规则残留。
- 发现五份模块 README 对 Host 3.6.13 的固定发布描述，已修正。中英文 README 版本表现在由新测试与 Host/SDK/Feature 源码版本动态比对，防止以后漏改。
- 旧版本测试夹具只用于历史场景，不进行全局替换；当前版本断言已由各套完整测试验证。
- 新品牌检查包含六项反向验证：在临时副本中分别改错 README 品牌、包名、工作流标题、Python 标识符、MCP 名称或 Host 版本表，均被对应测试拒绝；同时放入历史文档和生成副本，未产生误报。
- 本轮基线中的 260 个历史文件 SHA-256 完全一致。协议文件、SDK/Feature 源码、manifest、pyproject 和工作流均未变化。
- 411 个 Python 文件在内存中 compile 通过；未为语法检查写入工作区字节码。`.git`、`.worktrees` 不存在，`.stfolder` 保留。

## 实际测试

六套测试在各自目录独立执行，以下为等价复现命令；实际使用 `tee` 将每套完整输出保存至本地临时目录。`PIP_NO_INDEX=1` 禁用测试打包阶段的索引下载。

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider --tb=short tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider --tb=short tests
  )
done

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

| 测试套件 | 实际输出摘要 |
| --- | --- |
| host | 691 passed, 1 skipped, 302 subtests passed in 129.87s (0:02:09) |
| download | 174 passed, 33 subtests passed in 3.58s |
| search | 661 passed, 2 skipped, 121 subtests passed in 15.65s |
| rename | 385 passed, 24 subtests passed in 10.75s |
| sync | 157 passed, 80 subtests passed in 8.23s |
| caption | 1 passed in 0.06s |

**合计：2069 passed、3 skipped、560 subtests passed，0 failed。** Host 跳过项是未提供完整成套 .tpx 的产物矩阵；search 的 2 个跳过项是显式真实网络测试。未将它们算作通过，也未验证线上容器。发布脚本测试使用临时 fake Git 记录器，不执行真实 Git。

在六套完整测试之前，单独执行新品牌测试：6 passed、42 subtests passed。它们已包含在 Host 完整测试中，不重复计入上述合计。

原始本地日志：

- [host.log](/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-brand-gate-tv25ryqd/host.log)
- [download.log](/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-brand-gate-tv25ryqd/download.log)
- [search.log](/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-brand-gate-tv25ryqd/search.log)
- [rename.log](/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-brand-gate-tv25ryqd/rename.log)
- [sync.log](/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-brand-gate-tv25ryqd/sync.log)
- [caption.log](/var/folders/k9/9rj8jyqd5xx32zk99n825y980000gp/T/telepiplex-brand-gate-tv25ryqd/caption.log)

## 交付

等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 `/mnt/user/archives/life hacker/telepiplex` 检查并发布新 Host 版本。此次只新增 Host 3.6.14 发布目标；是否还有上一轮未完成的 Feature 标签，由 Unraid 发布脚本依据远端状态判断。此前品牌改动后的 `scripts/unraid/telepiplex-publish.sh` 若尚未替换到 User Scripts，仍需用户完成替换；本轮未再次修改该脚本。

本轮没有 Git、远端查询、标签操作或发布。前序审计交付报告作为历史记录保持原样。
