# download 自定义可视化配置恢复设计

## 背景

模块化前，Telegram `/config` 可以进入 115 配置，选择 Access/Refresh Token 路线，依次发送 Access token 与 Refresh token，并在写入后重新初始化 115 客户端。隔离式 download Feature 当前只允许 `/auth` 选择已经手工写入私有配置的 Token；它声明的 `/config` 又被 Telepiplex 自己的 `/config` 命令占用。与此同时，Telepiplex 的通用 schema 配置器只展示嵌套标量区块，而 download 的授权字段位于 schema 根层，因此 download 不会出现在可视化配置列表。

本次恢复旧交互能力，同时保留 Feature 隔离：Telepiplex 只负责发现和转交自定义配置会话，download 负责 115 授权字段、交互状态与运行时生效。

## 用户交互

1. 用户发送 `/config`。
2. Telepiplex 列出可配置 Feature，其中包含 `download`。
3. 用户选择 `download` 后，Telepiplex 将请求分派给 download 声明的自定义配置命令。
4. download 显示两个按钮：
   - `Access / Refresh Token`
   - `115 扫码授权`
5. Token 路线依次提示用户发送 Access token、Refresh token。只有两项均有效时才写入配置；成功后立即替换运行中客户端的 Token。
6. 扫码路线沿用现有 PKCE 设备授权流程，成功后写入同一份 Feature 私有配置并更新运行中客户端。
7. `/q`、会话超时、Feature 停用或更新会结束本次配置；未完成的 Token 不落盘。

## Telepiplex 契约

Feature 可以在其 `config.schema.json` 根层声明：

```json
{
  "x-telepiplex-config-command": "config"
}
```

该值必须是 Feature manifest 已声明的命令。Telepiplex 的 `/config` 列表同时包含以下两类 Feature：

- 存在通用 schema 标量配置区块的 Feature；
- 声明有效 `x-telepiplex-config-command` 的 Feature。

选择自定义配置 Feature 时，Telepiplex 通过当前原子路由快照调用该 Feature 的 `command.dispatch`，并复用动态 Feature 网关现有的 action 渲染与会话保存逻辑。Telepiplex 不读取或解释 download 的 token 字段，也不在回调数据中携带 token。

若声明缺失、命令未在 manifest 中注册、Feature 路由不可用或分派失败，Telepiplex 返回经过清洗的稳定错误，不创建会话。普通 schema 配置流程保持不变。

## download 会话与持久化

download 的 `config` 命令返回授权方式按钮并打开 Feature 会话。会话状态仅保存在 Feature 内存中，按 `(chat_id, user_id)` 隔离，并带有与 Telepiplex Feature 会话一致的 30 分钟有效期：

- `choose_mode`：等待选择 Token 或扫码路线；
- `access_token`：等待 Access token；
- `refresh_token`：暂存 Access token，等待 Refresh token。

进入暂存 Access token 的阶段时，download 同时启动一个受 runtime 管理的到期清理任务；新会话、完成、取消或进程关闭会替换或取消该任务。即使用户在超时后不再发送消息，暂存的 Access token 也会按时从内存会话中删除。

Token 路线收到第二项后，调用现有 `FeatureConfigStore.write_tokens(..., auth_mode="direct")`。该存储器继续以 `0600` 权限写临时文件、`fsync` 并原子替换 `/config/plugins/download/config.yaml`。写入成功后更新 `self.config` 并调用 `client.set_tokens(...)`，无需重启 Feature 进程即可立即生效。

扫码路线继续使用 `auth_mode="scan"`。自动刷新仍通过 `on_tokens_changed` 回调写回同一配置文件，保持当前授权模式。

为兼容已有入口，`/auth` 与 `/config → download` 共用同一授权菜单与状态机；`/auth` 不再只提供“启用已存在 Token”的降级行为。

## 安全与错误处理

- Access token、Refresh token 不出现在回复、日志、异常文本、回调数据或持久化会话数据库中。
- 第一步收到的 Access token 只短暂存在 download 进程内存；完成、取消、30 分钟超时、Feature 停用或更新时丢弃。
- 空值、占位值与多行值视为无效，停留在当前输入阶段并给出不含原值的提示。
- 写入失败时保留原配置和原客户端 Token；仅在原子写入成功后切换内存状态。
- Telepiplex 继续只允许 `allowed_user` 使用 `/config`；Feature 网关维持同一授权检查。
- Token 有效性由后续 115 API 调用及现有刷新逻辑验证；本次不新增向第三方服务发送额外验证请求。

## 分支与文件边界

`main`：

- 扩展 schema 配置发现，使自定义配置命令成为可选通用契约；
- 抽取并复用 Feature result 渲染/会话保存逻辑；
- 保持 Telepiplex 不含 download 字段名或业务判断。

`feature/115`：

- 在 schema 声明自定义配置命令；
- 恢复 Access/Refresh Token 两步录入状态机；
- 让 `/auth` 与 `config` 共用授权菜单；
- 保留现有扫码、原子写回、自动刷新和纯 storage/download 边界。

本次不修改其他 Feature，不把模块源码合并回 Telepiplex，不更新远端分支。

## 测试与验收

Telepiplex 回归测试证明：

- download 一类仅声明自定义配置命令的 Feature 会出现在 `/config` 列表；
- 选择后请求被分派给正确的 Feature 命令，并保存 Feature 返回的开放会话；
- 无效声明、不可用路由、未授权请求和错误消息均安全失败；
- 原有通用 schema 配置流程继续通过。

download 回归测试证明：

- `config` 与 `auth` 显示 Token/扫码两个入口；
- Token 路线严格按 Access 再 Refresh 的顺序收集；
- 只收到 Access、输入无效、取消或超时时不写配置；
- 两项完整后只进行一次原子写入，保留其他配置，切换为 `direct` 并立即更新客户端；
- 扫码成功仍写入 `scan`，自动刷新仍保持授权模式；
- 所有 action、异常与日志中均不包含 token 原值。

完成后分别运行两个分支的目标测试、完整测试、Python 编译检查与仓库 whitespace 检查。只有两边均通过才视为恢复完成。
