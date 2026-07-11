# Telepiplex

Telepiplex 是一个面向 Telegram 的媒体投递与整理机器人。`main` 是可部署运行分支，默认组合稳定模块：

- `app.modules.open115`：115 授权、保存目录和离线投递。
- `app.modules.media_search`：Prowlarr 媒体搜索、候选确认和下载请求提交。
- `app.modules.renaming`：下载完成后的反查、整理和重命名。
- `app.modules.plex_management`：重命名成功后的 Plex 扫库、匹配与媒体默认项管理。

`feature/telepiplex-core`、`feature/115`、`feature/media-search`、`feature/renaming` 仍作为模块开发边界使用；部署镜像建议跟随 `main`。

## Telegram 命令

| 命令 | 说明 |
| --- | --- |
| `/start` | 显示运行状态 |
| `/modules` | 查看当前模块状态 |
| `/reload` | 重载 `/config/config.yaml`，不会热加载 Telegram handler |
| `/config` | 配置 115 Token |
| `/auth` | 115 扫码授权 |
| `/magnet`、`/m` | 投递磁力链接 |
| `/search`、`/s` | 搜索媒体并提交下载请求 |
| `/plex <请求>` | 通过可选 AI 工具调用查询或准备 Plex 管理操作 |

模块代码更新或模块配置变更后，需要重启容器才会生效。

## 配置

运行时配置路径是容器内 `/config/config.yaml`。配置模板是所有稳定模块的配置合集，不再按 module 拆分；以 `config/config.yaml.example` 和 `app/config.yaml.example` 为准，两份模板应保持一致。

如果没有显式配置 `modules`，运行版默认等价于：

```yaml
modules:
  enabled: all
  disabled: []
```

需要临时禁用某个稳定模块时，可以写入 `disabled`：

```yaml
modules:
  enabled: all
  disabled:
    - app.modules.renaming
```

115、Prowlarr、TVDB、AI、媒体整理等配置都放在同一个 `/config/config.yaml`。其中 Prowlarr 配置位于 `search.prowlarr`，AI 地址使用 `ai.api_url`，不是旧的 `ai.base_url`。

## Plex 管理模块

`app.modules.plex_management` 只在 `renaming.*` 成功结束后异步运行，不处理
`open115.unorganized_fallback`。Plex 失败不会回滚已经完成的 115 下载或重命名。

自动管线按以下顺序执行：

1. 通过 `category_folder[].plex_library_id` 选择媒体库并触发扫描。
2. 校验 Plex GUID；当前外部 ID 正确时直接通过，唯一精确候选自动修复，多候选或无精确候选转 Telegram 人工确认。
3. 仅刷新当前条目的 `zh-CN` 元数据，不写入或锁定 TMDB 文本字段。
4. 无字海报依次使用 TMDB `iso_639_1=null`、Fanart.tv `lang=00`；没有结构化候选时保留当前海报并提供 Plex 候选。
5. 使用 TMDB `original_language` 选择原声最高质量音轨；同分歧义时保持不变。
6. 优先选择 Plex 已识别的外部 `chi` 字幕，其次内封 `chi` 字幕。

字幕下载属于另一个管线模块；这里不会联网补字幕。中文化、海报、音轨或字幕步骤可以记录警告并继续，扫库路由、媒体定位或匹配未确认则会停止当前 Plex 任务。

运行时 `/config/config.yaml` 的相关配置如下。TMDB 的 `api_key` 填 API Read Access Token；Fanart.tv 是可选补充来源。

```yaml
category_folder:
  - name: 真人电影
    path: /真人电影
    plex_library_id: "1"
  - name: 真人剧集
    path: /真人剧集
    plex_library_id: "2"

media:
  plex:
    base_url: "http://plex:32400"
    token: "YOUR_PLEX_TOKEN"
    timeout: 30
    management:
      enabled: true
      database_path: "/config/plex_management.db"
      scan_poll_interval: 5
      scan_timeout: 300
    mcp:
      enabled: false
      host: "127.0.0.1"
      port: 8765
      path: "/mcp"
      auth_token: ""
    ai:
      enabled: false
      max_tool_rounds: 3

metadata:
  tmdb:
    api_key: "YOUR_TMDB_READ_ACCESS_TOKEN"
    timeout: 15

artwork:
  fanart:
    api_key: ""
    timeout: 15
```

### 标准 MCP 服务端

MCP 使用 Streamable HTTP。容器内默认入口是 `http://127.0.0.1:8765/mcp`。
需要从宿主机或局域网访问时，将监听地址改为 `0.0.0.0`、设置非空
`auth_token`，并给 Compose 服务增加端口映射：

```yaml
ports:
  - "8765:8765"
```

客户端连接 `http://HOST:8765/mcp`，并在每个请求中发送：

```http
Authorization: Bearer YOUR_MCP_TOKEN
```

非回环监听没有 Token 时服务会拒绝启动。MCP 提供 7 个只读工具和 8 个写工具；写工具先返回单次确认令牌，再由客户端明确提交，令牌过期或重复使用都会被拒绝。

### `/plex` AI 工具调用

`/plex` 复用相同的 MCP 工具 schema。启用 `media.plex.ai.enabled: true` 后，
还需在顶层 `ai.api_url`、`ai.api_key`、`ai.model` 配置支持 OpenAI 兼容
`tool_calls` 的服务。单次请求最多执行 3 轮工具调用。AI 只能准备写操作，不能消费确认令牌；实际写入必须点击 Telegram 的“确认执行”按钮。

## 分支定位

- `main`：可部署组合运行分支，镜像建议拉取这里。
- `feature/telepiplex-core`：纯核心运行层。
- `feature/115`：115 单点能力分支。
- `feature/media-search`：媒体搜索能力分支。
- `feature/renaming`：下载完成后的重命名与整理能力分支。
- `feature/plex-management`：Plex 管理、MCP 与可选 AI 工具调用能力分支。

## 本地验证

```bash
python3 -m unittest tests/test_bot_runtime_startup.py tests/test_composable_integration.py tests/test_composable_core.py
python3 -m py_compile $(git ls-files '*.py')
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```
