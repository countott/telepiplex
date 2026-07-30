# Search selected-candidate source verification

本文件定义 search 将来源补查从“候选展示前的全候选收敛”移动到
“用户选择后的单候选验证”的新业务合同。它取代
`2026-07-27-unified-anchored-search-design.md` 和
`2026-07-28-search-source-convergence-and-diagnostics-design.md` 中关于
全候选缺失来源补查、补查后全局重新建图的规定；其他候选数量、
`media_metadata v1`、Prowlarr 硬门禁和 download handoff 合同保持不变。

## 问题与目标

当前文本搜索先广泛召回 Wikipedia、豆瓣和 TVDB，AI 整理出候选后，
程序会为所有候选补查缺失 Provider，再把所有补查事实放回同一个全局
事实图。任何一个候选的补查结果发生稳定事实冲突，都会在用户看到候选
之前终止整个搜索。

该顺序把“补齐所有可能候选”置于“让用户确认目标”之前，导致用户最终
不会选择的候选也能拖垮任务。来源补查的业务目的应是验证并补全用户选中
的结果，而不是要求所有召回结果预先完成多来源一致性证明。

新目标如下：

- 首轮广泛召回只负责形成可区分、可展示的作品候选；
- 来源召回事实允许不完整，不因候选缺少某个 Provider 而阻止展示；
- 用户选择一个候选后，才对该候选执行精确读取和缺失来源补查；
- 补查结果只更新被选候选，不重新合并或改变其他候选；
- 只有被选候选形成严格 `media_metadata v1` 后才能进入 Prowlarr；
- 机器无法唯一确认搜索结果时，让用户选择结果，不以规划失败替代用户
  仲裁。

## 方案选择

评估三种方案：

1. 保留全候选预补查，只放宽特定字段冲突。改动较小，但仍要求机器在
   用户选择前解决所有来源分歧，且会持续增加字段权威等级和例外规则。
2. 完全取消来源补查。链路最短，但跨语言标题、分季条目、海报和严格
   TVDB 范围可能无法补齐，选中后仍无法生成可靠合同。
3. 候选优先、选择后单候选验证。首轮只形成候选，用户选择后再精确读取
   锚点并补查该候选缺失来源。

采用方案 3。它保留多来源验证价值，同时把验证成本和失败范围限制在用户
真正选择的对象上。

## 两阶段事实模型

search 明确区分发现事实和验证事实。

### 发现事实

首轮 Provider 返回的每一次事实记录都是请求内的 `DiscoveryFact`。
它保存：

- 请求内唯一 occurrence ID；
- Provider 与 Provider 稳定 ID；
- 查询语言和查询提示；
- 标题、年份、媒体类型、外部 ID、来源链接和海报；
- 原始结果对应的稳定来源引用。

发现阶段不要求同一 Provider 稳定 ID 的多个 occurrence 先收敛成唯一
年份或媒体类型。不同语言、不同查询提示返回的同一稳定 ID 可以并存，
但不得被当成已经验证的正式媒体事实。

AI 只能引用 occurrence ID 整理候选。程序验证 occurrence ID 存在、
同一 occurrence 不跨候选复用、候选锚点属于本候选，以及角色和范围格式
合法。发现事实不能直接生成 confirmed `media_metadata v1`。

### 验证事实

用户选择候选后，程序首先精确读取该候选的锚点来源。锚点精确读取结果
建立本次搜索的锁定身份；其他来源只能作为该身份的补充证据，不能推翻
用户已经选择的锚点。

随后程序只为该候选缺失的 Provider 生成并执行定向补查。补查返回的条目
只有在稳定 ID、标题关系和媒体层级能够与锁定身份验证兼容时，才绑定到
被选候选。指向其他作品的结果作为不匹配补查结果记录，不进入候选，也
不使整个计划失败。

验证事实形成单候选 `VerifiedGraph`，严格
`media_metadata v1`、Prowlarr Query 和 download handoff 只能从该图
生成。

### 补查结果裁决

补查结果按以下顺序裁决，不建立 Provider 或字段权威等级表：

1. 与冻结候选已有 Provider 稳定 ID 相同的精确读取结果，归入该候选；
2. 包含能够直接指向锁定锚点的跨来源外部 ID 时，归入该候选；
3. 没有直接 ID 关系时，AI 只能从本次被选候选的补查结果中提出绑定；
   程序要求该结果拥有真实稳定 ID 和来源链接，且不违反用户原文明确给出
   的媒体类型、年份、季集范围；
4. 恰好一个结果通过上述约束时可以归入候选；没有结果通过时将该 Provider
   记为 unresolved；
5. 两个或以上结果仍然合理，或补查结果要替换锚点已有的非空身份字段时，
   转换为用户可选择的搜索结果，不自动合并。

补查事实只能补全锚点缺失的信息，不能静默覆盖锚点的标题身份、媒体类型、
年份或稳定 ID。用户选择其他结果时，程序创建新的锁定锚点并重新执行
单候选验证，而不是修改原 Provider 事实。

## AI 与程序职责

### AI

首轮候选编辑器：

- 根据用户意图和 `DiscoveryFact` occurrence ID 整理 0–6 个候选；
- 为每个候选选择一个已存在 occurrence 作为锚点；
- 标注 movie、series root、season、episode 和 related work 关系；
- 给出候选排序、置信度和面向用户的简短理由；
- 不生成标题、年份、URL、外部 ID、Provider 稳定 ID 或 Prowlarr
  Query。

选择后的补查提示编辑器：

- 输入中只包含被选候选；
- 只能针对该候选缺失的 Provider 输出最多三个跨语言纯片名；
- 不得输出其他候选 ID、年份结论、稳定 ID、URL 或正式元数据。

补查后的候选编辑器：

- 必须保留用户锁定的候选 ID 和锚点稳定身份；
- 最多输出一个候选；
- 只能绑定程序验证通过的精确读取和补查事实；
- 不得通过重排、替换锚点或新增候选覆盖用户选择。

### 程序

- 执行输入解析、Provider 查询和发现事实 occurrence 编号；
- 验证所有 AI 引用和候选结构；
- 冻结首轮候选列表，保持其他候选在选择后不变；
- 精确读取被选锚点，执行被选候选的缺失来源补查；
- 判断补查事实是否与锁定身份兼容；
- 构建严格 `media_metadata v1` 和 Prowlarr Query；
- 执行 Prowlarr 查询、去重、硬门禁、排序和 download handoff；
- 任何正式标题、年份、媒体类型、外部 ID、季集 inventory、URL 和
  海报只能来自验证事实。

### 用户

- 在 2–6 个发现候选中选择正确搜索结果；
- 必要时选择剧集范围或关联电影整理方式；
- 被选候选验证失败时，可以返回原候选列表选择其他结果；
- 本次设计只在当前 search plan 内保存选择，不建立跨请求学习或全局
  事实修正。

## 交互与状态

### 候选展示

首轮候选按钮继续表达“选择并验证”，不能承诺已经形成正式元数据。
候选卡至少显示：

- 用户可读标题；
- 年份和电影/剧集类型（存在时）；
- 海报（存在时）；
- 已命中的来源；
- AI 候选理由。

缺少 Provider、不同来源字段不完整或发现 occurrence 存在差异，只作为
候选的验证状态，不显示内部枚举、fact ID 或冲突字段名。

文本搜索一个候选时可以自动选择并进入单候选验证；直链入口继续锁定唯一
锚点并直接进入同一验证阶段。

### 被选候选验证

选择后，plan 保存：

- `selected_candidate_id`；
- 锚点 Provider、稳定 ID 和精确来源引用；
- 首轮候选版本；
- 当前验证阶段和已完成 Provider；
- 返回候选列表所需的冻结候选快照。

验证成功后生成严格合同，并进入现有剧集范围确认和 Prowlarr 流程。

验证失败时：

- 锚点暂时读取失败：提供“重试验证”“返回候选”“退出”；
- 锚点确定不存在或身份不匹配：把该候选标记为不可用并返回候选列表；
- 补查 Provider 超时、限流或无结果：记录为 unresolved；现有已验证事实
  足以生成严格合同时允许继续；
- 缺少严格合同必要字段：保留候选列表，说明无法完成验证，允许选择其他
  候选；
- 精确验证得到多个不同作品：把这些作品转换成新的可选择结果交给用户，
  不自动挑选，也不返回全局 `source_fact_conflict`。

选择后的任何失败都只影响被选候选，不删除、重排或重新补查其他冻结候选。

## 非交互 capability

`media.search resolve_metadata` 没有候选选择 UI：

- 发现阶段恰好一个候选时，自动锁定并执行单候选验证；
- 多个候选时返回结构化 `metadata_ambiguous`，不得自动选择；
- 返回值可以携带有限的候选摘要供调用方展示，但本次不修改 rename 的
  交互流程；
- 单候选验证失败时继续返回明确的 metadata 错误，不进入 Prowlarr。

## Prowlarr 与交接边界

- Prowlarr 仍不参与作品身份判断；
- 未形成严格且 confirmed 的 `media_metadata v1` 时不得查询 Prowlarr；
- Prowlarr Query 只从被选候选的验证事实生成；
- release gate、用户片源选择、magnet 解析以及
  `media_metadata + naming_metadata + release` handoff 合同保持不变。

## 日志

新增或调整以下结构化事件：

- `search_discovery_fact`：Provider、occurrence ID、稳定 ID 和查询摘要；
- `search_candidate status=ready`：候选 ID、锚点、已命中和缺失来源；
- `search_candidate status=selected`：被选候选和锚点；
- `search_selected_verification status=started|ready|failed`；
- `search_supplement status=planned|completed`：只允许出现被选候选 ID；
- `search_supplement status=rejected`：记录不兼容补查结果的 Provider、
  稳定 ID 和原因；
- `search_metadata status=incomplete|ready`：只描述当前被选候选。

日志不得包含 API Key、Token、Cookie、Header、未脱敏 URL 或 Provider
原始正文。

## 验收

### 单元与集成合同

- 同一 Wikipedia QID 的中英文发现 occurrence 即使年份或媒体类型不同，
  也能进入候选编辑和用户选择，不在 AI 前抛出
  `source_fact_conflict`。
- 首轮候选形成后，不调用缺失来源补查。
- 选择候选 c3 后，补查上下文和 Provider 请求只包含 c3；c1、c2 和 c4
  的冻结快照保持不变。
- 被选候选补查返回同一稳定 ID 的重复记录时，只在该候选的验证图内处理。
- 被选候选补查命中其他作品时，该事实被拒绝，不污染被选候选，也不影响
  其他候选。
- 被选候选验证失败后可以返回原候选列表并选择另一个候选。
- 文本单候选和直链候选自动进入同一验证阶段。
- `resolve_metadata` 单候选自动验证，多候选返回
  `metadata_ambiguous`。
- Prowlarr 在严格元数据形成前从不执行；验证成功后的 query、release
  gate 和 download handoff 合同保持不变。

### 真实问题语料

- `进击的巨人` 首轮展示可区分的整剧、动画电影和真人电影候选；未选择
  的电影候选不得触发 Wikipedia 补查。
- 选择其中一个候选后，只补查该候选缺失来源，并能生成相应严格合同或
  返回该候选的局部验证错误。
- `Rick and Morty S09E10` 的同一 Wikipedia QID 多语言字段差异不得在
  AI 前终止搜索；唯一候选自动验证并保留 S09E10 范围。
- `Rick and morty season 09` 保留第 9 季意图，候选确认后再执行来源
  补查。
- 豆瓣或 TVDB 直链继续锁定唯一稳定身份，AI 和补查不得改变锚点。

## 不在本次范围

- 跨请求保存用户选择；
- 用用户选择覆盖 Provider 原始事实；
- 建立全局媒体实体数据库或反馈学习系统；
- 修改 Prowlarr release gate、release ranking 或下载 Feature；
- 修改 rename、sync、caption 或其他 Feature 的交互。
