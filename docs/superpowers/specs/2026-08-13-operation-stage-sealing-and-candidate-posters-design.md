# 前台任务阶段封口与候选海报设计

## 目标

统一 telepiplex Host、search、download、rename 与 sync 的 Telegram 前台消息生命周期：同一能力模块内部原位更新，模块完成时原位封口；跨能力模块接力时在下方创建新消息。媒体身份确认是全链路唯一的持久中间里程碑，必须覆盖当前作品候选或身份解析消息并封口，后续执行阶段再新建消息。

同时在作品候选展示前，对缺失海报的 Wikipedia/Wikidata 候选执行有总时限的豆瓣、TMDB、TVDB 并行补全。候选类型只有在结构化证据明确时展示动画电影或动画剧集；缺少动画正证据时只展示电影或剧集，不把“未识别为动画”推断成真人。

## 当前问题与根因

- Host 的 operation 只保存一个 `message_id/message_kind` 游标。
- 作品候选宫格属于 operation 当前消息，但已确认身份通过独立 milestone 另发消息，没有覆盖当前候选，也没有轮换 operation 游标。
- search、download、rename 后续进度继续改写旧游标；图片与文本消息类型切换失败时又会追加消息，导致身份海报和下一阶段进度乱序。
- Feature 所有权变化当前会清空消息游标，但模块内部没有“封口当前消息并轮换游标”的显式契约。

## 消息类型和行为

1. 静态结果：单次发送，不进入 operation 游标。
2. 临时交互：目录、片源、Plex 海报、音轨、字幕或配置选择，原位更新，用完后继续复用当前消息。
3. 媒体身份里程碑：原位覆盖当前候选或解析消息，移除按钮并封口，然后轮换游标。
4. 能力执行消息：能力内部持续原位更新；能力完成时原位写入封口状态，然后跨 Feature 接力。
5. 后台阶段：没有用户判断信息时不产生前台消息。

“封口”保留消息内容，但清除 operation 的活动消息游标。下一个非重复 operation report 因没有游标而在下方创建新消息。封口操作必须按 `operation_id + milestone_id` 幂等，重试不能重复生成消息或重复轮换游标。

## Host 与 SDK 契约

扩展 `operation.milestone`，增加 `mode`：

- `identity`：把媒体身份卡覆盖到当前 operation 消息，成功后封口并轮换游标。
- `stage`：把模块完成摘要覆盖到当前 operation 消息，成功后封口并轮换游标。

SDK 提供：

- `publish_operation_milestone(..., mode="identity", photo_url=...)`
- `seal_operation_stage(operation_id, milestone_id, text)`，内部调用同一 RPC 的 `stage` 模式。

Host 负责 Telegram 编辑与降级策略，Feature 不持有 Telegram 消息 ID。身份卡优先编辑当前照片；当前为文本或编辑媒体失败时，清除旧按钮并发送新的身份卡，但只有新消息发送成功后才轮换游标。阶段封口优先编辑当前文本；当前为照片时使用照片 caption 封口，避免因消息种类转换追加乱序消息。没有当前游标时允许发送一条新消息作为里程碑。

数据库继续使用 `operation_milestones` 做幂等声明，并增加持久化的 `delivery_started`、`delivered`、实际送达 `message_id/message_kind`。Host 在调用 Telegram 前先标记“投递已开始”；成功投递后，在同一数据库事务内记录实际消息目标并清空 `operations.message_id/message_kind`，再完成 milestone。若进程在 Telegram 返回前后中断，恢复时把该投递视为结果不确定但可能已送达，直接完成并停止重发，避免产生重复身份卡或阶段消息；正常可观测的发送失败仍释放未送达声明并允许重试。若进程只在完成 milestone 前中断，则重试直接复用已记录的送达目标。旧数据库中既有 milestone 均来自旧版成功投递，迁移时标记为已送达。milestone 投递与普通 operation report 共用 `operation_id` 级渲染锁，避免新阶段进度插入“编辑完成、游标轮换”之间。

## 业务数据流

### search

1. 规划媒体证据并原位更新。
2. 若需要候选，在统一总时限内并行尝试豆瓣、TMDB、TVDB 补全缺失海报，之后一次性展示候选宫格；单一 Provider 失败不能阻断候选展示。
3. 用户确认或自动唯一匹配后，以 identity milestone 覆盖当前候选或规划消息并封口。
4. 身份里程碑确认送达后才创建片源搜索消息，原位更新搜索、片源选择和解析状态。
5. 先由 Host 接受对 download 的 provisional handoff，以确认下游可用；随后以 stage milestone 将 search 消息封口为“资源搜索已完成”，只有封口成功才真正调用 download capability。失败则在当前消息原位终止，不创建下游消息。

### download

1. 接受 search 接力或 `/magnet` 后创建 download 执行消息。
2. 原位更新保存目录、提交、下载和读取文件树。
3. 文件树成功读取并持久化 `downloaded` 结果、且 Host 接受对 rename 的 provisional handoff 后，以 stage milestone 将当前消息封口为“115 下载完成”。
4. 只有封口成功后才发布 `download.completed`，确保 rename 的新消息位于 download 完成消息之下。

### rename

1. 接受 download 后创建“检查媒体元数据/规划目录”消息。
2. 若当前 operation 已经成功展示同一身份 milestone，直接进入规划和整理，不重复身份卡。
3. 若当前 operation 尚未展示身份卡，无论自动唯一匹配还是用户选择候选，都以 identity milestone 覆盖当前解析/候选消息并封口，再新建整理消息。
4. 原位更新规划、冲突验证、目录准备、移动、重命名和清理。
5. 整理与 `media.organized` 持久状态准备完成、且 Host 接受对 sync 的 provisional handoff 后，以 stage milestone 封口当前消息为“媒体整理完成”，之后才发布给 sync。

### sync

rename 所有权接力已经使 Host 新建 Plex 消息。sync 在同一消息内原位更新扫描、海报、音轨、字幕及临时选择，最终 completed/failed/cancelled 原位终止。它不需要额外中间封口或新的持久 milestone。

## 身份去重

身份 milestone ID 继续由稳定媒体身份摘要生成。常规 `search -> download -> rename` 链路会把已确认的 `media_metadata` 直接传给 rename，因此 rename 不再发身份 milestone；只有 rename 自己解析或用户确认候选时才发布并封口身份卡。同一 operation 内稳定 milestone ID 仍保证重试幂等。

## 候选海报与媒体类型

- 只补全当前将展示的候选，且只处理缺失海报项。
- 豆瓣、TMDB、TVDB 并行执行，每个 Provider 使用现有请求超时，外层再设候选补全总时限。
- 通过候选已有的外部 ID、标题、年份、国家和媒体类型约束匹配；Provider 结果必须通过现有实体锚定/合并规则，不能仅按模糊标题塞入海报。
- 补全完成或总时限到达后一次性展示候选，不在 Telegram 中二次跳动。
- 全部 Provider 失败时继续使用标题占位宫格。
- Wikipedia/Wikidata `instance_of`、genre 和已交叉验证的 Provider 类型可作为动画证据。缺少动画证据不等于真人，因此统一退化为电影或剧集；本次不展示真人分类。

## rename 1.4.0 字幕能力不回归基线

本次实现以 rename `1.4.0` 的外挂字幕能力为不回归基线；交付版本提升为 `1.4.1`，但不得修改已有外挂字幕语义：

- 支持 `.srt`、`.ass`、`.sup`、`.vtt`。
- 简体中文加英文双语优先，简体中文单语次选，输出 `.chi` 后缀并保留扩展名。
- 已知繁体中文、英文及其他语言排除；未知语言或无法确定季集映射时阻断当前直接子项。
- 视频与字幕共享同一 OrganizationPlan、写前冲突检查、串行写入、写后验证和延迟清理。
- 支持跨目录、稀疏季度覆盖和纯字幕任务。

本次消息阶段调整不得修改字幕规划、命名、冲突检查或清理语义。

## 错误与恢复

- milestone 投递失败：不轮换游标、不接力下游，释放幂等声明并返回可重试错误。
- milestone 投递结果不确定：Feature 使用同一 milestone ID 重试，Host 幂等处理。
- 候选海报 Provider 失败或超时：记录 Provider 结果，继续其余 Provider，最终使用已有海报或占位图。
- 下游 Feature 不可用：当前模块消息原位改为终态说明，不伪造下游消息。
- Host 重启恢复：持久化 operation 游标和 milestone 声明继续决定是否需要投递；已封口消息不会被恢复任务重新占用。

## 验证

- Host：文本/照片身份覆盖、阶段封口、游标轮换、失败重试和 duplicate 幂等。
- search：候选海报并行补全、总时限、身份先于片源搜索、search 封口先于 download 接力。
- download：文件树读取后先封口，再发布 `download.completed`。
- rename：自动匹配和候选确认均生成身份卡；上游同身份不重复；整理封口先于 sync 接力。
- sync：handoff 后新建消息，Plex 生命周期在单条消息内结束。
- 端到端：`search -> download -> rename -> sync` 的消息顺序。
- rename：完整 Feature 测试以及字幕专属测试，确认 `1.4.1` 继续保持 `1.4.0` 的字幕能力。

## 发布身份

- Host/Core：`3.4.24`
- Host API：`1.6`
- Plugin SDK：`1.2.2`
- search：`1.9.3`，要求 `host_api >=1.6` 与 SDK `1.2.2`
- download：`1.0.9`，要求 `host_api >=1.6` 与 SDK `1.2.2`
- rename：`1.4.1`，要求 `host_api >=1.6` 与 SDK `1.2.2`
- sync 与 caption 没有生产代码改动，版本保持不变。
