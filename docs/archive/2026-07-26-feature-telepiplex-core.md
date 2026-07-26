# `feature/telepiplex-core` 退役归档手册

## 目的

`main` 已成为 Telepiplex Core/Host 唯一有效的源码与发布分支。旧
`feature/telepiplex-core` 与 `main` 已经分叉，不能直接删除后丢失历史；
本手册先把已核对的旧分支尖端保存为不可变的 annotated tag，再删除远端
活动分支。

固定归档身份：

- 旧分支：`feature/telepiplex-core`
- 已核对尖端：`4393bebac52ff75a1b46cf1ef9d634a4b4299f9d`
- 归档标签：`archive/feature-telepiplex-core-2026-07-26`

该标签不匹配 `telepiplex-v*` 或任何 Feature 发布标签，不会触发发布流水线。

## 执行边界

以下命令只能在 Syncthing 显示 `Up to Date / 最新` 后，由用户在 Unraid 的
唯一 Git 工作区执行。不要在 Mac 开发目录执行。

## 归档并删除旧分支

```bash
cd "/mnt/user/archives/life hacker/telepiplex"
set -euo pipefail

LEGACY_BRANCH=feature/telepiplex-core
EXPECTED_LEGACY_SHA=4393bebac52ff75a1b46cf1ef9d634a4b4299f9d
ARCHIVE_TAG=archive/feature-telepiplex-core-2026-07-26

git fetch --force origin \
  "refs/heads/$LEGACY_BRANCH:refs/remotes/origin/$LEGACY_BRANCH"

ACTUAL_LEGACY_SHA="$(git rev-parse "refs/remotes/origin/$LEGACY_BRANCH^{commit}")"
test "$ACTUAL_LEGACY_SHA" = "$EXPECTED_LEGACY_SHA"

test -z "$(git ls-remote --tags origin "refs/tags/$ARCHIVE_TAG")"
git tag -a "$ARCHIVE_TAG" "$EXPECTED_LEGACY_SHA" \
  -m "Archive retired feature/telepiplex-core branch"
git push origin "refs/tags/$ARCHIVE_TAG"

REMOTE_ARCHIVE_SHA="$(
  git ls-remote origin "refs/tags/$ARCHIVE_TAG^{}" | awk '{print $1}'
)"
test "$REMOTE_ARCHIVE_SHA" = "$EXPECTED_LEGACY_SHA"

REMOTE_BRANCH_SHA="$(
  git ls-remote --heads origin "refs/heads/$LEGACY_BRANCH" | awk '{print $1}'
)"
test "$REMOTE_BRANCH_SHA" = "$EXPECTED_LEGACY_SHA"

git push origin --delete "$LEGACY_BRANCH"
test -z "$(git ls-remote --heads origin "refs/heads/$LEGACY_BRANCH")"
```

任一 `test` 失败都必须停止，不要删除分支。最常见的失败含义是远端旧分支
在核对后又发生了变化，或归档标签已存在；此时应先重新确认实际 SHA 和既有
标签内容。

## 完成标准

- `archive/feature-telepiplex-core-2026-07-26^{}` 在远端解析为
  `4393bebac52ff75a1b46cf1ef9d634a4b4299f9d`；
- 远端不再存在 `refs/heads/feature/telepiplex-core`；
- `main` 保持为 Core/Host 唯一活动源码与发布分支。
