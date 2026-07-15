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
  catalog: https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml
  catalog_refresh_interval: 21600
  install_timeout: 300
  startup_timeout: 30
  drain_timeout: 120
  stabilize_seconds: 10
  restart_limit: 3
```

## 日志

`docker logs -f telepiplex` 现在会持续看到 Core 与各 Feature 子进程转发过来的 runtime 日志；敏感字段（Token、API Key、磁力链接、URL 等）会在写入前脱敏。

持久化日志路径：

- Core：`/config/logs/telepiplex-core.log`
- 每个 Feature：`/config/plugins/<plugin_id>/state/logs/runtime.log`

常用排查命令：

```bash
docker logs -f telepiplex
docker exec telepiplex tail -f /config/logs/telepiplex-core.log
docker exec telepiplex tail -f /config/plugins/open115/state/logs/runtime.log
docker exec telepiplex tail -f /config/plugins/media-search/state/logs/runtime.log
docker exec telepiplex tail -f /config/plugins/renaming/state/logs/runtime.log
docker exec telepiplex tail -f /config/plugins/plex-management/state/logs/runtime.log
```

## Feature 安装与升级

Feature 分支是开发源代码；发布物是由该分支构建的、版本不可变的 `.tpx`。运行容器不 checkout Git 分支，也不把业务源码复制进 Core 镜像。

`plugins.catalog` 可以是远程 HTTPS 地址或本地文件路径。新安装默认读取 `catalog` 分支上的 `https://raw.githubusercontent.com/countott/telepiplex/catalog/catalog.yaml`，这也是官方滚动目录入口。旧版默认 catalog 是 `<plugins.root>/catalog.yaml`（按上面的默认配置即 `/config/plugins/catalog.yaml`）；仅当这个 legacy 文件缺失时，Core 才回退到官方 URL。已存在的 legacy 文件继续使用本地目录；其他显式本地路径即使当前文件缺失，也保持本地配置意图。每个 Feature Release 仍附带发布时的完整 catalog 快照和校验和，供离线或固定版本使用；Core Release 不携带 catalog。目录将 `name@version` 映射到带 SHA-256 固定值的本地路径或 HTTPS 发布地址：

```yaml
plugins:
  media-search:
    versions:
      "1.2.0":
        url: https://example.invalid/releases/media-search-1.2.0.tpx
        sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

首次安装或日常管理时，在 Telegram 发送 `/plugin`。Core 会列出已安装 Feature 和 catalog 中尚未安装的候选：只有依赖满足的 ready 候选才显示安装按钮；依赖尚未满足的候选会显示“先安装”哪个 provider 或具体缺少的 capability。已安装项发现新版时显示更新按钮。安装按钮和更新按钮都绑定该 Feature 的最新稳定兼容版本，点击后才执行对应事务。Core 不会自动安装、批量安装或静默更新任何 Feature。

### 高级/离线操作

普通用户只需发送 `/plugin` 并点击按钮。目录不可用、需要固定版本或使用离线包时，仍可使用 `/plugin install <name@version|artifact.tpx>` 和 `/plugin update <name@version|artifact.tpx>` 精确引用入口。

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

## Feature 可视化配置

Feature 安装后，在 Telegram 发送 `/config`，或在 `/plugin` 页面点击“配置 Feature”。Core 会从当前 Feature 随包提供的 `config.schema.json` 动态生成配置区块，例如 media-search/renaming 的 TVDB、AI，以及 media-search 的 Prowlarr。

选择区块后按提示发送需要修改的 `key=value` 行即可；未发送字段保持不变。API Key、Token、Subscriber PIN 等敏感字段只显示“已配置/未配置”，不会回显真实值，也不会进入日志。配置会先经过完整 schema 校验，再原子写入 `/config/plugins/<plugin_id>/config.yaml`；运行中的 Feature 会完成 drain、shadow 启动和原子切换，失败时恢复旧配置与旧路由。

数组和自由结构暂不通过 Telegram 表单修改。open115 的扫码授权与 Access/Refresh Token 两条路线仍由它自己的 `/auth` 入口独立管理。

## GitHub 独立发布通道

Core、Feature 和 catalog 分开发布。Core 只由 `core-v<semver>` tag 构建镜像，例如发布下一版 Core：

```bash
git tag core-v1.0.7
git push origin core-v1.0.7
```

该流水线先推送 `linux/amd64` Core 镜像 `ghcr.io/<owner>/telepiplex-core:1.0.7` 和 `latest`，随后创建 `core-v1.0.7` 同名 GitHub Release，并强制设为 **Latest**。Core Release 不附带 Feature、catalog 或其他资产，也不会改动 Feature。四种 Feature 各自使用独立 tag，当前发布示例为：

```bash
git tag open115-v1.0.2
git tag media-search-v1.0.2
git tag renaming-v1.0.2
git tag plex-management-v1.0.2
```

每个 Feature tag 只构建或复用该 Feature 的一个不可变 `.tpx`，创建自己的 GitHub Release，并以乐观合并方式更新 `catalog` 分支。Feature Release 固定使用 `--latest=false`，不会取得仓库的 **Latest** 标签；Latest 永远由最近一次成功的 `core-v<semver>` Release 持有。这个分支保存完整的 `catalog.yaml` 和 `catalog.yaml.sha256`；catalog 中每个 HTTPS 资产都固定到实际 SHA-256、Feature branch 和 commit，并携带从已验证 manifest 提取的 `provides` / `requires` capability 元数据。

每个 Feature Release 都附带完整 catalog 快照；Feature 发布完成后会再次从 `catalog` 分支读取已确认快照并覆盖当前 Feature Release 上的 catalog 资产，处理并发发布造成的目录推进。滚动读取始终使用 `catalog` 分支，Core Release 与历史 Platform Release 都不参与 catalog 同步。

Feature 的 `manifest.yaml` 中 Feature version 是不可变的 `name@version` 身份。代码、构建字节、来源分支或来源提交发生变化时必须先提升 version；发布流水线会拒绝同一 version 对应不同 digest 或来源。把现有四个 Feature 的相同 1.0.1 字节从历史聚合 Release 迁移到各自 Release 时，安装版本仍是 1.0.1；Core 按语义版本比较，因此不会产生 Telegram 更新通知。

Core 启动后会立即刷新一次远程目录，此后按 `catalog_refresh_interval: 21600`（6 小时）检查已安装 Feature 当前版本对应的最新稳定兼容版本。刷新使用 HTTPS、大小限制、结构校验和原子缓存；网络或目录异常只跳过本轮，并保留上一次有效目录，不影响 Core 与其他 Feature。

发现更新后，Core 只向 `allowed_user` 发送一次 Telegram 通知，列出当前版本、目标版本和来源提交，并提供“确认更新”和“暂不更新”按钮。只有授权用户点击“确认更新”才复用既有的校验、shadow 启动、drain、原子切换和失败回滚事务；Core 不会静默更新 Feature。离线环境仍可把 Release 中的 `catalog.yaml` 保存为 `/config/plugins/catalog.yaml` 并将配置切回该路径。

## 开发与验证

Core、SDK 和 `.tpx` 构建工具位于同一仓库；Feature 分支只依赖 Core API/SDK 合同，不 import 其他 Feature。

```bash
python3 tools/build_tpx.py --help
python3 -m unittest discover -s tests -t .
git diff --check
```
