# open115 Feature

`feature/115` 是纯 Feature 源码分支，提供 `download.provider` 与 `storage.provider`。它由 Telepiplex Core 构建为不可变 `.tpx`，安装后在 Core 容器内以独立 venv/子进程运行。

配置位于 `/config/plugins/open115/config.yaml`。下载完成发布 `download.completed`；失败发布 `download.failed`。Feature 不执行媒体清理，文件筛选与“只保留目标视频”由 renaming Feature 统一完成。

构建（先提交当前分支）：

```bash
python /opt/telepiplex/tools/build_feature.py . dist/open115-1.0.0.tpx
```
