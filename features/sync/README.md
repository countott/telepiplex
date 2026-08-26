# Plex Management Feature

`features/sync` 是独立 Feature 源码目录。telepiplex 将其构建为不可变 `.tpx`，并在 telepiplex 容器内以独立 venv/子进程运行。

## 升级到 2.0.0

1.0.0 删除了本地 AI 配置。更新 Feature 前，先编辑 `/config/plugins/sync/config.yaml`，删除整个 `ai:` 配置段并保留其他现有值，然后再执行更新。

telepiplex 对删除或改名的配置字段采用 fail-closed 策略；如果旧 `ai:` 段仍在，更新会返回 `config_migration_required`，当前 release 和配置保持不变。删除该段后，剩余 Plex、TMDB、Fanart.tv、分类目录和 MCP 配置可直接通过 schema 验证。

## 独立手动管理

sync 2.0.0 不订阅 rename 事件，也不会在 rename 完成后自动扫描 Plex。Plex 扫描与增强只能由用户通过 Telegram 命令或带确认令牌的 MCP 写工具独立发起；rename 的成功或失败不依赖 sync 是否安装、启用或可用。

用户明确发起增强 Job 后，Plex 自己负责识别、匹配和基础元数据，插件执行：

```text
scanning -> artwork -> audio -> subtitle -> completed
```

部分文件定位失败时，已定位文件继续增强并记录 warning。任务只有完整执行后才标记 `completed`；进程停止时的活动任务标记 `interrupted`。原子 claim 和持久化步骤结果用于避免重复执行已经完成的工作。

增强 Job 的媒体身份只接受 `media_metadata v2`；作品名、范围与 provider 引用从冻结合同读取，最终文件路径从独立整理结果读取。v2 不携带原始语言或海报，音轨与无字海报阶段按运行时需要请求 TMDB/Fanart.tv。

## Telegram 命令

- `/plex`：查看最近 Job；传入 Job ID 可查看单个任务和待处理选择。
- `/scan`：实时列出 Plex 媒体库，扫描一个库或全部库。它是独立手动操作，不创建增强 Job，也不执行 artwork、audio、subtitle。
- `/sync_config`：交互配置 Plex、TMDB 和 Fanart.tv。MCP 仅通过 YAML 配置。

`/scan` 的媒体库选择本身就是执行意图，点击后不会再要求二次确认。

## 配置与 MCP

运行时配置位于 `/config/plugins/sync/config.yaml`；仓库中的默认值和 schema 分别是 `config.default.yaml` 与 `config.schema.json`。状态库由 telepiplex 放在该 Feature 的私有 state 目录。

Plex 客户端和 MCP 都延迟初始化。Plex 配置缺失或 MCP 启动失败不会阻止 Feature 进程，更不会阻止 telepiplex/Bot 启动。提供只读 `library.sync` capability（`get_job`、`list_jobs`）。

MCP 对外地址由 `mcp.host`、`mcp.port`、`mcp.path` 控制；非本机监听必须配置 `mcp.auth_token`。MCP 只读工具直接执行；扫描、海报、音轨、字幕和 Job 重试等写工具先返回十分钟有效的一次性确认令牌，调用方再次提交该令牌后才执行。

纯本地验证构建（不读取 Git 元数据）：

```bash
python tools/build_feature.py features/sync /tmp/sync-2.0.0.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
