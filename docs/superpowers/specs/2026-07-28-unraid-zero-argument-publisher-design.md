# telepiplex Unraid 零参数一键发布设计

## 目标

将 Unraid User Scripts 中的 `telepiplex Publish` 改为零参数的一键发布器。
操作者只需点击 `Run`；脚本自动提交 Syncthing 同步内容，推送 `main`，
并逐个发布所有尚未出现在 GitHub 远端的 telepiplex Host 与 Feature tag。

## 当前问题

现有脚本默认参数是：

```text
PUBLISH - update telepiplex
```

其中 `-` 表示跳过 Host。这个默认值会让一次看似完整的发布只发布 Feature，
而不创建 telepiplex Host tag，也不会触发本体镜像、`latest` 和 GitHub
Latest Release 更新。

## 操作界面

新脚本不声明 `argumentDescription` 或 `argumentDefault`，也不要求运行参数。
Unraid User Scripts 页面不再要求操作者输入确认词、Host 版本或提交说明。

点击 `Run` 是唯一发布入口。若 Unraid User Scripts 仍传入界面持久化的
旧参数，脚本仅提示已经忽略，不读取、不回显，也不让旧参数影响版本或提交
说明；随后继续按源码版本完成一键发布。

## 版本来源

### telepiplex Host

脚本从 `app/115bot.py` 的 `get_version()` 中读取唯一的
`v<major>.<minor>.<patch>-host` 字面量，去掉前导 `v` 和结尾 `-host` 后生成：

```text
telepiplex-v<major>.<minor>.<patch>
```

读取结果为空、出现多个匹配或不是严格三段 SemVer 时，脚本在任何提交或
推送前失败。脚本不猜测、不递增，也不从历史 tag 反推源码版本。

### Feature

继续从以下两个权威文件读取各 Feature 版本：

- `features/<module>/manifest.yaml`
- `features/<module>/pyproject.toml`

两个版本必须一致且为严格三段 SemVer，否则在提交或推送前失败。

## 发布决策

脚本先一次性读取 GitHub 远端 tag：

- 若当前 Host 或 Feature tag 不存在，且版本高于该 tag 家族的最新远端
  SemVer，则加入待发布列表。
- 若当前 tag 已存在，保留远端不可变 tag，不重复创建或推送；同步内容仍可
  进入 `main`。
- 若当前 tag 不存在但版本不高于该家族最新远端版本，立即失败，要求先提升
  源码版本。

Host 始终排在待发布列表第一位，随后按
`download`、`search`、`rename`、`sync`、`caption` 的固定顺序处理 Feature。

## 提交与推送

脚本固定使用提交说明：

```text
update telepiplex
```

它继续覆盖未暂存、已暂存、未跟踪和尚未推送的本地内容：

1. 暂存全部同步内容。
2. 仅在存在暂存变更时创建提交。
3. 先推送 `main`。
4. 在已经进入远端 `main` 的当前 HEAD 上创建缺失 tag。
5. 每个 tag 使用一次独立的 `git push`，保证 GitHub 为每个 tag 生成独立
   push 事件。
6. 最后验证远端 `main` 和所有本次 tag。

没有文件变化、未推送提交或待发布 tag 时，脚本正常退出并说明无待办内容。

## 安全边界与错误处理

保留现有安全检查：

- 只允许 Unraid 权威目录
  `/mnt/user/archives/life hacker/telepiplex`。
- 要求 `.git`、`.stfolder`、专用 SSH 私钥、预期 origin 和 `main` 分支。
- 拒绝未完成 Git 操作、冲突、分叉历史和并行发布任务。
- `main` 推送失败时不创建 tag。
- 单个 tag 推送失败后立即停止，不继续推送后续 tag。
- 不删除、移动或覆盖已有远端 tag。

Mac `/Users/young/Documents/telepiplex` 仍只负责源码修改和本地测试，不执行
Git 或发布。

## 测试

扩展 `tests/test_unraid_publish_script.py` 覆盖：

1. 零参数运行自动从 Host 源码读取版本，并将 Host 与多个缺失 Feature tag
   逐个推送。
2. 当前 Host tag 已存在时跳过 Host，不阻止 `main` 或新 Feature 发布。
3. Host 版本缺失、重复或格式错误时，在提交和推送前失败。
4. 传入任何旧式参数时提示并忽略，随后继续按源码版本运行。
5. User Scripts 元数据不再声明参数输入。
6. 固定提交说明为 `update telepiplex`。
7. 保留已有的单 tag 推送、远端版本单调递增和最终验证测试。

验证包括针对性 pytest、Bash 语法检查，以及项目工作指引要求的完整 Host
和五个 Feature 测试矩阵。

## 交付

本地修改完成并验证后，操作者等待 Syncthing 显示 `Up to Date / 最新`，
再用受控文件 `scripts/unraid/telepiplex-publish.sh` 完整替换 Unraid User
Scripts 中的实际脚本。此后发布只需点击一次 `Run`，不再填写参数。
