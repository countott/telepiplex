# Telepiplex 远程 Feature 更新发现设计

日期：2026-07-13

状态：已确认方向，自动执行

目标分支：feature/telepiplex-core

对应业务决策：OPS-TODO-01B

## 1. 目标

Core 能够安全读取 GitHub Release 的远程 catalog，比较已安装 Feature 与兼容的最新稳定版本，通过 Telegram 主动通知 allowed_user，并在用户点击一次确认按钮后调用现有 PluginManager.update 流程。

默认行为只发现和通知，不静默下载、不静默更新。Core 镜像更新仍由 Unraid 拉取并允许重启一次，不属于本子项目。

## 2. Catalog 来源与缓存

plugins.catalog 同时接受：

- 本地绝对或相对 catalog.yaml 路径。
- 仅允许 HTTPS 的远程 catalog URL。

默认模板改为 countott/telepiplex 最新 GitHub Release 的 catalog.yaml URL。远程响应限制大小，拒绝 HTTPS 降级重定向；下载内容先验证 YAML 与版本结构，再通过 fsync 和 os.replace 原子写入 plugins cache。刷新失败时保留上一次有效缓存，不覆盖为坏内容。

解析 release 时要求 plugin ID、semver、64 位小写 SHA-256、HTTPS tpx URL、core_api 和 source branch/commit 合法。现有 name@version resolve 继续复用同一缓存和 digest 校验。

## 3. 更新比较

PluginCatalog 暴露 available_updates(installed_versions, core_api_version)：

- 只比较当前 active Feature。
- 使用 PEP 440 Version 比较 semver。
- 只选择大于当前版本的最高稳定版本。
- catalog core_api 必须包含当前 Core API。
- 无效或不兼容 release 被忽略并记录，不得成为更新候选。
- 返回 plugin_id、current_version、target_version、artifact reference、source commit 和 digest。

PluginManager 暴露 async available_updates，负责从 PluginStore 取得 active versions，并委托 catalog。没有 catalog 能力的自定义 resolver 返回空列表而不是破坏 Manager。

## 4. 轮询与通知

Core 启动并恢复 Feature 后立即执行一次更新检查，再按 plugins.catalog_refresh_interval 秒轮询；默认 21600 秒，最小 300 秒。

每次检查：

1. 刷新远程 catalog。
2. 取得兼容更新。
3. 对未通知过的 plugin/current/target 发送 Telegram 消息。
4. 消息列出当前版本、目标版本和 source commit。
5. 提供确认更新与暂不处理两个按钮。

内存中记录本进程已通知键，避免轮询重复轰炸。Core 重启后可以再次提醒尚未安装的更新。catalog 或 Telegram 暂时失败只记录警告，不阻止 Core、Bot 或 Feature 运行。

## 5. 一次确认

Core 专用 callback namespace 为 core-plugin-update，先于通用 Feature callback gateway 注册。

确认 payload 只接受严格的 plugin_id@semver。处理步骤：

1. Telegram query.answer。
2. 再次验证用户权限。
3. 编辑消息为更新处理中。
4. 调用 manager.update(plugin_id@target_version)。
5. 复用现有下载 digest 校验、shadow 启动、稳定检查、drain、原子切换和失败回滚。
6. 成功后编辑为新版本与状态；失败时显示经过脱敏的稳定错误码。

暂不处理只关闭本条提醒，不改变 Feature。没有按钮点击时绝不调用 update。

## 6. 生命周期

更新 monitor task 存在 application.bot_data。应用停止时先 cancel 并 await task，再关闭 PluginManager，防止 catalog 请求和 manager.close 竞争。

测试和本地运行可以直接调用单次 check，不需要等待真实 interval。

## 7. 配置

两份模板保持字节一致，并包含：

- plugins.catalog 为远程 latest catalog URL。
- plugins.catalog_refresh_interval 为 21600。

仍允许用户把 catalog 改回本地路径。README 同时说明远程发现、非静默更新和离线本地 catalog 回退。

## 8. 测试与验收

- 远程 catalog 成功刷新、缓存原子更新、HTTPS 降级、超限、坏 YAML 和旧缓存保留均有测试。
- catalog 版本比较覆盖最新版本、相同版本、旧版本、不兼容 Core API、无效 digest 和多个插件。
- PluginManager 只比较 active releases。
- 启动单次检查只发送一个通知，第二次不重复。
- 未点击按钮不调用 manager.update。
- 未授权 callback 不更新。
- 确认 callback 精确调用 manager.update(plugin@version)，成功和脱敏失败均有测试。
- monitor failure 不阻止 Core startup；shutdown 会取消 task。
- app/config.yaml.example 与 config/config.yaml.example 字节一致。
- Core 完整测试、compileall、YAML 解析和 git diff check 通过。
- 本轮只提交本地 feature/telepiplex-core，不推送。
