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

## 四、待确认的业务决策

### TODO-01 普通搜索是否取消 AI 强依赖

- 当前：普通电影和普通剧集仍经过两阶段 AI 规划。
- 推荐：规则和证据源完成明确普通场景，AI 只处理歧义、关联电影、OVA、Special 和无法解析的范围。
- 待确认：是否正式改为“规则优先、AI 兜底”。
- 验收：AI 不可用时，明确的普通电影、整季和单集仍能生成可确认计划。

### TODO-02 AI 的业务权限边界

- 当前：AI 是搜索规划者，也是 renaming 的 mapping 兜底。
- 推荐：搜索阶段可生成待确认草案；确认后 AI 只能在 canonical contract 目标集合内做映射。
- 待确认：是否禁止 AI 在确认后新增媒体身份、季集编号或分类。
- 验收：任何 AI 输出都不能越过已确认 contract。

### TODO-03 open115 是否完全纯化为下载与存储

- 当前：open115 仍可能为单文件套顶层目录并重命名顶层目录。
- 推荐：open115 只返回真实下载根路径和文件树，所有业务命名交给 renaming。
- 待确认：是否删除 open115 的业务命名行为。
- 验收：更换 storage.provider 时不需要复制 115 特有命名逻辑。

### TODO-04 115 授权体验

- 当前：`/auth` 主要检查配置 token；刷新 token 的完整持久化体验仍不完善。
- 推荐：Telegram QR 授权作为可选入口；token 刷新原子写回 Feature 私有配置且不进入日志。
- 待确认：是否恢复 QR 授权并实现 token 自动持久化。
- 验收：Core 或 Feature 重启后不需要重复人工填写仍有效的 token。

### TODO-05 普通电影的主视频选择

- 当前：普通电影主要选择目录内最大视频，成功后清理源目录。
- 风险：花絮、错误版本或异常大文件可能被选成主电影。
- 推荐：已确认计划文件名或唯一候选优先；大小只作为可解释兜底。
- 待确认：主视频判定是否加入文件名、时长、分辨率和大小阈值。
- 验收：花絮或错误大文件不会覆盖目标电影。

### TODO-06 未匹配的大视频如何处理

- 已确认：小视频和其他非目标类型可以清理。
- 未确认：mapping 失败但体积较大的视频，是清理、保留原地，还是进入 `/未整理`。
- 推荐：大视频进入未整理并通知，小视频按阈值清理。
- 验收：可能属于下载计划的大视频不会静默删除。

### TODO-07 删除 renaming 的 legacy 无 contract 路径

- 当前：没有 canonical contract 时，renaming 仍可能重新查 TVDB 并调用 AI 推断。
- 推荐：media-search 成为唯一正常入口后，先告警一个版本，再删除 legacy 路径。
- 待确认：是否允许无 contract 的下载继续进入自动整理。
- 验收：renaming 只消费 canonical contract，不重复承担搜索职责。

### TODO-08 mapping 失败的落点与恢复方式

- 当前：完整 mapping 失败可进入 `/未整理`；发生部分移动后停止自动重放。
- 推荐：未发生移动时整包进入未整理；部分移动后冻结现场并输出恢复清单。
- 待确认：是否增加显式人工恢复命令。
- 验收：通知列出正式目录、未处理文件、失败文件、清理结果和人工动作。

### TODO-09 Plex 扫描粒度

- 当前：电影一个 job，剧集每集一个 job；可能对同一批次重复触发媒体库扫描。
- 推荐：一个 `media.organized` 批次只扫描一次，再按最终路径逐项验证。
- 待确认：job 保持逐条但 scan 合并，还是改为完整批次 job。
- 验收：同一下载批次不会重复扫描相同 Plex Library。

### TODO-10 Plex 普通媒体自动化边界

- 当前：普通条目可执行 scan、locate、match、中文刷新、海报和 stream 选择。
- 推荐：规范命名的普通电影与剧集交给 Plex 自动匹配；Feature 只负责扫描、出现校验、异常修复和临时 S00 patch。
- 待确认：普通入库是否停止自动 Fix Match、海报替换和音轨/字幕写操作。
- 验收：规范资源只需扫描即可入库，复杂行为只在异常路径触发。

### TODO-11 Plex MCP 写工具收缩

- 当前：MCP 同时暴露查询、scan、Fix Match、中文刷新、海报、音轨、字幕和 pipeline 操作。
- 推荐：保留只读查询、scan、必要的 Fix Match 和 job retry；高风险写操作需要一次性人工确认。
- 待确认：最终保留哪些 MCP 写工具。
- 验收：AI 不能在没有人工确认时直接执行高风险 Plex 写操作。

### TODO-12 统一失败与 dead-letter 告警

- 当前：业务失败主要由各 Feature 通知；Core dead-letter 主要通过 `/plugin doctor` 查看。
- 推荐：Core 报告事件投递失败，各 Feature 报告业务失败，dead-letter 主动发送 Telegram 告警并提供恢复入口。
- 待确认：是否建立统一失败任务视图和主动通知。
- 验收：任何终止自动处理的任务都能被用户主动看到。

## 五、运维与发布 TODO

### OPS-TODO-01A GitHub 聚合发布（已实现）

- 已实现：`platform-v<semver>` tag 或显式手动触发聚合发布。
- 已实现：GitHub Actions 自动测试 Core、构建并推送 GHCR `linux/amd64` Core 镜像。
- 已实现：从四个独立 Feature branch 构建 Linux `.tpx`，发布 SHA-256 固定的 `catalog.yaml` 和不可变 GitHub Release。
- 已实现：同一 Feature version 对应不同 digest 时拒绝发布，防止覆盖 `name@version`。
- Core 更新：由 Unraid 拉取新镜像并允许重启一次。
- Feature 更新：Core 内完成下载、校验、shadow 启动、drain、原子切换和失败回滚，不重启 Core。

### OPS-TODO-01B 远程更新发现（待实现）

- 待实现：Core 安全刷新远程 catalog 并比较已安装 Feature 版本。
- 待实现：发现兼容更新后 Telegram 通知，用户一次确认后执行更新；默认不静默升级。

### OPS-TODO-02 首次安装体验

- 当前：需要提供 `.tpx` 绝对路径或精确的 `name@version`。
- 推荐：增加可用 Feature 列表和安装按钮，默认选择 catalog 中兼容 Core API 的最新稳定版本。
- 验收：普通使用者不需要进入 ttyd、克隆分支、安装构建依赖或手工计算 SHA-256。

## 六、建议评审顺序

1. TODO-09、TODO-10、TODO-11：先收缩 Plex 普通入库与 MCP 边界。
2. TODO-05、TODO-06、TODO-08：确定主视频、删除、未整理和恢复策略。
3. TODO-01、TODO-02、TODO-07：确定规则、AI 和 legacy 的最终边界。
4. TODO-03、TODO-04：纯化 open115 和授权体验。
5. TODO-12：补齐统一失败告警。
6. OPS-TODO-01、OPS-TODO-02：完成面向普通使用者的发布和安装体验。

## 七、执行规则

- 每个 TODO 必须先由用户明确确认业务结论。
- 确认后先写设计文档，再写测试，最后实现。
- P2、P3 不因本清单更新而自动恢复。
- 不把未确认的推荐方向当成已决定规则。
