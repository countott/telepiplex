# Feature 命令目录筛选设计

日期：2026-07-16  
目标分支：`main`

## 目标

Telepiplex 的 `/start` 帮助和 Telegram 显式命令列表只展示可以独立发起任务的
Feature 命令，同时保留命令路由与旧入口兼容性。

## 展示规则

- Telepiplex 命令 `/start`、`/reload`、`/plugin`、`/config` 保持外显。
- search 外显 `/search`、`/s`。
- download 外显 `/magnet`、`/m`。
- 配置命令 `/search_config`、`/auth`、`/rename_config` 不外显。
- 会话控制命令 `/q` 不外显，因为交互中已有显式退出按钮。
- 未启用或不可路由的 Feature 继续不外显。
- 一个 Feature 过滤后没有命令时，不显示空分组。
- sync 将来启用时外显 `/plex`，不外显 `/sync_config`。

## Manifest 契约

`commands[]` 增加可选布尔字段 `menu_visible`：

- `true`：明确外显。
- `false`：明确隐藏。
- 省略：为兼容已经发布且没有该字段的 Feature 1.1.0，Telepiplex 隐藏命令名后缀为
  `_config` 的命令以及 `auth`、`q`，其他命令保持外显。

显式值优先于兼容规则，因此未来 Feature 可以不依赖命令命名表达展示意图。
非布尔值必须作为无效 manifest 拒绝。

## Telepiplex 数据流

manifest 解析器保留每条命令的 `menu_visible`。命令目录提供唯一的共享筛选函数，
`build_bot_commands()` 与 `build_start_help()` 都只消费筛选后的声明，避免两个界面
出现不同结果。筛选不修改 CapabilityRouter 的命令注册，因此隐藏命令仍能被直接调用。

## 测试与发布

- manifest 测试覆盖可选布尔字段、非法类型和显式值覆盖兼容规则。
- 命令目录测试覆盖别名保留、配置/控制命令隐藏、空 Feature 分组省略，以及
  `/start` 与 Telegram 命令列表一致。
- 运行 Telepiplex 完整测试、编译、工作流与 whitespace 检查。
- 只推送 `main`，不修改或发布 Feature 分支。
- 创建 `telepiplex-v1.1.1` 标签，等待 GitHub Actions 完成 Telepiplex 测试、GHCR 镜像和 Release。
