# open115 纯下载存储与双授权设计

## 范围

本次只修改 `feature/115`。open115 负责 115 授权、离线下载和存储能力，不再创建业务目录、筛选媒体或修改业务名称。

## 下载完成契约

下载完成后保留 115 返回的原始顶层资源。单文件不再额外包目录，目录和文件均不在 open115 内重命名。`download.completed` 发布：

- `download_root`：115 上实际完成下载的文件或目录路径；
- `final_path`：兼容字段，与 `download_root` 相同；
- `resource_name`：115 实际资源名；
- `file_tree`：下载根下完整文件树，每个节点包含名称、相对路径、绝对路径、目录标识、文件 ID 和大小；
- 原样透传 `media_metadata`、`naming_metadata` 与 Prowlarr `release` 证据。

Telegram `/magnet` 在选择保存目录后立即提交，不再询问顶层文件夹名。通过 `download.provider` 传入的旧 `target_folder_name` 也会被忽略。

## 授权边界

`/auth` 展示两个互相独立的入口：

1. 现有 Token：读取 Feature 私有 `config.yaml` 中的 `access_token` 与 `refresh_token`，选中后切换到 `direct` 模式。
2. 115 扫码：使用 `app_id` 和 PKCE 获取设备码，在 Telegram 文本中显示二维码，确认后换取 Token 并切换到 `scan` 模式。

两条路线最终都通过同一个原子配置存储器写回 `/config/plugins/open115/config.yaml`。Token 刷新也走同一写回回调；临时文件权限为 `0600`，写入后原子替换。响应、事件、日志和异常文本均不包含 Token。

## 失败与恢复

下载任务的 durable outbox 行为保持不变。文件树读取失败视为下载完成契约不完整，发布 `download.failed`，不伪装成已交给 renaming。扫码超时或失败只通知授权失败，不修改当前有效 Token。

