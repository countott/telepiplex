# open115 Feature

`feature/open115` 是纯 Feature 源码分支，提供 `download.provider` 与 `storage.provider`。它由 Telepiplex Core 构建为不可变 `.tpx`，安装后在 Core 容器内以独立 venv/子进程运行。

配置位于 `/config/plugins/open115/config.yaml`。Core `/config` 选择 open115 后，可进入“授权配置”或“保存目录”：授权支持分步录入 Access/Refresh Token 与 115 扫码，保存目录支持逐条新增、编辑和删除，并在“保存并完成”后统一原子写入、立即生效。新增目录分两步：第一步填写只用于按钮展示的名称；第二步填写实际保存路径。单级目录可依次输入显示名称 `真人电影`、保存路径 `真人电影`；多级路径可填写 `series/live action`。路径末尾 `/` 可省略，但不要以 / 开头，因为 Telegram 会将它识别为命令。直接发送 `/auth` 仍会进入授权方式选择。两种授权路线及自动刷新只原子写回该 Feature 私有配置，Token 不进入消息与日志。

下载完成发布 `download.completed`；失败发布 `download.failed`。完成事件中的 `download_root`/`final_path` 是 115 上未经业务改名的真实文件或目录，并附完整 `file_tree` 与下载片源证据。Feature 不创建业务目录、不执行媒体清理；命名、筛选和冲突处理全部由 renaming Feature 完成。

构建（先提交当前分支）：

```bash
python /opt/telepiplex/tools/build_feature.py . dist/open115-1.2.3.tpx
```
