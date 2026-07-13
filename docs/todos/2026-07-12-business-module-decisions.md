# Telepiplex 当前业务决策与 TODO

更新时间：2026-07-13

状态：`active`

文档职责：记录当前已经确认的业务规则、实际运行链路和仍需确认的决策。本文档不自动授权修改代码；每个待决策项确认后再进入设计、TDD 和实现。

## 一、已经落地的系统现状

### 1. 运行架构

- `feature/telepiplex-core` 是唯一常驻 Docker 运行层。
- `open115`、`media-search`、`renaming`、`plex-management` 是四个独立 Feature。
- 每个 Feature 使用独立 venv、子进程、配置、状态目录和 Unix Socket。
- Feature 只能通过 Core SDK、capability 和 event 协作，不得导入或声明依赖另一个 Feature 包。
- 普通安装、升级、启用、停用、回滚和卸载不重启 Core。
- 只有新增全新的 Core API 合同时，才允许升级 Core 镜像并重启一次。

### 2. 安装与故障隔离

- Feature 以不可变 `.tpx` 安装，不从运行容器 checkout Feature branch。
- `.tpx` 会校验来源 branch/commit、SHA-256、wheel metadata 和 sibling Feature 依赖。
- Provider 更新不得令现有消费者新增缺失 capability；不兼容更新会在切换前被拒绝。
- Feature 启动、AI、MCP 或业务初始化失败，不得阻止 Core 和其他 Feature 启动。
- Core 重启后，已启用 Feature 按 capability 依赖顺序恢复。
- Feature 安装后自动启用；只有被显式 disable 后才需要 `/plugin enable`。

### 3. 当前分支与发布状态

- Core 与四个 Feature 源码分支已经独立推送到远端。
- `main` 暂不作为日常开发或部署入口。
- GitHub 聚合发布流水线已经落地到 Core Feature 分支，可由 `platform-v<semver>` tag 自动生成 Core 镜像、四个 Linux `.tpx` 和远程 catalog。
- Core 已能安全刷新远程 catalog、比较兼容稳定版本，并在 Telegram 一次确认后更新 Feature；不会静默更新。
- `/plugin` 已提供依赖感知的可安装 Feature 列表和显式安装按钮，普通用户无需进入 ttyd 或自行构建 `.tpx`。
- 当前尚未创建实际 release tag；本地或 Unraid 手工构建仍作为发布前验证与故障兜底。

## 二、已经确认的业务规则

### 1. 下载计划与媒体身份

- 用户确认后的 canonical contract 是后续流程的唯一业务目标。
- 下载后的文件 mapping 只能把实际文件对应到已确认目标，不得新增、删除或改写季、集、电影身份和媒体分类。
- Prowlarr 搜索使用英文或原始标题；中文标题回填不是 Prowlarr 查询的前置条件。
- 后续获得中文标题时，应更新 canonical metadata，供命名和展示使用，不应改变已确认的下载目标。

### 2. 文件保留与清理

- 最终只保留下载计划需要的视频文件。
- 字幕、NFO、海报和其他非视频文件不保留。
- 明确属于小视频、样片、广告或非目标内容的文件清理。
- 任何文件不能因为异常被当作“正常成功”静默处理；部分失败必须给出结果清单和通知。

### 3. 任务状态与人工兜底

- `completed` 只代表任务实际完成。
- 进程中断或外部操作结果不确定时，任务标记为 `interrupted`，不得伪装成 completed。
- open115 外部传输中断后不自动重复提交，等待人工确认或显式重试。
- renaming 发生破坏性操作中断后不自动重放，保留现场并交由人工恢复。
- Plex job 允许人工兜底；中断任务必须可见，不能仅靠数据库防重复而重复执行外部操作。

### 4. 普通场景与复杂场景

- 普通电影和普通剧集的正常整理是主流程。
- 关联电影、OVA、Special、临时 S00 等复杂情况应以 patch 行为叠加。
- 复杂 patch 不得改变或干扰普通电影、普通剧集和正式 Season 的默认行为。
- Plex AI/MCP 是可选能力；关闭或配置失败不能阻止普通 Plex 扫描链路和 Bot 启动。

### 5. 暂缓范围

- 原检查报告中的 P2、P3 继续搁置。
- 未经再次明确确认，不进入设计或实现。

## 三、当前端到端业务链路

```text
Telegram 搜索请求
→ media-search 收集证据并生成候选下载计划
→ 用户确认 canonical contract
→ media-search 调用 download.provider
→ open115 提交并跟踪下载
→ open115 发布 download.completed
→ renaming 获取真实文件树并执行规则优先 mapping
→ AI 只在规则无法确定时提供受 contract 约束的映射候选
→ renaming 移动目标视频并清理不保留内容
→ renaming 发布 media.organized
→ plex-management 按最终路径扫描、定位和处理 Plex 条目
→ 成功 completed；中断 interrupted；失败通知并人工兜底
```

模块责任边界：

| 模块 | 当前责任 | 不应承担 |
|---|---|---|
| Core | 生命周期、路由、事件、隔离、回滚、Telegram 通用入口 | 媒体搜索、文件命名、115 或 Plex 业务规则 |
| open115 | 下载、存储、真实文件树和传输结果 | 媒体身份判断、最终媒体库命名 |
| media-search | 证据检索、下载计划、canonical contract | 文件移动、Plex 写操作 |
| renaming | 文件 mapping、最终路径、移动、清理、整理结果 | 重新定义已确认下载计划 |
| plex-management | 扫描、最终路径定位、Plex 异常处理 | 重做下载计划或文件 mapping |

## 四、业务决策执行状态

### TODO-01 普通搜索取消 AI 强依赖（已实现）

- Wikipedia 与无需 Key 的豆瓣证据默认启用；TVDB 和 AI 在配置中启用后参与。
- 普通条目在多源证据能够严格唯一确认时直接生成 canonical contract，不调用 AI。
- 只有歧义、复杂关系或规则门禁失败时才调用 AI；AI 不可用不阻止高置信普通电影、整季或单集计划。

### TODO-02 AI 的业务权限边界（已实现）

- AI 可以参与搜索草案、下载后的文件映射、未匹配视频清理和 Plex 隔离工具调用。
- 用户确认后，AI 只能在 canonical contract 的身份、分类和季集目标内做映射，不得新增或改写业务目标。
- direct magnet 先补做 canonical metadata 规划，再进入同一受约束流程。

### TODO-03 open115 纯化为下载与存储（已实现）

- open115 不再为单文件套目录，不再接受或执行业务顶层命名。
- `download.completed` 返回未经改名的 `download_root`/`final_path`、真实 `resource_name`、完整 `file_tree`，并透传 canonical metadata 和 Prowlarr release 证据。
- 所有业务命名、视频筛选和清理由 renaming 承担。

### TODO-04 115 双授权体验（已实现）

- `/auth` 提供互相独立的“现有 Access/Refresh Token”与“115 扫码授权”入口。
- 扫码路线使用 PKCE；两条路线、扫码换取 Token 和后续刷新均通过同一原子存储器写回 Feature 私有配置。
- 私有配置权限为 `0600`；消息、事件、日志和异常不输出 Token。

### TODO-05 普通电影主视频选择（已实现）

- 固定顺序为 contract `source_hint`、唯一视频、Prowlarr 片源文件名唯一匹配、AI 多证据判断、可解释大小比例兜底。
- 多版本候选时 AI 输入包含 canonical evidence、Prowlarr release、下载根和完整文件树；输出必须精确引用真实文件，并明确其余视频的清理决策。
- 大小不再作为默认首选依据。

### TODO-06 未匹配大视频（已实现）

- 明确小样片仍可按大小规则清理。
- 未匹配的大视频必须由 AI 明确列入 `discard_files`；AI 未确认时整个下载根进入 `/未整理`，不会静默删除。

### TODO-07 保留并修正 legacy 无 contract 路径（已实现）

- 无 contract 路径不删除，供 direct magnet 使用。
- renaming 先调用 `media.search.resolve_metadata`，由 media-search 统一重新查询 Wikipedia、豆瓣、TVDB，并在严格证据不足时调用 AI。
- 成功后转换为 canonical contract 再整理；回查失败时保留旧 TVDB+AI 兼容尝试，仍无法确认则进入 `/未整理`，禁止仅凭文件名自动命名电影。

### TODO-08 mapping 失败落点（已实现）

- 目标路径冲突、映射不完整或 AI 无法确认时，必须在第一次目标写操作前终止，并把整个下载根移动到 `/未整理`。
- 已知业务冲突不再产生部分移动结果。
- 外部存储在执行中发生不可预测 I/O 中断时仍冻结现场、停止自动重放并通知人工恢复，不能伪装为可安全回滚的业务冲突。

### TODO-09 Plex 扫描粒度（已实现）

- job 继续按最终电影或剧集文件逐条持久化。
- 一个 `media.organized` 批次按 Plex Library 只记录一次扫描前快照并扫描一次，再按各自最终路径逐项定位验证。
- 已完成的 scanning step 在重试和中断恢复时复用，不强拆 Plex 自身扫描行为。

### TODO-10 Plex 普通媒体自动化边界（已实现）

- 普通媒体固定执行 `scan -> locate -> match -> zh-CN -> artwork -> streams` 一次。
- artwork 步骤必须尝试 TMDB/Fanart 无字海报并写入 Plex；无候选或外部服务失败时记录 warning，不伪造替换成功，也不重复覆盖。
- TVDB 官方 Special 的保留规则继续作为复杂 patch，不改变普通媒体链路。

### TODO-11 Plex MCP 写确认（已实现）

- 保留现有经确认需要的查询、扫描、匹配、海报、音轨、字幕和 job 工具；每个写工具继续需要一次性确认 Token。
- 新增 `plex_apply_metadata_batch`，只允许把 Fix Match、中文元数据刷新和无字海报设置打包为一次人工确认。
- 扫描、任务重试、音轨和字幕不能混入元数据批次；非 loopback MCP 必须配置 `mcp.auth_token`。

### TODO-12 统一失败与 dead-letter 告警（已确认延期）

- 当前继续由各 Feature 报告业务失败，Core dead-letter 通过 `/plugin doctor` 查看。
- 等业务规则稳定后再设计统一失败任务视图、主动 Telegram 告警和恢复入口；本轮不实现。

## 五、运维与发布 TODO

### OPS-TODO-01A GitHub 聚合发布（已实现）

- 已实现：`platform-v<semver>` tag 或显式手动触发聚合发布。
- 已实现：GitHub Actions 自动测试 Core、构建并推送 GHCR `linux/amd64` Core 镜像。
- 已实现：从四个独立 Feature branch 构建 Linux `.tpx`，发布 SHA-256 固定的 `catalog.yaml` 和不可变 GitHub Release。
- 已实现：同一 Feature version 对应不同 digest 时拒绝发布，防止覆盖 `name@version`。
- Core 更新：由 Unraid 拉取新镜像并允许重启一次。
- Feature 更新：Core 内完成下载、校验、shadow 启动、drain、原子切换和失败回滚，不重启 Core。

### OPS-TODO-01B 远程更新发现（已实现）

- 已实现：Core 启动时及默认每 6 小时安全刷新远程 catalog，以原子缓存保留上一次有效目录，并比较已安装 Feature 当前版本对应的最新稳定兼容版本。
- 已实现：目录或网络失败只跳过本轮检查，不影响 Core 与其他 Feature。
- 已实现：发现更新后只通知 `allowed_user`；用户点击一次“确认更新”才执行既有更新事务，也可选择“暂不更新”。
- 已实现：默认不静默升级；本地 `/config/plugins/catalog.yaml` 仍作为离线和固定版本入口。

### OPS-TODO-02 首次安装体验（已实现）

- 已实现：发送 `/plugin` 可查看已安装状态和 catalog 中尚未安装的 Feature。
- 已实现：每个 Feature 默认选择兼容当前 Core API 的最新稳定版本；预发布、不兼容和无效发布不会进入列表。
- 已实现：catalog 携带 manifest 派生的 capability 元数据，缺少依赖时先展示 provider 或 capability；满足条件后才出现安装按钮。
- 已实现：用户点击安装按钮才执行既有的 SHA-256、manifest、capability、健康检查和原子激活事务，不自动或批量安装。
- 已实现：精确 `name@version` 和本地 `.tpx` 路径继续作为离线与运维入口。
- 验收完成：普通使用者不需要进入 ttyd、克隆分支、安装构建依赖或手工计算 SHA-256。

## 六、后续评审顺序

1. 以真实下载验证 TODO-03 到 TODO-11 的端到端运行契约。
2. 业务规则稳定后重新启动 TODO-12 的统一失败与 dead-letter 告警设计。

## 七、执行规则

- 每个新增 TODO 必须先由用户明确确认业务结论。
- 确认后先写设计文档，再写测试，最后实现；本轮 TODO-01 至 TODO-11 已按该流程完成。
- P2、P3 不因本清单更新而自动恢复。
- 不把未确认的推荐方向当成已决定规则。
