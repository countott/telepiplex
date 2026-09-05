# download Feature

`features/download` 是独立 Feature 源码目录，提供 `download.provider` 与 `storage.provider`。2.1.0 在任何 115 副作用发生前严格校验并深拷贝 search 交付的最小 `media_metadata v2`，拒绝 v1、未知字段、非法范围和新链路中的 `naming_metadata`；完成事件原样转交冻结合同，不追加展示元数据。下载进度、资源选择和终态只覆写 Host API 1.7 的一条 `download` 消息，终态被 Host 精确确认后封存消息段，再交接 Rename；回执不确定时只重试同一消息段，不创建第二条可点击消息。

115 离线轮询、离线写入、存储读取、存储写入和 Token 刷新分别限速，长时间不变的下载轮询从 2 秒起按 1.7 倍退避到最多 30 秒；进度或状态变化会立即把下一次等待恢复为 2 秒。存储读取全局最多四路并发，成功写入与文件信息缓存通过同一代际屏障收敛，残缺或没有稳定 provider ID 的文件事实不会进入缓存。`move_files_by_id` 使用 115 官方 `POST /open/ufile/move` 服务端移动接口，一次接收 1–100 个去重文件 ID 与目标目录 ID；正常 115 整理不再通过 copy 后 delete 放大请求和等待时间。旧 `move_file_detailed` 仅保留给不支持新 capability 的兼容路径。`get_file_info_batch` 的单次上限仍为 32 个路径，文件树继续保留 provider 返回的 SHA1。它使用 SDK 2.1.0，由 telepiplex 构建为不可变 `.tpx`，安装后在 telepiplex 容器内以独立 venv/子进程运行。

配置位于 `/config/plugins/download/config.yaml`。`minimum_video_size_mib` 控制交给 Rename 的最小视频体积，默认 `100` MiB；设为 `0` 可只按视频扩展名过滤。telepiplex `/config` 选择 download 后，可进入“授权配置”“保存目录”或“最小视频体积”：授权支持分步录入 Access/Refresh Token 与 115 扫码，保存目录支持逐条新增、编辑和删除，最小视频体积支持输入 `0–10240` 的整数 MiB；配置均原子写入并立即生效。新增目录分两步：第一步填写只用于按钮展示的名称；第二步填写实际保存路径。单级目录可依次输入显示名称 `真人电影`、保存路径 `真人电影`；多级路径可填写 `series/live action`。路径末尾 `/` 可省略，但不要以 / 开头，因为 Telegram 会将它识别为命令。直接发送 `/auth` 仍会进入授权方式选择。两种授权路线及自动刷新只原子写回该 Feature 私有配置，Token 不进入消息与日志。

下载完成后、发布 `download.completed` 前，Feature 会删除所有非视频文件以及小于 `minimum_video_size_mib` 的视频，再从 115 重新读取文件树确认清理结果。至少保留一个合格视频后才交给 Rename；如果没有合格视频，清理会在删除前失败并发布 `download.failed`，避免清空下载内容或触发错误重命名。完成事件中的 `download_root`/`final_path` 是 115 上未经业务改名的真实文件或目录，`file_tree` 只含清理后重新读取的结构与下载片源证据。Feature 不创建业务目录；命名和冲突处理仍由 rename Feature 完成。

如果 Host 在交接前确认 rename 未安装或未启用，download 会把下载本身收敛为成功终态，明确通知保存目录和“已跳过自动整理”，且不会发布无人消费的 `download.completed`。

纯本地验证构建（不读取 Git 元数据）：

```bash
python tools/build_feature.py features/download /tmp/download-2.1.0.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```

### 完整树快照分页（分阶段启用）

`enable_tree_snapshot_references` 默认 `false`。关闭时，完整树读取仍以 1,000 个后代节点为上限，超过上限明确失败。先升级支持 `snapshot_ref_v1` 的 download、共享 SDK 和 rename，再由用户将此配置设为 `true`；不要向旧 rename 发送新版引用。

开启后，1,000 个及以下节点仍使用 `inline_v1`；更大的下载目录通过共享严格扫描器读取，最多 20,000 个节点、深度 8。清理前检查全部节点的分页容量及完整交接帧容量，清理后再次完整扫描并核对保留对象 ID、路径、大小、类型和 SHA1。根目录身份通过清除本地路径缓存后的读取绑定。没有服务端可靠变更令牌，因此仍保留第二次扫描。

download 在任务数据库旁的 `<jobs-db>.snapshots.sqlite3` 保存不可变完整副本；`download.completed` 发送 `file_tree_transport: snapshot_ref_v1`、`snapshot_complete: true`、空 `file_tree` 和 `file_tree_snapshot`。引用包含版本、唯一快照 ID、job ID、根路径/ID、节点/文件/目录数量、SHA-256 摘要和页数。`storage.provider.get_tree_snapshot_page(reference, cursor)` 只读取此本地副本，不按页扫描 115；每页最多 500 个节点且节点 JSON 不超过 262,144 UTF-8 字节，完整响应保留 RPC 封装余量后小于 1 MiB。

`acknowledge_tree_snapshot(reference)` 仅记录接收凭据，不删除快照。两个 Feature 的快照副本均无 TTL 清理，也不依赖可被覆盖的任务 `result_json`。新增独立数据库，不迁移旧任务表；回退旧版本前先关闭引用发送、处理活动任务并保留双方 sidecar 文件。保留旧任务数据库格式不代表旧消费者能处理新版活动任务。当前没有自动快照垃圾回收，磁盘使用会随完成任务增长。
