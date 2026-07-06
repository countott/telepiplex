# Telepiplex

Telepiplex 是基于 Telegram-115Bot 的个人媒体自动化 fork，用 Telegram 控制 115 网盘离线下载，并把“找片源、选候选、投递 115、整理媒体库”放到同一条流程里。

当前重点是电影片源搜索和 115 离线投递：

- 通过 Prowlarr 搜索片源，并按清晰度、来源、编码、音轨和屏蔽词排序候选。
- 选择候选后复用原有 115 离线下载流程，按配置的分类目录保存。
- 支持直接发送豆瓣、IMDb、TVDB、TMDB 等元数据链接，自动解析片名和年份后搜索。
- 普通片名搜索不会阻塞等待元数据；下载完成后优先根据实际文件名推断整理。
- 搜索链路中的豆瓣/IMDb/TVDB/TMDB 元数据会作为下载后整理的辅助信息。
- 115 OpenAPI 初始化失败时，Bot 会尽量继续启动，保留 `/auth`、`/reload` 和搜索能力，避免容器反复重启。

> 本项目仅供个人学习和自用研究。请遵守当地法律法规和站点规则，自行承担使用风险。

## 当前入口

### Telegram 命令

| 命令 | 用途 |
| --- | --- |
| `/start` | 显示帮助信息 |
| `/auth` | 115 扫码授权 |
| `/reload` | 重新加载配置 |
| `/search 片名` | 搜索片源，选择候选后加入 115 离线 |
| `/magnet 磁力链接` | 跳过片名搜索，直接投递已有磁力链接 |
| `/m 磁力链接` | `/magnet` 的短命令 |
| `/retry` | 查看离线失败后的重试列表 |
| `/r` | `/retry` 的短命令 |
| `/strm` | 同步目录并创建 STRM 文件 |
| `/q` | 取消当前会话 |

### 推荐使用方式

- 发送 `/search 布达佩斯大饭店` 搜索片源。
- 直接发送豆瓣、IMDb、TVDB 或 TMDB 页面链接，Bot 会解析标题和年份后搜索。
- 已有磁力链接时，发送 `/magnet magnet:?xt=urn:btih:...` 或 `/m magnet:?xt=urn:btih:...`，跳过片名搜索并直接投递 115 离线。
- 搜索结果出现后，选择候选资源，再选择 115 保存分类和目录。

不支持的普通 HTTP/HTTPS 网页会被拒绝。已有磁力链接请使用 `/magnet` 或 `/m`。

## 快速部署

### 运行要求

- Docker 或 Docker Compose
- Telegram Bot Token
- 可访问 Telegram 的网络环境
- 115 OpenAPI 凭据，或可用的 `access_token` / `refresh_token`
- Prowlarr 服务和 API Key，若要使用搜索片源能力

如需使用本 fork 的搜索和整理能力，请从本仓库构建镜像，或使用你自己基于本仓库发布的镜像。上游 `qiqiandfei/115-bot:latest` 不一定包含 Telepiplex 的新增能力。

```bash
git clone https://github.com/countott/telepiplex.git
cd telepiplex
cp config/config.yaml.example config/config.yaml
docker build -t telepiplex:latest .
```

最小 Docker 运行示例：

```bash
docker run -d \
  --name telepiplex \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -v /path/to/config:/config \
  -v /path/to/tmp:/tmp \
  -v /path/to/media:/media \
  -v /path/to/CloudNAS:/CloudNAS:rslave \
  telepiplex:latest
```

Docker Compose 可参考仓库内的 `docker-compose.yaml`。如果部署在 Unraid，真实运行配置通常是容器内的 `/config/config.yaml`，也就是你挂载到 `/config` 的宿主机目录中的 `config.yaml`。

## 关键配置

先复制模板：

```bash
cp config/config.yaml.example config/config.yaml
```

### Telegram

```yaml
bot_token: your_bot_token
allowed_user: your_user_id
bot_name: "@your_bot_name"
```

`allowed_user` 填 Telegram 用户 ID，建议通过 `@getidsbot` 获取。`bot_name`、`tg_api_id`、`tg_api_hash` 主要用于处理超过 Bot API 限制的大视频转存。

### 115 授权

推荐使用 115 开放平台：

```yaml
115_app_id: your_115_app_id
access_token: ""
refresh_token: ""
```

不使用开放平台时，使用直接 Token 模式：

```yaml
115_app_id: null
access_token: your_access_token
refresh_token: your_refresh_token
```

在直接 Token 模式下，`config.yaml` 中的 Token 是优先来源，并会同步到 `/config/115_tokens.json`。如果你在 Unraid 中更新 Token，请确认改的是容器实际挂载的 `/config/config.yaml`，不是仓库里的示例文件。

### Prowlarr 搜索

搜索片源需要启用 `search` 并填写 Prowlarr API Key：

```yaml
search:
  enable: true
  prowlarr:
    base_url: "http://your-prowlarr:9696"
    api_key: "your_prowlarr_api_key"
    timeout: 20
    indexer_ids: "-2"
    result_limit: 8
```

`search.prowlarr.api_key` 是运行时必须填写的位置。Unraid 部署时同样应写入 `/config/config.yaml`。

### 115 分类目录

搜索候选被选中后，会复用普通离线下载流程，并要求选择分类目录：

```yaml
category_folder:
  - name: movies
    display_name: 电影
    path_map:
      - name: 外语电影
        path: /影视/电影/外语电影
      - name: 华语电影
        path: /影视/电影/华语电影
```

`path` 是 115 网盘内的保存目录。目录命名会影响后续媒体库整理，建议保持稳定。

### 媒体库整理

```yaml
media:
  unorganized_path: /未整理
  plex:
    base_url: ""
    token: ""
    library_id: ""
  emby:
    base_url: ""
    api_key: ""
    strm_mode: disable
    strm_root: /media/115
    openlist_root: /115
    mount_root: /CloudNAS/115
```

有可靠元数据时，下载流程会尝试按媒体库友好的名称整理。缺少元数据或整理失败时，会移动到 `media.unorganized_path`，避免混入已整理目录。

## 搜索流程

1. 用户发送 `/search 片名`，或直接发送支持的元数据链接。
2. Bot 解析搜索词。豆瓣链接优先使用内建解析；IMDb、TVDB、TMDB 会先提取英文标题和年份，再尝试豆瓣反查，所得元数据只作为下载后整理辅助。
3. Bot 调用 Prowlarr 搜索候选，并展示索引器、大小、做种数、发布时间和评分信息。
4. 用户选择候选资源。
5. 用户选择 115 保存目录，或使用上次保存目录。
6. Bot 将下载链接投递到 115 离线下载队列。
7. 下载完成后按配置清理广告文件，并尝试整理到媒体库命名结构。

普通片名无法可靠匹配豆瓣元数据时，Bot 会要求你回复豆瓣链接或中文片名，避免把错误标题写进媒体库目录。

## 运行验证

容器启动后可以看日志确认当前功能是否生效：

```bash
docker logs -f telepiplex
```

应能看到类似运行特性标记：

```text
Telepiplex runtime features: direct_metadata_link_search=enabled, builtin_douban_title_priority=latin_or_original_first, external_metadata_douban_reverse_lookup=enabled, search_command=enabled, magnet_command=enabled, find_command_removed=enabled, legacy_s_command_removed=enabled, retry_command=enabled, strm_command=enabled
Search处理器已注册
```

如果日志里没有这些标记，通常说明容器没有运行到包含 Telepiplex 改动的镜像或分支。

本地开发常用检查：

```bash
python3 -m unittest tests/test_search_handler.py
python3 -m unittest tests/test_bot_surface_cleanup.py
python3 -m py_compile app/115bot.py app/handlers/search_handler.py app/handlers/download_handler.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

## 重要风险

- `/strm` 会删除目标目录下的所有文件后重新生成 STRM，包括元数据文件。大目录慎用。
- 115 离线下载、重命名和移动都依赖 115 接口状态，接口限流或 Token 失效会导致任务失败。
- Prowlarr 结果质量取决于索引器配置。建议先在 Prowlarr 中确认索引器可用，再排查 Bot。
- 本仓库仍保留部分上游历史模块，用户可见命令以 `app/115bot.py` 注册内容和 README 为准。

## 项目结构

```text
.
├── app
│   ├── 115bot.py                 # Telegram Bot 入口
│   ├── adapters                  # 外部服务适配器
│   ├── core                      # 115、调度和核心流程
│   ├── handlers                  # Telegram handlers
│   ├── utils                     # 搜索、元数据、媒体整理等工具
│   └── config.yaml.example       # 应用配置模板
├── config
│   └── config.yaml.example       # 容器运行配置模板
├── tests                         # 单元测试和回归检查
├── docker-compose.yaml
├── Dockerfile
├── requirements.txt
└── README.md
```

## 上游与许可

本项目基于 `qiqiandfei/Telegram-115bot` 演进，遵循原项目 MIT License。原始许可证文本见 `LICENSE`。
