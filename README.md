# open115 Feature

`feature/115` 是纯 Feature 源码分支，提供 `download.provider` 与 `storage.provider`。它由 Telepiplex Core 构建为不可变 `.tpx`，安装后在 Core 容器内以独立 venv/子进程运行。

配置位于 `/config/plugins/open115/config.yaml`。Core `/config` 选择 open115 或直接发送 `/auth`，都可分步录入 Access/Refresh Token，或选择 115 扫码授权；两条路线及自动刷新都只原子写回该 Feature 私有配置，Token 不进入消息与日志。

下载完成发布 `download.completed`；失败发布 `download.failed`。完成事件中的 `download_root`/`final_path` 是 115 上未经业务改名的真实文件或目录，并附完整 `file_tree` 与下载片源证据。Feature 不创建业务目录、不执行媒体清理；命名、筛选和冲突处理全部由 renaming Feature 完成。

构建（先提交当前分支）：

```bash
python /opt/telepiplex/tools/build_feature.py . dist/open115-1.0.1.tpx
```
