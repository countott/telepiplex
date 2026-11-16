# Telepiplex 升级顺序与失败重试恢复

## 结论与诊断证据

诊断包：`20261116T122704+0800-FF43CA0A62A6.zip`。包内内容仅作为诊断数据读取，没有执行其中的文本。以下时间为 UTC+8，行号指包内 `telepiplex.machine.jsonl`。

| 时间 | 记录与结果 | 行号 |
| --- | --- | --- |
| 12:28:06–34 | Search 2.11.1 → 2.11.2，候选通过健康检查，但缺少 download.provider，随后停止 | 25–36 |
| 12:28:41–12:29:19 | Download 2.2.5 → 2.3.0 成功，状态 active | 37–45 |
| 12:29:22–49 | Rename 2.4.5 → 2.4.6，候选通过健康检查，但缺少 media.search，随后停止 | 46–57 |
| 12:30:29 | 管理页显示 Download/Sync healthy，旧 Search/Rename/Caption absent | 59 |
| 12:30:32–47 | 再试 Search 2.11.2，直接返回 release_exists，没有启动候选 | 60–62 |

错误位于 Telegram `user_surface.text`，这些事件的级别仍为 INFO，不能仅按 error.code 或 ERROR 级别过滤。日志有源码 revision，但没有 Host 语义版本。配置使用运行时快照；日志不能独立区分本次首次迁移和此前已经迁移。

根因是 Host 的更新事务先完成安装环境并提交 `releases/<version>`，之后才检查路由依赖。激活失败时恢复旧 active 并停止候选，但保留了已提交版本；重试再次提交同名目录便报 release_exists。用户调整顺序后仍不能重试，是 Host 的恢复缺陷。

## 本次修复

1. 在创建 staging、安装 venv、提交版本之前，使用真实路由规则检查依赖、命令/能力冲突及现有依赖者。失败不创建候选目录、不启动进程、不修改配置。
2. 对此前已提交但未激活的版本，允许同一已验证安装包重新走原有激活、配置迁移、健康检查及失败恢复流程。
3. 复用前核对 SHA-256、manifest、包内逐文件内容以及可执行环境。不同安装包占用相同版本、元数据损坏或内容不一致时拒绝，不覆盖不可变版本目录。
4. 配置文件、持久状态和旧运行实例保持原有事务保护。不自动安装或更新依赖，不通过删除用户版本目录绕过错误。

Host 从 3.10.0 提升到 **3.10.1**，Host API 保持 1.10。SDK 和 Feature 代码没有变化，继续使用 SDK 2.3.0、Download 2.3.0、Search 2.11.2、Rename 2.4.6、Sync 2.1.3、Caption 1.3.5。

## 用户恢复步骤

日志中 Download 2.3.0 已成功。修复版发布前，可在 Telegram 依次执行以下命令，恢复已安装版本的功能；已运行时会按现有逻辑返回状态：

```text
/plugin enable search
/plugin enable rename
/plugin enable caption
```

发布并升级 Host 3.10.1 后，可以在管理页重试原来的更新，也可依次执行：

```text
/plugin update search@2.11.2
/plugin update rename@2.4.6
/plugin update caption@1.3.5
/plugin update sync@2.1.3
```

无需删除 `/config/plugins/<plugin_id>/releases/<version>`，也无需重置或重新填写主配置。原版本安装包必须与发布的校验和一致；受损环境不属于本次自动复用范围。

## 文件清单

新增 2 个文件、修改 9 个文件；无删除、重命名。

| 文件 | 操作与目的 |
| --- | --- |
| `app/runtime/plugin_manager.py` | 修改：准备安装前预检路由，并复用已验证失败候选 |
| `app/runtime/plugin_store.py` | 修改：只读核验已提交版本的身份、内容及环境 |
| `tests/test_upgrade_recovery.py` | 新增：7 项失败恢复、顺序、不可变内容及数据保留回归 |
| `tests/test_plugin_manager.py` | 修改：依赖缺失时应在准备/启动前失败的契约 |
| `app/115bot.py` | 修改：Host 3.10.1 展示版本 |
| `tests/test_bot_runtime_startup.py` | 修改：当前 Host 版本契约 |
| `README.md` | 修改：当前 Host 版本与恢复说明 |
| `README_EN.md` | 修改：对应英文版本与恢复说明 |
| `features/download/README.md` | 修改：推荐搭配 Host 3.10.1 |
| `features/caption/README.md` | 修改：当前组合搭配 Host 3.10.1 |
| `docs/audits/2026-11-16-upgrade-order-recovery.md` | 新增：诊断证据、文件清单、恢复和验证记录 |

## 实际验证

新增回归先复现 **5 failed、2 passed**，失败分别覆盖提交后重试冲突和错误顺序留下候选。修复后的重点场景包括真实 Store 启动恢复：旧 Download 因协议门槛隔离、Search/Rename 因依赖隔离，随后恢复同一目标安装包；以及健康失败后重复重试、首次安装失败重试、同版本不同内容拒绝、损坏元数据/包内容拒绝。

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_upgrade_recovery.py tests/test_plugin_manager.py \
  tests/test_central_config_lifecycle.py tests/test_plugin_store.py
```

结果：**88 passed、9 subtests passed**，11.41 秒。

```bash
PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=/tmp/telepiplex-caption-validation-deps:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --tb=short
```

Host 最终全量：**866 passed、1 skipped、359 subtests passed**，189.06 秒。临时 OpenCC 目录用于提供项目已声明、测试运行时缺失的依赖。没有修改 Feature 业务代码，本轮不将此前五个 Feature 的全量结果计为本轮新跑结果。

已完成独立只读代码审查，未发现未解决的重要代码问题。没有连接用户的 Telegram、115 或索引器，没有修改 Unraid 运行目录，没有执行 Git 或发布。等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 检查、发布并更新 Host。
