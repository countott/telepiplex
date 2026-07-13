# Telepiplex GitHub 聚合发布设计

日期：2026-07-13

状态：已确认方向，自动执行

目标分支：feature/telepiplex-core

对应业务决策：OPS-TODO-01A

## 1. 目标

由一个 tag 驱动的 GitHub Actions workflow 自动完成以下发布物：

- linux/amd64 Telepiplex Core OCI 镜像。
- open115、media-search、renaming、plex-management 四个 Linux tpx。
- 引用 GitHub Release HTTPS 资产并固定 SHA-256 的 catalog.yaml。
- 包含上述资产的不可变 GitHub Release。

发布过程不得把 Feature 源码复制进 Core 镜像，不得从 main 拼装业务代码，也不得静默覆盖已经存在的 Release。

## 2. OPS-TODO-01 拆分

OPS-TODO-01 分为两个独立子项目：

1. 01A 聚合发布：本设计负责构建、校验、发布镜像、tpx 和 catalog。
2. 01B 更新发现：后续设计负责 Core 读取远程 catalog、比较已安装版本、Telegram 通知和一次确认更新。

01A 完成后，远程 catalog 已具备 01B 所需的版本、Core API、来源 commit、URL 和 digest。

## 3. 发布入口

- 正式发布由 platform-v<semver> tag 触发，例如 platform-v1.0.0。
- 保留 workflow_dispatch，但必须输入同格式、尚不存在的 release tag。
- 普通 branch push 和 pull request 不发布资产。
- workflow 使用 concurrency 按 release tag 串行化，禁止同一 tag 并发发布。
- 如果 GitHub Release 或对应版本镜像已经存在，workflow 失败，不覆盖既有发布物。

## 4. 来源与不可变性

Core 来源是触发 workflow 的 tag commit。四个 Feature 分别从 feature/115、feature/media-search、feature/renaming 和 feature/plex-management checkout。

每个 checkout 使用完整 Git 元数据。现有 tools/build_feature.py 在 Linux runner 上构建 wheelhouse，并把实际 branch 和 40 位 commit 写入 tpx manifest。发布 job 不重写 Feature version；manifest.yaml 中的 semver 是 catalog 的版本键。

同一插件同一版本只能出现一次。若 Feature 代码已变化但 manifest version 未提升，发布前置校验失败，要求先在对应 Feature branch 提升版本，避免用相同 name@version 指向不同内容。

## 5. Workflow 架构

### 5.1 validate-core

- checkout tag 对应 Core。
- 安装 Python 3.12 依赖。
- 运行 Core 完整测试和 tracked Python 编译检查。
- 校验 tag 格式和 Release 不存在。

### 5.2 build-features

使用 matrix 并行构建四个 Feature：

1. checkout Core tag。
2. checkout 指定 Feature branch 到隔离目录。
3. 使用 Core 的 tools/build_feature.py 构建 Linux tpx。
4. 使用 Core artifact verifier 复验 manifest、成员和 digest。
5. 校验 manifest branch 与 matrix branch 一致。
6. 上传单个 tpx artifact 供 release job 汇总。

### 5.3 build-core-image

- 使用 GHCR：ghcr.io/<owner>/telepiplex-core。
- 只构建并推送 linux/amd64。
- 发布不可变 semver tag 和滚动 latest tag。
- OCI labels 记录 repository、release version、Core commit 和构建时间。
- pull request 场景不存在 push 路径。

### 5.4 publish-release

- 下载四个 tpx。
- 使用仓库内 catalog generator 读取并验证每个 tpx。
- 生成稳定排序的 catalog.yaml 和 catalog.yaml.sha256。
- 再次验证四个插件齐全、版本唯一、URL 为本 Release 的 HTTPS URL、digest 与资产字节一致。
- 使用 GitHub CLI 创建 Release 并一次性上传所有资产。
- Release notes 列出 Core image、Core commit、每个 Feature 的 version、branch、commit 和 digest。

## 6. Catalog 格式

Catalog 顶层包含 schema_version 和 release。plugins 下每个 Feature 以 manifest version 为版本键；每个版本记录 GitHub Release HTTPS URL、64 位小写 sha256、core_api，以及 source.branch 和 source.commit。

现有 PluginCatalog 只消费 url 和 sha256，会忽略附加元数据，因此该格式向后兼容。01B 使用附加字段做兼容性过滤和通知展示。

## 7. 安全与权限

- workflow 仅申请 contents: write 和 packages: write。
- GHCR 使用短期 GITHUB_TOKEN，不需要 Docker Hub 密钥。
- 不打印 token、Release auth header 或 Feature 私有配置。
- checkout ref 使用固定受控 branch 名；脚本参数不用 shell eval。
- Release URL 强制 HTTPS，SHA-256 使用小写 64 位十六进制。
- 不允许 PR 触发 publish。
- 删除现有会在 PR 中仍强制 push 的旧 Docker workflow。

## 8. 本地可验证组件

新增 tools/generate_release_catalog.py，接受 repository、tag、output 和一个或多个 tpx 路径。脚本通过 Core 的 verify_tpx 读取 typed manifest，不信任文件名内容。输出按 plugin ID 和 version 排序，并在检测到重复版本、非 semver、非法 repository/tag、非 tpx、损坏资产或缺少四个必需插件时失败。

## 9. 测试与验收

- catalog generator 对相同输入产生字节一致输出。
- URL、digest、Core API 和 source branch/commit 与 tpx manifest 一致。
- 重复插件版本、缺少插件、损坏 tpx 和非法 tag 均失败。
- workflow 静态测试确认：
  - tag/dispatch 才发布；
  - Core 只构建 linux/amd64；
  - registry 是 GHCR；
  - 四个 Feature branch 完整且没有 main 业务拼装；
  - PR 不存在 push 路径；
  - Release 依赖 Core 测试和全部 Feature 构建。
- Core 完整测试、compileall、YAML 解析和 git diff --check 通过。
- 本轮只提交 feature/telepiplex-core，不推送、不创建真实 tag、不触发真实 Release。
