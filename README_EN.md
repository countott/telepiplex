<div align="center">
    <h1>115Bot - Telegram Bot</h1>
    <p>English | <a href="./README.md">[简体中文]</a></p>
</div>

A Python-based Telegram bot for managing and controlling 115 Network Disk, supporting offline downloads, video uploads, directory synchronization, and more.

## Tg group

Usage Issues & Bug Reports

[Join](https://t.me/+FTPNla_7SCc3ZWVl)

## Update Log
- Added `/search` for verified media-entry confirmation, release search, metadata link parsing, 115 offline download, and automatic Plex naming.
- Added `/magnet` and `/m` for direct magnet submission.
- Unsupported HTTP/HTTPS web pages are rejected; existing magnet links should be submitted with `/magnet` or `/m`.
- Removed obsolete command surfaces and manual naming flows.
- Added media configuration for unorganized files and Plex/Emby library update extension points.

## Background
This project originated from the need to optimize personal daily viewing experience. As a movie enthusiast, I use the combination of 115 Network Disk + CloudDrive2 + Emby to manage and watch media content.

Imagine this scenario:

While commuting, you come across an interesting movie. Simply send the magnet link to the TG bot, and it will:
- Automatically download the movie to the specified 115 save directory
- Intelligently clean up advertisement files
- Automatically create STRM files and notify Emby for media library scanning

When you return home after work, just prepare some snacks and drinks, open Emby, and enjoy a well-organized viewing experience. Let a good movie wash away your daily fatigue and help you relax.

## Known Issues
- Limited support for TV series. Downloading series directly may cause unexpected issues
- Directory synchronization will clear the entire folder, including metadata (quite aggressive)

If you'd like to help improve this project, welcome to [join](https://t.me/qiqiandfei)!

## Features

- � **115 Account Management**
  - Based on 115 Open Platform
  - Uses official API for stable and reliable service

- ⬇️ **Offline Download**
  - Support multiple download protocols: Magnet links, Thunder, ed2k
  - Intelligent automatic save-path selection
  - Advertisement file cleanup
  - Automatic organization for media-library naming

- 🔄 **Directory Synchronization**
  - Automatic local symlink creation
  - STRM file batch generation
  - Seamless Emby media library integration

- � **Video Processing**
  - Support automatic video file upload to 115 Network Disk (Note: Consumes VPS/proxy traffic, use with caution)

## Quick Start

### Requirements

- Docker environment
- Python 3.12+
- Accessible Telegram network environment

### Installation

1. **Clone Project**
   ```bash
   git clone https://github.com/qiqiandfei/Telegram-115bot.git
   cd 115bot
   ```

2. **Configure Settings**
   - Copy configuration template
     ```bash
     cp config/config.yaml.example config/config.yaml
     ```
   - Edit `config.yaml`, fill in necessary configurations:
     - Telegram Bot Token
     - Telegram authorized user
     - 115 Network Disk configuration
     - Directory mapping settings

3. **Docker Deployment**

   **Local**
   ```bash
   # Build base image
   docker build -t 115bot:base -f Dockerfile.base .
   
   # Build application image
   docker build -t 115bot:latest .
   
   # Run container
   docker run -d \
     --name tg-bot-115 \
     --restart unless-stopped \
     -e TZ=Asia/Shanghai \
     -v /path/to/config:/config \
     -v /path/to/tmp:/tmp \
     -v /path/to/media:/media \
     -v /path/to/CloudNAS:/CloudNAS:rslave \
     115bot:latest
   ```
   
   **Docker Compose (Recommended)**
   ```yaml
   version: '3.8'
   services:
    115-bot:
      container_name: tg-bot-115
      environment:
        TZ: Asia/Shanghai
      image: qiqiandfei/115-bot:latest
      # privileged: True
      restart: unless-stopped
      volumes:
        - /path/to/config:/config  # Configuration path
        - /path/to/tmp:/tmp        # Temp path
        - /path/to/media:/media    # Emby media library directory (symlink directory)
        - /path/to/CloudNAS:/CloudNAS:rslave # CloudDrive2 mount directory
   ```

## Configuration

Please refer to the comments in `config/config.yaml.example` for configuration details.

### Directory Structure
```
.
├── app
│   ├── 115bot.py                 # Entry point script
│   ├── config.yaml.example       # Template of configuration
│   ├── core                      # Core functions
│   ├── handlers                  # Telegram handlers
│   ├── images                    # Images
│   ├── init.py                   # Init script
│   └── utils                     # Utils
├── build.sh                      # local build shell
├── config                        # dir of configuration
├── create_tg_session_file.py     # create tg_session file
├── docker-compose.yaml           # docker-compose
├── Dockerfile                    
├── Dockerfile.base
├── legacy                        
├── LICENSE
├── README_EN.md
├── README.md
├── requirements.txt              
```

## Usage Guide

### Basic Commands

- `/start`   - Show help information
- `/auth`    - 115 authorization setup
- `/reload`  - reload the configuration
- `/search`  - Search releases and add them to 115 offline download
- `/magnet`  - Submit an existing magnet link directly
- `/m`       - Short magnet command
- `/retry`   - Retry list
- `/r`       - Short retry list command
- `/strm`    - Sync directory and create STRM files
- `/q`       - Cancel current session

Use `/search movie name` to resolve and confirm a media entry before searching releases, or send a Douban, IMDb, TVDB, or TMDB link directly. Series requests can include scope such as `S02E05`; unreleased episodes are blocked before Prowlarr is queried. Use `/magnet magnet:?xt=urn:btih:...` or `/m magnet:?xt=urn:btih:...` when you already have a magnet link.

`/search` does not send raw text, cleaned page titles, or unverified AI guesses directly to Prowlarr. It first resolves a verified movie or series entry, asks for confirmation when needed, and then generates the Prowlarr query from the confirmed title, year, season, or episode scope.

Configure `category_folder` as a flat list of 115 save paths. The bot shows these paths directly, without an extra category step:

```yaml
category_folder:
  - name: 真人电影
    path: /真人电影
    plex_library_id: "1"
  - name: 动画电影
    path: /动画电影
    plex_library_id: "12"
  - name: 真人剧集
    path: /真人剧集
    plex_library_id: "2"
  - name: 动画剧集
    path: /动画剧集
    plex_library_id: "11"
```

When Plex is configured, `plex_library_id` maps each 115 save path to the corresponding Plex library. The bot sends a confirmation button after media organization, and Plex refresh is triggered only after the user confirms.

### 115 Open Platform Application

**Strongly recommend applying for 115 Open Platform for better user experience**
- Application URL: [115 Open Platform](https://open.115.com/)
- After approval, fill in the `115_app_id` in the configuration file

If you don't want to use the 115 Open Platform, please use the previous image version `qiqiandfei/115-bot:v2.3.7`

### Video Download Configuration

Due to Telegram Bot API limitations, videos larger than 20MB cannot be downloaded. To download large videos, please configure the Telegram client:

#### Configuration
Telegram API application address: [Telegram Development Platform](https://my.telegram.org/auth)

When your application is successful, you will receive a “tg_api_id” and “tg_api_hash”.

Ensure that these three parameters are correct:
```
# bot_name
bot_name: "@yourbotname"

# telegram api info
tg_api_id: 1122334
tg_api_hash: 1yh3j4k9dsk0fj3jdufnwrhf62j1k33f
```

> **Note**: If you don't configure this step, the bot will still work normally, but cannot handle video files larger than 20MB.

### Important Warning

⚠️ **STRM Sync Function Warning**: The `/strm` command will **delete all files in the target directory**, including metadata. Large-scale synchronization operations may trigger 115 Network Disk's risk control mechanism, please use with caution!

### License
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
## Disclaimer
This project is intended solely for educational and research purposes. Please comply with all applicable laws and regulations, and refrain from using it for commercial purposes. Users assume all risks associated with its use!

If this project has been helpful to you, please give it a ⭐!

## Buy me a coffee~
![Buy me a coffee](https://alist.qiqiandfei.fun:8843/d/Syncthing/yufei/%E4%B8%AA%E4%BA%BA/%E8%B5%9E%E8%B5%8F.png)
