<div align="center">
    <h1>115Bot - Telegram 机器人</h1>
    <p>简体中文 | <a href="./README_EN.md">[English]</a> </p>
</div>

一个基于 Python 的 Telegram 机器人，用于管理和控制 115 网盘，支持离线下载、视频上传、目录同步等功能。

## Tg讨论群

使用问题 & Bug反馈

[加入](https://t.me/+FTPNla_7SCc3ZWVl)

## 部署&使用

📖[部署&使用](https://github.com/qiqiandfei/Telegram-115bot/wiki)


### 目录结构
```
.
├── app
│   ├── 115bot.py                 # 程序入口脚本
│   ├── config.yaml.example       # 配置文件模板
│   ├── core                      # 核心功能
│   ├── handlers                  # Telegram handlers
│   ├── images                    # 图片
│   ├── init.py                   # 初始化脚本
│   └── utils                     # 有用的工具
├── build.sh                      # 本地构建脚本
├── config                        # 配置目录
├── create_tg_session_file.py     # 创建tg_session脚本
├── docker-compose.yaml           # docker-compose
├── Dockerfile                    
├── Dockerfile.base
├── legacy                        # 历史遗留
├── LICENSE
├── README_EN.md
├── README.md
├── requirements.txt              # 项目依赖
```

## 使用指南

### 基本命令

- `/start`   - 显示帮助信息
- `/auth`    - 115 授权设置
- `/reload`  - 重载配置
- `/s`       - 搜索片源
- `/rl`      - 重试列表
- `/rss`     - rss订阅
- `/sm`      - 订阅电影
- `/sync`    - 同步目录并创建软链
- `/q`       - 取消当前会话

搜索片源可使用 `/s 片名`，也可以直接发送豆瓣、IMDb 或 TVDB 链接。

### 115 开放平台申请

**强烈建议申请 115 开放平台以获得更好的使用体验！**
- 申请地址：[115开放平台](https://open.115.com/)
- 审核通过后将 `115_app_id` 填入配置文件中

如不想使用 115 开放平台，请使用之前的镜像版本 `qiqiandfei/115-bot:v2.3.7`

### 视频下载配置

由于 Telegram Bot API 限制，无法下载超过 20MB 的视频文件。如需下载大视频，请配置 Telegram 客户端：

#### 配置方法
Telegram API申请地址：[Telegram Development Platform](https://my.telegram.org/auth)

申请成功后可以获取到tg_api_id和tg_api_hash

确保配置文件中以下三个参数配置正确：
```
# bot_name
bot_name: "@yourbotname"

# telegram 的api信息
tg_api_id: 1122334
tg_api_hash: 1yh3j4k9dsk0fj3jdufnwrhf62j1k33f
```
**生成 user_session的方法**
1. 修改create_tg_session_file.py中的 API_ID 和 API_HASH
2. 运行脚本：python create_tg_session_file.py
3. 按照提示输入手机号和验证码
4. 将生成的 user_session.session 文件放到 config 目录

> **注意**：如果不配置此步骤，机器人仍可正常运行，只是无法处理超过 20MB 的视频文件。

### 重要提醒

⚠️ **同步功能警告**：`/sync` 命令会**删除目标目录下的所有文件**，包括元数据。大规模同步操作可能触发 115 网盘风控机制，请谨慎使用！

## 许可证
```
MIT License

Copyright (c) 2025 qiqiandfei

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 免责声明
本项目仅供学习和研究使用，请遵守相关法律法规，不得用于商业用途。使用者需自行承担使用风险！

如果这个项目对您有帮助，请献上一个⭐！

## Buy me a coffee~
![请我喝咖啡](https://alist.qiqiandfei.fun:8843/d/Syncthing/yufei/%E4%B8%AA%E4%BA%BA/%E8%B5%9E%E8%B5%8F.png)
