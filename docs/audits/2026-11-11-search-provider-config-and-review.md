# Telepiplex 搜索 Provider 配置与 115 方案复核

本轮在 Mac 本地修改、测试源码，未发布。Search 2.10.0 → 2.11.0；Download 2.2.3 → 2.2.4。Host、SDK 和其他 Feature 未改版。

## Provider 配置

入口：`/config → search → Provider 接口参数 → Provider → 字段`。也可从 `/search_config` 进入。原有 Prowlarr、TMDB、TVDB 快速配置入口保留。

编辑现有适配器使用的参数，不增加新的 Provider 或认证协议：

| Provider | 参数 |
| --- | --- |
| Prowlarr | 服务地址、API Key、总超时、单站超时、状态超时、站点 IDs、优先站点 IDs、其他站点延迟、结果数量 |
| TMDB | 启停、API 地址、API Read Access Token、超时 |
| TVDB | 启停、API 地址、API Key、Subscriber PIN、超时 |
| AniList | 启停、GraphQL 地址、超时 |
| Wikipedia | 启停、语言顺序、超时、请求间隔、每轮查询数、限流冷却 |
| 豆瓣 | 启停、超时、缓存时长、并发数、连续失败阈值及冷却 |

跨 Provider 修改保存在会话草稿中；点击“保存修改 → 确认保存”才返回嵌套 `config_patch`。复用 Host 原有合并、schema 校验、原子写入及重新加载流程。退出、超时或其他用户的回调不提交草稿。只提交实际选择过的字段，其余配置保留。

敏感值只展示“已配置 / 未配置”；输入 `-` 保留当前值，凭据支持 `clear` 清空。启停输入 `on` / `off`。数值按现有 schema 范围验证，拒绝 NaN、无穷值和整数项的小数。站点 IDs 接受去重后的正整数，启用站点可用 `-2`，优先列表可用 `clear` 清空。URL 必须为带主机的 HTTP(S) 地址，不接受内嵌用户名或密码。没有修改配置 schema 或默认配置。

交付前审查补齐了配置链路中的三处问题，并先用回归复现失败：

- 配置向导原来只把按钮投影到 Host 任务卡，字段页与输入提示文字会被固定状态文案覆盖。现在由 Search 把向导的安全文案同步到配置任务状态，并用文本消息段展示；真实 Host 投递回归覆盖当前值、字段提示、非法输入、暂存提示及密钥不回显。
- 豆瓣 `cache_ttl=0` 原来被默认值 `900` 覆盖；现在保留显式零值，回归检查保存后的值实际进入 Provider。
- Prowlarr `status_timeout` 原来被适配器静默限制到 `5–30` 秒，与公开 schema 不一致；现在遵守已有 `1–300` 秒范围，回归检查保存边界值进入真实 HTTP 调用参数。

## 115 复核结论

上轮日志只能确认：下载清理后和整理期间，文件列表触发 `listing ended before its continuation or count` 或 `listing exceeds declared count`。两个诊断包没有保存当时原始分页响应，无法确定接口实际返回的 count、offset、记录数及其对应的查询状态，也不能认定是服务端短暂延迟。

因此撤回上轮的两个自动读重试：单页矛盾不再自动重取，清理后完整文件树复核也不再循环重扫。重复读取不应被称作根因修复。必要的一次清理后完整复核继续保留；分页完整性、文件 ID、根 ID 和内容身份校验没有放宽，不把错误当成空目录，不重复下载、删除或移动。

保留明确的当前目录与稳定排序查询参数、分页诊断、Storage 具体错误码，以及“下载完成但尚未整理”的事实提示。这些分别用于消除账户记忆设置的影响、保留故障证据和准确表达任务状态，不能证明 115 根因已经解决。目录聚合大小与 SHA1 不作为文件内容身份的规则也继续保留。

本次不能宣称已修复用户账户的分页异常。下一次实际错误仍需依据请求与响应的分页事实定位接口返回或协议适配问题，不能仅凭当前旧日志继续假定根因。

上一轮的下载前原始片源确认、空目录清理与计数修复继续保留。旧交付记录保持历史原文，其中有限读重试方案由本记录取代。

## 文件清单

新增 2 个文件，修改 22 个文件；无删除、重命名。以下为本轮相对于上一轮交付的增量。

| 文件 | 操作与目的 |
| --- | --- |
| `features/search/src/telepiplex_search/config_wizard.py` | 修改：Provider 参数菜单、类型校验、敏感值状态、跨 Provider 草稿及统一保存 |
| `features/search/src/telepiplex_search/service.py` | 修改：配置文案投影、文本消息段、豆瓣显式零缓存时长 |
| `features/search/src/telepiplex_search/adapters/prowlarr.py` | 修改：状态超时与公开配置范围一致 |
| `features/search/tests/test_provider_config.py` | 新增：多 Provider 编辑、敏感值、非法输入、所有权、取消、schema 对齐、消息投影与实际 Provider/HTTP 参数回归 |
| `features/search/tests/test_config_wizard.py` | 修改：原有配置菜单增加 Provider 参数入口的预期 |
| `features/search/manifest.yaml` | 修改：Search 2.11.0 |
| `features/search/pyproject.toml` | 修改：Search 包版本 |
| `features/search/README.md` | 修改：Provider 配置入口、字段和使用说明 |
| `features/search/tests/test_feature_service.py` | 修改：版本及本地构建示例断言 |
| `features/search/tests/test_config_schema_contract.py` | 修改：Search 版本断言 |
| `features/download/src/telepiplex_download/client.py` | 修改：撤回矛盾分页自动重取，保留一次请求及失败诊断 |
| `features/download/src/telepiplex_download/service.py` | 修改：撤回清理后文件树复核循环，保留一次完整复核和身份校验 |
| `features/download/tests/test_listing_consistency.py` | 修改：矛盾后一次失败，不以稍后成功的响应掩盖最初错误 |
| `features/download/tests/test_completion_integrity.py` | 修改：复核失败停止、不重复删除、目录统计不改变文件身份 |
| `features/download/manifest.yaml` | 修改：Download 2.2.4 |
| `features/download/pyproject.toml` | 修改：Download 包版本 |
| `features/download/tests/test_feature_runtime.py` | 修改：Download 版本及构建示例断言 |
| `features/download/README.md` | 修改：说明撤回自动重试及根因尚未确认 |
| `tests/test_config_handler.py` | 修改：Host `/config` 实际分发到 Search Provider 配置入口，以及真实 Host 任务卡文案与敏感值保护回归 |
| `tests/test_technical_identity_migration.py` | 修改：当前 Feature 版本断言 |
| `README.md` | 修改：本轮行为、版本与交付记录入口 |
| `README_EN.md` | 修改：对应英文版本与升级说明 |
| `docs/media-release-format-topology.md` | 修改：对应 Search 2.11.0，识别规则未改写 |
| `docs/audits/2026-11-11-search-provider-config-and-review.md` | 新增：本交付与方案复核记录 |

## 实际本地验证

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

# 分别在 features/search 和 features/download 目录执行
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests --tb=short

# 在项目根目录执行
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_config_handler.py tests/test_technical_identity_migration.py \
  tests/test_feature_builder.py tests/test_product_name_casing.py \
  tests/test_storage_safety_rpc.py --tb=short

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_plugin_handler.py -k config
```

- Search 最终全量：3605 passed、2 skipped、246 subtests passed。
- Download 全量：265 passed、33 subtests passed。
- 根目录配置/身份/构建/产品名称/存储最终回归：73 passed、1 skipped、58 subtests passed。
- Host 配置补丁保存与重载回归：13 passed、67 deselected。
- Search、Download 的 manifest、包与 README 版本一致；`.git`、`.worktrees` 不存在，`.stfolder` 保留。

已完成独立只读代码审查，发现的配置文案投影与参数生效问题均已修正。审查者另行复测真实 Host 文案投递、Provider 实参和 Download 两处定向用例，通过且未发现未解决的重要代码问题；115 线上根因未确认的限制仍然成立。

测试使用本地传输、HTTP mock 或测试存储，没有连接用户的 115 账户、索引器或 Telegram，也没有提交实际下载。本轮没有重跑其他 Feature 或 Host 全量，上一轮结果不计作本轮全量结果。

等待 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 发布。本轮更新 Search 和 Download；若尚未安装上一轮修复，同时更新 Rename 2.4.4。Mac 不执行 Git 或发布。
