# Telepiplex

Telepiplex 是一个面向 Telegram 的媒体投递与整理机器人。`main` 是可部署运行分支，默认组合稳定模块：

- `app.modules.open115`：115 授权、保存目录和离线投递。
- `app.modules.media_search`：Prowlarr 媒体搜索、候选确认和下载请求提交。
- `app.modules.renaming`：下载完成后的反查、整理和重命名。

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

## 分支定位

- `main`：可部署组合运行分支，镜像建议拉取这里。
- `feature/telepiplex-core`：纯核心运行层。
- `feature/115`：115 单点能力分支。
- `feature/media-search`：媒体搜索能力分支。
- `feature/renaming`：下载完成后的重命名与整理能力分支。

## 本地验证

```bash
python3 -m unittest tests/test_bot_runtime_startup.py tests/test_composable_integration.py tests/test_composable_core.py
python3 -m py_compile $(git ls-files '*.py')
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```
