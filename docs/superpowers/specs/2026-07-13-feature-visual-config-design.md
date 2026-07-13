# Feature 可视化配置设计

## 背景

模块化前的 `/config` 能通过 Telegram 按钮配置 TVDB、AI 等服务。模块化后，配置已经迁移到 `/config/plugins/<plugin_id>/config.yaml`，每个 Feature 也随包提供 `config.schema.json`，但 Core 只实现了安装、更新和启停，没有提供 schema 驱动的配置入口。

## 决策

Core 提供通用 `/config` 可视化配置器，不在 Core 中硬编码 `media-search`、`renaming` 或 Plex 的业务字段。Feature 使用标准 JSON Schema 的 `title`、`description` 和 `writeOnly` 描述表单；Core 只展示 schema 中声明的标量字段。

配置器按“Feature -> 配置区块 -> key=value 输入”工作：

1. `/config` 列出已安装且含可编辑区块的 Feature。
2. 选择 Feature 后列出 schema 中的标量对象区块，例如 `metadata.tvdb`、`ai`、`search.prowlarr`。
3. 提示当前非敏感值；`writeOnly` 字段只显示“已配置/未配置”，绝不回显真实值。
4. 用户只发送需要修改的字段；未发送字段保持不变，显式空值用于清空字符串字段。
5. Core 进行类型转换和完整 schema 校验，原子写入 Feature 私有 `config.yaml`，文件权限保持 `0600`。
6. 已运行的 Feature 先 drain，再以新配置启动 shadow 进程并原子切换路由；启动失败时恢复旧配置和旧进程。

复杂数组和自由结构暂不在 Telegram 表单中编辑，例如 `category_folder` 与 115 的 `save_directories`。115 授权继续由 open115 自己的 `/auth` 管理，保持扫码授权与 Access/Refresh Token 两条独立路线。

## 安全边界

- API Key、Token、Secret、Password 使用 `writeOnly: true`。
- 消息和错误路径不记录配置 payload；错误继续经过 Core 的敏感值清洗。
- 回调数据只携带会话内索引，不携带配置值。
- 配置先验证、后写入；写入和运行时切换任一步失败都不得留下部分状态。

## 本次覆盖

- `media-search`: Prowlarr、Wikipedia、TVDB、AI。
- `renaming`: TVDB、AI，以及已有标量选择参数。
- `plex-management`: Plex、TMDB、Fanart.tv、AI、MCP 的已有 schema 字段。
- `open115`: 保留 Feature 自己的授权 UI，不通过通用配置器重复管理 token。

## 验收标准

- `/config` 和 `/plugin` 中的“配置 Feature”入口可达。
- AI 与 TVDB 字段在对应 Feature 中可见、可写且立即生效。
- API Key 不在状态文案、提示、日志或异常中回显。
- 无效类型或违反 schema 的输入不会修改磁盘配置。
- Feature 热重载失败时恢复原配置与原路由。

