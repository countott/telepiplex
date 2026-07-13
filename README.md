# Telepiplex Core

`feature/telepiplex-core` 是唯一常驻 Docker 运行层。容器内只有 Core 主进程；115、媒体搜索、重命名和 Plex 管理等业务能力均以独立 Feature 子进程运行，不再通过进程内模块或 `main` 分支缝合。

每个 Feature 使用自己的 Python 虚拟环境、配置、状态和版本目录。Core 通过 Unix Domain Socket 调用 Feature 声明的 capability，并负责命令路由、事件投递、健康检查、排空、切换和回滚。正常安装、升级、启用、停用和回滚不重启 Core；只有 Core API 合同本身升级时，才允许升级镜像并重启一次。

## 运行

```bash
docker compose up -d
```

持久化目录只有 `/config`。Feature 运行数据位于 `/config/plugins`，进程 socket 位于容器内临时目录 `/tmp/telepiplex`。

Core 配置示例：

```yaml
log_level: info
bot_token: "your_bot_token"
allowed_user: 123456789
plugins:
  root: /config/plugins
  catalog: /config/plugins/catalog.yaml
  install_timeout: 300
  startup_timeout: 30
  drain_timeout: 120
  stabilize_seconds: 10
  restart_limit: 3
```

## Feature 安装与升级

Feature 分支是开发源代码；发布物是由该分支构建的、版本不可变的 `.tpx`。运行容器不 checkout Git 分支，也不把业务源码复制进 Core 镜像。

`/config/plugins/catalog.yaml` 将 `name@version` 映射到带 SHA-256 固定值的本地路径或 HTTPS 发布地址：

```yaml
plugins:
  media-search:
    versions:
      "1.2.0":
        url: https://example.invalid/releases/media-search-1.2.0.tpx
        sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

管理命令：

```text
/plugin install media-search@1.2.0
/plugin update media-search@1.3.0
/plugin enable media-search
/plugin disable media-search
/plugin rollback media-search
/plugin remove media-search
/plugin status media-search
/plugin doctor
```

也可把已存在的绝对 `.tpx` 路径传给 `install` 或 `update`。更新过程先校验和安装新版本，再启动 shadow 子进程、检查健康、排空旧任务并原子切换路由；任何一步失败都保留旧版本。

## GitHub 聚合发布

正式发布使用 `platform-v<semver>` tag，例如：

```bash
git tag platform-v1.0.0
git push origin platform-v1.0.0
```

GitHub Actions 会构建并推送 `linux/amd64` Core 镜像 `ghcr.io/<owner>/telepiplex-core:1.0.0`，同时从四个独立分支生成 `open115`、`media-search`、`renaming` 和 `plex-management` 的 Linux `.tpx`。同一个 GitHub Release 还包含 `catalog.yaml` 与 `catalog.yaml.sha256`；catalog 中每个 HTTPS 资产都固定到实际 SHA-256、Feature branch 和 commit。

Feature 的 `manifest.yaml` version 是不可变的 `name@version` 身份。代码发生变化时必须先提升 version；发布流水线会拒绝同一 version 对应不同 digest。当前可把 Release 中的 `catalog.yaml` 放到 `/config/plugins/catalog.yaml` 使用。远程版本发现与 Telegram 一键确认属于 OPS-TODO-01B；在该功能完成前，Core 不会静默更新 Feature。

## 开发与验证

Core、SDK 和 `.tpx` 构建工具位于同一仓库；Feature 分支只依赖 Core API/SDK 合同，不 import 其他 Feature。

```bash
python3 tools/build_tpx.py --help
python3 -m unittest discover -s tests -t .
git diff --check
```
