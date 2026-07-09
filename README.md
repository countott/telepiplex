# Telepiplex feature/115

这是一个极简 115 Telegram Bot 分支，只保留 115 登录、磁力离线投递、保存目录选择和顶层文件夹重命名。

## 命令

| 命令 | 用途 |
| --- | --- |
| `/start` | 显示帮助信息 |
| `/auth` | 115 OpenAPI 扫码授权 |
| `/config` | 配置 115 OpenAPI 或 Access / Refresh Token |
| `/reload` | 重新加载配置 |
| `/magnet 磁力链接` | 投递磁力链接到 115 离线 |
| `/m 磁力链接` | `/magnet` 的短命令 |
| `/q` | 取消当前会话 |

## 使用流程

1. 发送 `/magnet magnet:?xt=urn:btih:...`，或使用 `/m`。
2. 选择配置中的 115 保存目录。
3. 输入下载完成后的顶层文件夹名。
4. Bot 投递 115 离线任务。
5. 下载完成后，Bot 只重命名顶层文件夹，不改内部文件名。

输入 `-` 可保留 115 原始文件夹名。若目标名称已存在，Bot 会自动追加 ` (2)`、` (3)` 等后缀。

## 配置

运行时配置路径为 `/config/config.yaml`。可从模板复制：

```bash
cp config/config.yaml.example config/config.yaml
```

最小配置：

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789

115_app_id: null
access_token: ""
refresh_token: ""

open115:
  timeout: 30

clean_policy:
  switch: "on"
  less_than: 400M

category_folder:
  - name: 电影
    path: /电影
  - name: 剧集
    path: /剧集
```

`115_app_id` 用于扫码授权。直连 Token 模式下，`115_app_id` 留空，并填写 `access_token` 与 `refresh_token`。

## Docker

```bash
docker build -t telepiplex:feature-115 .
docker run -d \
  --name telepiplex \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -v /path/to/config:/config \
  -v /path/to/tmp:/tmp \
  telepiplex:feature-115
```

## 本地验证

```bash
python3 -m unittest tests/test_feature_115_surface.py tests/test_config_handler.py tests/test_download_task_startup.py tests/test_directory_config.py tests/test_open_115_startup.py tests/test_auth_handler_startup.py tests/test_bot_runtime_startup.py tests/test_log_sanitizer.py
python3 -m py_compile app/115bot.py app/init.py app/handlers/auth_handler.py app/handlers/config_handler.py app/handlers/download_handler.py app/core/open_115.py app/utils/directory_config.py app/utils/message_queue.py app/utils/log_sanitizer.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

## License

本项目基于 `qiqiandfei/Telegram-115bot` 演进，遵循原项目 MIT License。许可证文本见 `LICENSE`。
