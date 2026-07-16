# Plex Management Feature

`feature/plex-management` 是纯 Feature 源码分支。Core 将其构建为不可变 `.tpx`，并在 Core 容器内以独立 venv/子进程运行。

## 从 1.1.x 升级

1.2.0 删除了本地 AI 配置。更新 Feature 前，先编辑 `/config/plugins/plex-management/config.yaml`，删除整个 `ai:` 配置段并保留其他现有值，然后再执行更新。

Core 对删除或改名的配置字段采用 fail-closed 策略；如果旧 `ai:` 段仍在，更新会返回 `config_migration_required`，当前 1.1.x release 和配置保持不变。删除该段后，剩余 Plex、TMDB、Fanart.tv、分类目录和 MCP 配置可直接通过 1.2.0 schema 验证。

## 自动管线

Feature 订阅 `media.organized`。每个事件只创建一个持久化 Job，并按 `category_folder[].plex_library_id` 对最终媒体文件分组；同一个 Job 内每个 Plex 媒体库只扫描一次，再按每个 `final_path` 定位条目。Plex 自己负责识别、匹配和基础元数据，插件只执行：

```text
scanning -> artwork -> audio -> subtitle -> completed
```

部分文件定位失败时，已定位文件继续增强并记录 warning。任务只有完整执行后才标记 `completed`；进程停止时的活动任务标记 `interrupted`。原子 claim 和持久化步骤结果用于避免重复执行已经完成的工作。

## Telegram 命令

- `/plex`：查看最近 Job；传入 Job ID 可查看单个任务和待处理选择。
- `/scan`：实时列出 Plex 媒体库，扫描一个库或全部库。它是独立手动操作，不创建自动管线 Job，也不执行 artwork、audio、subtitle。
- `/plex_config`：交互配置 Plex、TMDB 和 Fanart.tv。MCP 仅通过 YAML 配置。

`/scan` 的媒体库选择本身就是执行意图，点击后不会再要求二次确认。

## 配置与 MCP

运行时配置位于 `/config/plugins/plex-management/config.yaml`；仓库中的默认值和 schema 分别是 `config.default.yaml` 与 `config.schema.json`。状态库由 Core 放在该 Feature 的私有 state 目录。

Plex 客户端和 MCP 都延迟初始化。Plex 配置缺失或 MCP 启动失败不会阻止 Feature 进程，更不会阻止 Core/Bot 启动。提供只读 `plex.management` capability（`get_job`、`list_jobs`）。

MCP 对外地址由 `mcp.host`、`mcp.port`、`mcp.path` 控制；非本机监听必须配置 `mcp.auth_token`。MCP 只读工具直接执行；扫描、海报、音轨、字幕和 Job 重试等写工具先返回十分钟有效的一次性确认令牌，调用方再次提交该令牌后才执行。自动管线属于受信任的 `media.organized` 流程，不使用 MCP 确认令牌。

构建（先提交当前分支）：

```bash
python /opt/telepiplex/tools/build_feature.py . dist/plex-management-1.2.0.tpx
```
