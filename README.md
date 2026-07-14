# Plex Management Feature

`feature/plex-management` 是纯 Feature 源码分支。Core 将其构建为不可变 `.tpx`，并在 Core 容器内以独立 venv/子进程运行。

它订阅 `media.organized`：同一事件按媒体库只触发一次扫描，再按每个最终路径逐项定位和验证。普通电影和剧集固定执行扫描、定位、匹配、中文化、无字海报尝试与音轨/字幕选择；S00 等复杂行为是在既有条目上的补丁步骤。任务只有完整执行后才标记 `completed`；进程停止时的活动任务标记 `interrupted`，下次启动按批次续跑；原子 claim 防止同一任务重复执行。

Plex 客户端、AI 和 MCP 都延迟初始化。配置缺失或 AI/MCP 故障不会阻止 Feature 进程，更不会阻止 Core/Bot 启动。配置位于 `/config/plugins/plex-management/config.yaml`，状态库位于该 Feature 私有 state 目录。

提供只读 `plex.management` capability（`get_job`、`list_jobs`）和 `/plex` 入口。AI/MCP 写操作必须经用户一次性确认；`plex_apply_metadata_batch` 可把匹配修复、中文元数据刷新和无字海报设置打包为一次人工确认。MCP 对外暴露地址由 `mcp.host`、`mcp.port`、`mcp.path` 控制；非本机监听时必须配置 `mcp.auth_token`。

构建（先提交当前分支）：

```bash
python /opt/telepiplex/tools/build_feature.py . dist/plex-management-1.0.2.tpx
```
