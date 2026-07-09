# Telepiplex Core

`feature/telepiplex-core` 是 Telepiplex 的纯核心运行层分支，用来承载共享启动、配置读取、日志、消息队列、用户校验和基础 Telegram Bot runtime。

这个分支不包含 115 投递、媒体搜索、Prowlarr、TVDB、Plex、Aria2、视频转存或媒体整理业务能力。业务功能应从当前 `main` 单独抽取到对应 feature 分支，再由 `main` 做最终缝合。

## 命令

| 命令 | 说明 |
| --- | --- |
| `/start` | 显示核心运行层状态 |
| `/reload` | 重载 `/config/config.yaml` |

## 配置

运行时配置路径仍是容器内 `/config/config.yaml`：

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789

category_folder:
  - name: 真人电影
    path: /真人电影
  - name: 动画电影
    path: /动画电影
  - name: 真人剧集
    path: /真人剧集
  - name: 动画剧集
    path: /动画剧集
```

`category_folder` 是共享保存目录合同，供业务分支复用；core 分支本身不会执行下载或整理。

## 本地验证

```bash
python3 -m unittest tests/test_telepiplex_core_surface.py
python3 -m py_compile app/115bot.py app/init.py app/utils/message_queue.py app/utils/logger.py app/utils/log_sanitizer.py app/utils/directory_config.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

## 分支定位

- `main`：当前已缝合成功的完整业务代码。
- `feature/telepiplex-core`：纯核心运行层。
- `feature/115`：115 单点能力分支。
- `feature/media-search`：媒体搜索能力分支，替代旧 `feature/prowlarr-search`。
