#!/bin/bash
#name=telepiplex Publish
#description=提交 Syncthing 同步内容，并发布 telepiplex Host 与待发布 Feature 版本
#foregroundOnly=true
#arrayStarted=true
#clearLog=true
#noParity=true

set -Eeuo pipefail

REPO="${TELEPIPLEX_PUBLISH_REPO:-/mnt/user/archives/life hacker/telepiplex}"
EXPECTED_ORIGIN='ssh://git@ssh.github.com:443/countott/telepiplex.git'
SSH_KEY="${TELEPIPLEX_PUBLISH_SSH_KEY:-/root/.ssh/telepiplex_github}"
LOCK_FILE="${TELEPIPLEX_PUBLISH_LOCK_FILE:-/var/lock/telepiplex-publish.lock}"

MODULES=(
  download
  search
  rename
  sync
  caption
)

on_error() {
  local status=$?

  echo
  echo "FAILED: line ${BASH_LINENO[0]} exited with status $status" >&2
  exit "$status"
}

trap on_error ERR

die() {
  echo "ERROR: $*" >&2
  exit 1
}

if (($# > 0)); then
  echo '提示：已忽略 User Scripts 保留的旧参数；版本将从源码自动读取。'
fi

COMMIT_MESSAGE='update telepiplex'

# Unraid User Scripts 通常由 root 运行，而 Syncthing 接收目录可能保留其他
# 所有者。将信任范围限制在本次命令和唯一仓库，不修改全局 Git 配置。
# 使用数组而不是函数，确保 diff --quiet、show-ref 等正常非零状态仍可被
# if/|| 判断，而不会被 ERR trap 误判为脚本故障。
GIT=(
  git
  -c "safe.directory=$REPO"
  -C "$REPO"
)

is_semver() {
  [[ "$1" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
}

semver_gt() {
  local a1 a2 a3
  local b1 b2 b3

  IFS=. read -r a1 a2 a3 <<<"$1"
  IFS=. read -r b1 b2 b3 <<<"$2"

  if ((10#$a1 != 10#$b1)); then
    ((10#$a1 > 10#$b1))
    return
  fi

  if ((10#$a2 != 10#$b2)); then
    ((10#$a2 > 10#$b2))
    return
  fi

  ((10#$a3 > 10#$b3))
}

remote_tag_exists() {
  local ref="refs/tags/$1"

  awk -v ref="$ref" '
    $2 == ref {
      found=1
    }

    END {
      exit !found
    }
  ' <<<"$REMOTE_TAGS"
}

latest_remote_version() {
  local prefix="refs/tags/$1-v"

  awk -v prefix="$prefix" '
    index($2, prefix) == 1 {
      version = substr($2, length(prefix) + 1)

      if (version ~ /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/) {
        print version
      }
    }
  ' <<<"$REMOTE_TAGS" |
    sort -V |
    tail -n 1
}

assert_newer_than_remote() {
  local family="$1"
  local version="$2"
  local latest

  latest="$(latest_remote_version "$family")"

  if [[ -n "$latest" ]] && ! semver_gt "$version" "$latest"; then
    die "$family version $version is not newer than remote version $latest"
  fi
}

# 防止重复点击导致两个发布任务同时运行。
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock -n 9 ||
    die '另一个 telepiplex 发布任务正在运行'
fi

[[ -d "$REPO" ]] ||
  die "仓库目录不存在：$REPO"

cd "$REPO"

[[ -d .git ]] ||
  die "该目录不是 Unraid Git 工作区：$REPO"

[[ -d .stfolder ]] ||
  die '未发现 .stfolder；请确认这是 Syncthing 接收目录'

HOST_VERSION_SOURCE='app/115bot.py'

[[ -f "$HOST_VERSION_SOURCE" ]] ||
  die "缺少文件：$HOST_VERSION_SOURCE"

HOST_VERSION_MATCHES="$(
  awk '
    /^def get_version\(/ {
      in_get_version=1
      next
    }

    in_get_version && /^[^[:space:]]/ {
      in_get_version=0
    }

    in_get_version {
      print
    }
  ' "$HOST_VERSION_SOURCE" |
    sed -nE \
      's/^[[:space:]]*version[[:space:]]*=[[:space:]]*"v((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))-host"[[:space:]]*$/\1/p'
)"

HOST_VERSION_COUNT="$(
  awk 'NF { count++ } END { print count + 0 }' <<<"$HOST_VERSION_MATCHES"
)"

((HOST_VERSION_COUNT == 1)) ||
  die '无法从 app/115bot.py 的 get_version() 唯一读取 Host 版本'

HOST_VERSION="$HOST_VERSION_MATCHES"

is_semver "$HOST_VERSION" ||
  die "Host 版本无效：$HOST_VERSION"

[[ -r "$SSH_KEY" ]] ||
  die "GitHub SSH 私钥不可读：$SSH_KEY"

if ! CURRENT_BRANCH="$("${GIT[@]}" branch --show-current)"; then
  die '无法读取当前 Git 分支；请检查仓库目录、所有权和 Git 状态'
fi

[[ "$CURRENT_BRANCH" == 'main' ]] ||
  die "当前分支不是 main；实际：${CURRENT_BRANCH:-detached HEAD}"

if ! ORIGIN_URL="$("${GIT[@]}" remote get-url origin)"; then
  die '无法读取 Git origin'
fi

[[ "$ORIGIN_URL" == "$EXPECTED_ORIGIN" ]] ||
  die "origin 不匹配；实际：$ORIGIN_URL；预期：$EXPECTED_ORIGIN"

[[ -n "$("${GIT[@]}" config user.name || true)" ]] ||
  die '尚未配置 git user.name'

[[ -n "$("${GIT[@]}" config user.email || true)" ]] ||
  die '尚未配置 git user.email'

GIT_DIR="$("${GIT[@]}" rev-parse --git-dir)"

for state in \
  MERGE_HEAD \
  CHERRY_PICK_HEAD \
  REVERT_HEAD \
  rebase-apply \
  rebase-merge
do
  [[ ! -e "$GIT_DIR/$state" ]] ||
    die "Git 正在执行未完成操作：$state"
done

[[ -z "$("${GIT[@]}" diff --name-only --diff-filter=U)" ]] ||
  die '存在未解决的 Git 冲突'

# 确保始终使用 telepiplex 专用部署密钥和 GitHub SSH 443 连接。
"${GIT[@]}" config --local core.sshCommand \
  "ssh -i $SSH_KEY -o IdentitiesOnly=yes -o UpdateHostKeys=no -o BatchMode=yes"

echo '[1/5] 获取 GitHub main 与远端标签...'

"${GIT[@]}" fetch \
  --no-prune \
  --no-tags \
  --refmap= \
  origin \
  '+refs/heads/main:refs/remotes/origin/main'

"${GIT[@]}" merge-base --is-ancestor origin/main HEAD ||
  die 'origin/main 不是本地 HEAD 的祖先；远端可能领先或历史已分叉，请先人工处理'

REMOTE_TAGS="$(
  "${GIT[@]}" ls-remote \
    --tags \
    --refs \
    origin \
    'refs/tags/*-v*'
)"

# 同时覆盖：
# - 未暂存变更
# - 已暂存变更
# - 尚未推送的本地提交
# - 未跟踪文件
CHANGED_FILES="$(
  {
    "${GIT[@]}" diff --name-only
    "${GIT[@]}" diff --cached --name-only
    "${GIT[@]}" diff --name-only origin/main..HEAD
    "${GIT[@]}" ls-files --others --exclude-standard
  } |
    sed '/^$/d' |
    sort -u
)"

PENDING_TAGS=()
MAIN_ONLY_FEATURES=()

HOST_TAG="telepiplex-v$HOST_VERSION"

if remote_tag_exists "$HOST_TAG"; then
  HOST_RELEASE_STATE='远端标签已存在，不重复发布'
else
  assert_newer_than_remote telepiplex "$HOST_VERSION"
  PENDING_TAGS+=("$HOST_TAG")
  HOST_RELEASE_STATE='待发布'
fi

echo '[2/5] 检查 Host 与 Feature 版本...'
echo "Host：${HOST_VERSION}（${HOST_RELEASE_STATE}）"

for module in "${MODULES[@]}"; do
  manifest="features/$module/manifest.yaml"
  project="features/$module/pyproject.toml"

  [[ -f "$manifest" ]] ||
    die "缺少文件：$manifest"

  [[ -f "$project" ]] ||
    die "缺少文件：$project"

  manifest_version="$(
    awk '
      /^version:[[:space:]]*/ {
        print $2
        exit
      }
    ' "$manifest" |
      tr -d "\"'"
  )"

  project_version="$(
    awk -F'"' '
      /^version[[:space:]]*=[[:space:]]*"/ {
        print $2
        exit
      }
    ' "$project"
  )"

  is_semver "$manifest_version" ||
    die "$manifest 中的 version 无效"

  is_semver "$project_version" ||
    die "$project 中的 version 无效"

  [[ "$manifest_version" == "$project_version" ]] ||
    die "$module 的 manifest.yaml 与 pyproject.toml 版本不一致"

  tag="$module-v$manifest_version"
  module_changed=false

  if grep -q "^features/$module/" <<<"$CHANGED_FILES"; then
    module_changed=true
  fi

  if remote_tag_exists "$tag"; then
    if [[ "$module_changed" == true ]]; then
      MAIN_ONLY_FEATURES+=("$module $manifest_version")
    fi

    continue
  fi

  assert_newer_than_remote \
    "$module" \
    "$manifest_version"

  PENDING_TAGS+=("$tag")
done

echo '[3/5] 暂存并提交同步内容...'

"${GIT[@]}" add -A

AHEAD="$(
  "${GIT[@]}" rev-list \
    --count \
    origin/main..HEAD
)"

echo
echo 'Git 变更：'

"${GIT[@]}" status --short

echo
echo '仅进入 main、不创建标签的 Feature 变化：'

if ((${#MAIN_ONLY_FEATURES[@]})); then
  printf '  %s\n' "${MAIN_ONLY_FEATURES[@]}"
else
  echo '  （无）'
fi

echo
echo '准备发布的标签：'

if ((${#PENDING_TAGS[@]})); then
  printf '  %s\n' "${PENDING_TAGS[@]}"
else
  echo '  （无）'
fi

if "${GIT[@]}" diff --cached --quiet &&
  ((AHEAD == 0)) &&
  ((${#PENDING_TAGS[@]} == 0)); then
  echo
  echo '没有需要提交、推送或发布的内容。'
  exit 0
fi

if ! "${GIT[@]}" diff --cached --quiet; then
  "${GIT[@]}" commit -m "$COMMIT_MESSAGE"
fi

echo '[4/5] 推送 main...'

"${GIT[@]}" push origin main

HEAD_SHA="$("${GIT[@]}" rev-parse HEAD)"
TAG_REFS=()

if ((${#PENDING_TAGS[@]})); then
  for tag in "${PENDING_TAGS[@]}"; do
    if "${GIT[@]}" show-ref \
      --verify \
      --quiet \
      "refs/tags/$tag"; then

      [[ "$("${GIT[@]}" rev-list -n 1 "$tag")" == "$HEAD_SHA" ]] ||
        die "本地标签 $tag 已存在，但没有指向当前 HEAD"
    else
      family="${tag%%-v*}"
      version="${tag#*-v}"

      "${GIT[@]}" tag \
        -a "$tag" \
        -m "Release $family $version"
    fi

    TAG_REFS+=("refs/tags/$tag")
  done
fi

if ((${#TAG_REFS[@]})); then
  echo '[5/5] 逐个推送发布标签...'

  for tag_ref in "${TAG_REFS[@]}"; do
    "${GIT[@]}" push \
      origin \
      "$tag_ref"
  done
else
  echo '[5/5] 没有待发布标签。'
fi

"${GIT[@]}" fetch \
  --no-prune \
  --no-tags \
  --refmap= \
  origin \
  '+refs/heads/main:refs/remotes/origin/main'

[[ "$("${GIT[@]}" rev-parse HEAD)" == "$("${GIT[@]}" rev-parse origin/main)" ]] ||
  die '推送完成后，本地 HEAD 与 origin/main 不一致'

if ((${#PENDING_TAGS[@]})); then
  for tag in "${PENDING_TAGS[@]}"; do
    "${GIT[@]}" ls-remote \
      --exit-code \
      --tags \
      --refs \
      origin \
      "refs/tags/$tag" >/dev/null ||
      die "远端未找到已推送标签：$tag"
  done
fi

echo
echo '发布操作完成。'

"${GIT[@]}" status -sb
