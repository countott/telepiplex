# Telepiplex 项目工作指引

本文件适用于 `/Users/young/Documents/telepiplex` 及其所有子目录。除非用户在当前任务中明确改变流程，否则后续开发严格遵守以下边界。

## 1. 工作区职责

- 私人 Mac 上的 `/Users/young/Documents/telepiplex` 是唯一开发工作区。
- 在此目录中只进行源码读取、创建、修改、删除、重命名和本地测试。
- 可以修改包括 `.github/workflows/`、`Dockerfile`、配置模板、脚本和项目文档在内的项目文件。
- Unraid 不承担开发工作，只接收 Syncthing 同步内容，并作为唯一 Git 工作区。
- GitHub 只承担版本管理、GitHub Actions 构建和 Release 发布。

## 2. Mac 本地禁止 Git

Mac 本地项目彻底不使用 Git。不要在此工作区执行任何 `git` 命令，不要创建、修改或依赖 Git 元数据。

明确禁止但不限于：

- `git init`
- `git clone`
- `git status`
- `git diff`
- `git add`
- `git commit`
- `git pull`
- `git push`
- 创建、切换或重命名分支
- 创建 Pull Request
- 创建、修改或推送标签
- 从本地连接 Telepiplex 的 GitHub 仓库

`.git` 和 `.worktrees` 不应存在于 Mac 项目目录。不要为了检查修改而创建它们。

允许只读查看其他 GitHub 项目的代码作为参考，但不得把当前本地项目连接到 GitHub，不得通过 `git clone` 获取参考项目，也不得对外部仓库执行写操作。

## 3. Syncthing 链路

当前权威同步链路为：

```text
Mac /Users/young/Documents/telepiplex
  → Syncthing「仅发送」
  → Unraid /mnt/user/archives/life hacker/telepiplex
  → Syncthing「仅接收」
```

- 当前 Unraid 权威路径是 `/mnt/user/archives/life hacker/telepiplex`。
- 旧的 `/mnt/user/dropzone/telepiplex` 及其他历史路径不再作为默认路径。
- `.git` 只存在于 Unraid，并被 Mac 端 Syncthing 忽略。
- `.stfolder` 是 Syncthing 文件夹标记，应保留。
- 不要在本地创建、删除、同步或依赖 Unraid 的 `.git` 内容。

## 4. 开发完成后的交付

每次完成任务后必须：

1. 明确列出新增、修改、删除或重命名了哪些文件。
2. 简述每个文件改动的目的。
3. 给出必要的本地验证命令及实际验证结果。
4. 提醒用户等待 Syncthing 显示 `Up to Date / 最新`。
5. 不自行发布，不执行任何 Git 操作。

推荐的本地完整测试命令：

```bash
cd /Users/young/Documents/telepiplex

PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

可以根据改动范围运行更小的针对性测试，但交付时必须说明实际运行了什么，不能把未运行的命令描述成已通过。

## 5. Unraid 与 GitHub 发布边界

Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid 手动检查和发布：

```bash
cd "/mnt/user/archives/life hacker/telepiplex"

git status
git diff
git add -A
git commit -m "..."
git push origin main
```

版本标签和 Release 也只能由用户在 Unraid 手动操作，或由用户主动运行 Unraid User Scripts 中的 `Telepiplex Publish` 脚本。Mac 本地不得代替执行。

Unraid 推送至 GitHub 后，由现有 GitHub Actions 自动完成构建和发布。

