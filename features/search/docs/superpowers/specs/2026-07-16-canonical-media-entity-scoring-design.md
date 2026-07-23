# Telepiplex 规范媒体实体与固定评分设计

日期：2026-07-16

状态：已批准，进入本地实现

主责模块：`feature/search`

协作模块：`main`、`feature/rename`

## 1. 背景与问题定义

当前 `/s` 链路已经能并发读取 Wikipedia、豆瓣和 TVDB，并在严格规则失败后调用 AI。但 2026-07-15 的运行日志暴露出四类结构性问题：

1. 候选合并以标题交集为中心，年份、类型和关系条件没有形成可解释的联合评分。同名、近似名和搜索噪声会进入同一判断面；相反，缺少双语标题等单项条件又会直接阻断正确作品。
2. AI 直接生成完整 `media_metadata`。日志中的《杀马特我爱你》返回了 `title_zh`、`title_en` 等契约外字段，说明自然语言模型既承担判断又承担数据库/契约写入，失败时只能在末端被判为无效。
3. 首轮证据、AI 假设、二轮证据、AI 完整元数据串行执行，提示词还携带大量无关条目。它扩大延迟和 RPC 截止时间风险，也让取消后的工作难以及时收敛。
4. 用户确认界面只有文本。相同标题、年份接近或作品关系复杂时，没有海报这一强视觉证据；同时 Telepiplex 当前只支持安全的 `send_message` 和 `edit_message` 动作。

本设计把 AI 从“生成数据库记录”改成“按固定量表审片的评分员”，把证据事实、固定规则、关系发现、用户确认和持久化拆成独立层。

## 2. 目标

- 任何输入语言最终都落到稳定的规范检索标题。
- 非日文作品使用官方英文标题；日文作品使用官方罗马字原题，不使用英文翻译标题作为默认搜索和重命名标题。
- 以固定、版本化、可复算的 100 分量表排序候选，不做在线学习，不动态调整权重。
- AI 只能解释既有事实、发现可能关系并填写评分卡，不能发明来源事实、稳定 ID 或直接写入最终契约。
- 候选、原始证据、AI 评分卡和海报只在当前请求内存在；请求结束即释放。
- 只持久化用户最终选中的规范媒体实体及其规范关系，不积累未选候选和原始证据。
- 确认界面显示候选海报、标题、年份、类型、关系和得分，最终决定仍由用户作出。
- search 负责作品身份与作品关系；rename 只负责下载文件到既定集号/路径的映射。
- 整个规划阶段有 90 秒硬预算，每一阶段有独立预算和明确降级行为。

## 3. 非目标

- 不构建自动学习权重、用户画像或隐式偏好模型。
- 普通 `/s` 确认不创建永久的“同一输入永远选这个结果”覆盖规则。
- 不持久化搜索历史、未选候选、网页摘要、完整评分卡、提示词或海报二进制。
- 不让 rename 再次判断作品身份、电影/剧集关系或目标剧集。
- 不以增加无限搜索轮次来换取命中率；扩展检索最多一次。

## 4. 总体架构

```text
用户输入
  -> 意图解析与语言识别
  -> 规范实体注册表候选 + 外部证据源并发检索
  -> 请求内媒体实体图
  -> 确定性关系事实
  -> 复杂条目 RelationScout（可选）
  -> 关系假设定向复查
  -> HardGate
  -> 固定程序分 60 + AI 评分卡 40
  -> 海报候选确认
  -> 选中实体写入规范实体注册表
  -> 生成 confirmed media_metadata v1
  -> Prowlarr 片源搜索与下载交接
  -> rename 文件映射
```

三个模块保持独立：

- search 拥有请求图、证据适配、关系发现、评分、候选确认、实体注册表和 `media_metadata` 生成。
- telepiplex 只扩展安全图片动作和异步操作渲染，不理解媒体评分。
- rename 消费已确认的 `media_metadata`，不重新搜索作品身份；确定性文件映射失败时，AI 仅可在锁定的集号集合内映射文件。

## 5. 请求内媒体实体图

每次规划创建一个 `SearchGraph`，保存在内存中并绑定 `plan_id`。它包含：

- `CandidateEntity`：候选作品，最多 5 个。
- `EvidenceFact`：来源、来源内事实 ID、标题、年份、类型、稳定 ID、URL、海报和可用状态。
- `RelationHypothesis`：候选与目标剧集的关系假设，最多 3 个。
- `CandidateScore`：HardGate 结果、程序分、AI 评分卡、总分和原因码。
- `PosterRef`：只保存当前请求使用的远端 URL、来源和候选稳定键。

候选的内部稳定键按以下优先级生成：

1. `tvdb:<type>:<id>`；
2. `douban:<subject_id>`；
3. `wikipedia:<wikibase_item>`；
4. `title:<normalized canonical title>:<year>:<type>`。

不同来源只有在稳定 ID 明确对齐，或“规范标题 + 年份 + 类型”同时一致时才能合并。仅有包含关系、搜索命中或摘要提及不能合并实体。

生命周期规则：

- `confirmed`：先提取选中实体的规范快照并持久化，再释放整个 `SearchGraph`。
- `cancelled`、`failed`、`timed_out`：直接释放。
- Feature 重启：内存图自然消失；不会恢复半成品候选。
- 运行中计划继续沿用当前 operation/owner 互斥机制，不增加搜索历史表。

## 6. 持久化规范实体注册表

SQLite 文件位于 search 的 Feature 状态目录：`state/media_entities.db`。数据库只存已选中的规范实体，不存请求数据。

### 6.1 `canonical_entities`

| 字段 | 含义 |
| --- | --- |
| `entity_key` | 首选稳定键，主键 |
| `content_kind` | movie、series、special 等规范类型 |
| `year` | 首发年份 |
| `chinese_title` | 可用时的中文显示名 |
| `original_title` | 官方原文标题 |
| `original_language` | BCP-47/ISO 语言码，日文为 `ja` |
| `official_english_title` | 官方英文标题，可为空 |
| `romanized_original_title` | 官方罗马字标题，仅在有权威依据时填写 |
| `canonical_search_title` | 后续外部检索的唯一默认标题 |
| `search_title_policy` | `official_english` 或 `romanized_original` |
| `canonical_latin_title` | 传给 rename 的拉丁标题 |
| `poster_url` | 用户确认时看到的海报 URL |
| `poster_source` | 海报来源 |
| `external_ids_json` | 已验证的稳定 ID 映射 |
| `scoring_version` | 选中时使用的评分规则版本 |
| `created_at` / `updated_at` | 审计时间 |

### 6.2 `canonical_relations`

关系表只保存选中实体的规范关系快照：

- `source_entity_key`
- `relation_type`
- `target_entity_key`
- `target_chinese_title`
- `target_canonical_latin_title`
- `target_year`
- `target_external_ids_json`
- `mapping_kind`
- `season_number`
- `episode_number`
- `tvdb_episode_id`
- `confirmed_at`

目标剧集不因一次关系判断自动写成独立 `canonical_entities` 记录；只有它自己被用户选中过才拥有实体行。关系边仍可通过 `target_entity_key` 指向其稳定身份。

### 6.3 注册表的使用边界

- 注册表可作为一个高置信候选来源，补充已确认标题、稳定 ID 和海报。
- 它不绕过本次用户确认，也不创建全局输入覆盖。
- 外部稳定 ID 与注册表冲突时，冲突进入 HardGate，不能用历史选择强压当前证据。
- 更新采用单事务 UPSERT；只有候选确认成功后执行。

## 7. 标题与语言策略

输入语言只用于首轮发现，不决定最终沉淀标题。

### 7.1 非日文作品

- `canonical_search_title = official_english_title`
- `canonical_latin_title = official_english_title`
- 如果外部来源暂时没有可信的官方英文标题，候选可以展示，但不能持久化为最终实体；允许一次定向补查。

### 7.2 日文作品

- 仅当来源确认 `original_language = ja` 时启用日文策略。
- `canonical_search_title = romanized_original_title`
- `canonical_latin_title = romanized_original_title`
- 官方英文翻译只写入 `official_english_title` 作为参考，不参与默认 Prowlarr 查询和 rename。
- 不使用通用机器翻译临时生成罗马字；没有可信罗马字时进入人工候选，而不是伪造标题。

### 7.3 `media_metadata v1` 兼容映射

不提升 schema version，采用向后兼容的附加字段：

```json
{
  "identity": {
    "chinese_title": "进击的巨人",
    "english_title": "Shingeki no Kyojin",
    "original_title": "進撃の巨人",
    "original_language": "ja",
    "official_english_title": "Attack on Titan",
    "romanized_original_title": "Shingeki no Kyojin",
    "canonical_search_title": "Shingeki no Kyojin",
    "search_title_policy": "romanized_original"
  }
}
```

为兼容现有 SDK 和 rename，`identity.english_title` 的运行时语义明确为“规范拉丁标题”：日文填罗马字，其他语言填官方英文。

因此日文作品默认生成：

- 剧集目录：`进击的巨人 (Shingeki no Kyojin)`
- 文件主体：`Shingeki no Kyojin S01E01`

## 8. 关系发现

关系判断发生在最终评分之前，因为“这是独立电影还是某剧的特别篇”会直接改变候选实体、保存分类和重命名目标。

### 8.1 确定性阶段

规则层提取并验证：

- TVDB 官方 Season 00 / Special；
- 明确相同的系列稳定 ID；
- 来源中结构化的 prequel、sequel、spin-off、movie special 关系；
- 用户明确输入的季集范围。

普通独立作品没有复杂信号时跳过 AI 关系发现。

### 8.2 `RelationScout`

当出现“剧场版、特别篇、续作、前传、衍生、同名电影/剧集、来源季划分冲突”等复杂信号时，AI 只输出最多 3 个关系假设：

```json
{
  "candidate_key": "douban:123",
  "relation_type": "extension_movie",
  "target_series_keys": ["tvdb:series:456"],
  "fact_ids": ["douban:123:relation:1"],
  "verification_queries": {
    "tvdb": ["Official Latin Title"],
    "wikipedia": ["Official English Title film relation"]
  }
}
```

AI 不能把假设直接写为事实。系统用定向查询验证假设，再把验证结果加入请求图。无法验证的关系可以保留为低分候选，但不得获得官方映射标记。

## 9. 固定评分模型 v1

总分固定为 100 分。程序事实分 60，AI 语义评分 40。权重只通过发布新的 `scoring_version` 人工升级，不在运行中学习。

### 9.1 HardGate

以下情况直接排除候选，AI 分数不能补偿：

- 同一权威来源的稳定 ID 明确指向另一个实体；
- 用户明确指定 movie/series，而候选类型与其相反；
- 关系需要 TVDB 官方集号，但候选的 series ID 或 episode ID 与 TVDB 事实冲突；
- 缺少任何可审计的来源事实；
- 规范标题违反语言策略，且一次定向补查后仍无法得到可信官方英文或日文罗马字。

用户输入年份与候选年份不一致不是 HardGate。它降低“发行一致性”和“意图相关性”，从而允许处理口误，同时保证正确年份候选不会被误当成唯一答案。

### 9.2 程序事实分：60

1. 稳定身份，25 分
   - 两个来源通过相同稳定 ID 或权威跨库链接对齐：25。
   - 一个权威稳定 ID，且其他来源以标题、年份、类型支持：20。
   - 只有一个权威稳定 ID：15。
   - 只有标题聚类：0。
2. 独立来源支持，15 分
   - 3 个及以上独立来源：15。
   - 2 个：10。
   - 1 个：4。
3. 发行一致性，10 分
   - 明确年份完全一致：10。
   - 来源间相差 1 年但发行日期可解释：6。
   - 未提供年份：4。
   - 与用户年份或权威来源冲突：0。
4. 类型与范围，10 分
   - 类型及季集范围均由结构化事实确认：10。
   - 类型确认、范围未指定：7。
   - 只有类型推断：3。
   - 冲突：0，必要时由 HardGate 排除。

### 9.3 AI 评分卡：40

AI 只能引用传入的 `fact_ids`，输出：

1. 标题等价，20 分：别名、跨语言标题、拼写和语境是否指向同一作品。
2. 关系一致，10 分：独立作品、目标剧集和 Season 00 映射是否与事实一致。
3. 意图相关，10 分：候选是否满足用户给出的标题、年份、类型和季集意图。

程序校验每个引用的 fact ID，限制各项分值范围并重新计算总分。引用不存在、字段越界或 JSON 契约无效时，AI 分记 0；不会接受 AI 返回的 `media_metadata`。

### 9.4 排序与交互阈值

- `total >= 85` 且领先第二名至少 10 分：标记为推荐项，但仍需用户确认。
- `65 <= total < 85`，或领先不足 10 分：展示候选供用户选择。
- `total < 65`：执行一次受控扩展检索；仍不足则明确失败。
- 展示候选最多 5 个，RelationScout 假设最多 3 个，每来源每候选定向结果最多 3 个。

普通确认只选定本次实体，不改变评分权重，也不创建输入覆盖。

## 10. 海报确认体验与 Telepiplex 动作契约

候选卡片显示：

- 海报；
- 中文显示名；
- 规范拉丁标题；
- 年份和 movie/series 类型；
- 若有关联，显示目标剧集和映射方式；
- 总分、推荐标记和简短理由；
- “选择此项”“上一项”“下一项”“退出”按钮。

海报必须与候选稳定键绑定，不能按列表位置临时复用。

Telepiplex 新增两个白名单动作：

```json
{
  "kind": "send_photo",
  "text": "caption",
  "data": {"photo_url": "https://...", "keyboard": []}
}
```

```json
{
  "kind": "edit_photo",
  "text": "caption",
  "data": {"photo_url": "https://...", "keyboard": []}
}
```

Telepiplex 负责 URL/文本/键盘校验和 Telegram API 调用。图片发送或编辑失败时，执行一次安全降级：发送纯文本候选卡片，保留同一键盘和候选回调。不得因为海报失败丢失搜索任务。

异步 `operation.report` 的 `details` 可携带 `photo_url` 和 `keyboard`；渲染器根据当前消息类型选择编辑媒体或新发照片。若旧消息不可编辑，沿用现有“编辑失败后新发消息”恢复策略。

## 11. 时间预算、取消和错误处理

规划总预算为 90 秒：

| 阶段 | 上限 | 超时行为 |
| --- | ---: | --- |
| 首轮外部来源并发检索 | 15 秒 | 单来源记 `server_down`/`timed_out`，其余继续 |
| RelationScout | 20 秒 | 跳过未完成假设，保留确定性关系 |
| 关系定向验证 | 15 秒 | 未验证关系降分，不伪装为官方关系 |
| AI 评分卡 | 25 秒 | AI 分为 0，若程序分不足则失败 |
| 海报整理与最终候选 | 15 秒 | 无海报也可文本确认 |

实现要求：

- 来源请求并发运行，不因一个来源超时串行拖慢全部来源。
- 每一阶段使用单调时钟计算剩余总预算，阶段预算不得突破总预算。
- 取消信号在来源返回、AI 返回、定向查询返回和持久化前都检查。
- `/s` 命令始终快速返回后台 operation，不让 Telegram command RPC 等待规划完成。
- rename 的 `media.search.resolve_metadata` RPC 使用现有 120 秒调用截止时间；search 自身必须在 90 秒内成功或返回结构化 `metadata_unresolved`，预留传输和清理时间。
- `deadline_exceeded`、`timed_out`、`server_down` 分开记录，日志只输出候选键、阶段、耗时和原因码，不记录整份提示词/来源正文。
- AI JSON 解析或契约失败返回明确 `ai_scorecard_invalid`，不再出现“日志显示 AI 成功但计划静默消失”。

## 12. `media_metadata` 责任边界

search 输出最终 confirmed `media_metadata v1`：

- `identity`：规范标题、年份、类型、海报、稳定 ID 和语言策略；
- `relation`：与目标剧集的已确认关系；
- `placement`：library/category、Season 00 或 standalone 映射；
- `items`：已验证的逻辑集号；
- `evidence`：仅随本次下游任务传递的最小决策摘要，不写入实体数据库；
- `warnings`：未验证关系、来源缺失等可见警告。

交互式 `/s` 必须经过本次用户确认。非交互的
`media.search.resolve_metadata` 只能按稳定 ID，或“规范标题 + 年份”精确复用已经
持久化的实体；它不能自动选择新候选，也不能写入新实体。注册表没有精确命中时返回
`metadata_unresolved`。

rename 必须遵守：

- 有 confirmed `media_metadata`：直接使用，不重判作品身份。
- 没有元数据：通过 `media.search.resolve_metadata` 请求 search。
- search 无法确认：移入 `/未整理`，不启动 legacy TVDB + AI 身份兜底。
- 对已锁定 series、season、episode 集合，优先确定性匹配文件；只有文件名/目录结构无法唯一映射时，AI 才能选择 `file_tree` 中的文件与既定集号配对。
- AI 不得修改 `target_series`、`library_type`、`category_kind`、season 或 episode。

## 13. 迁移策略

### 13.1 search

- 新增请求图、实体注册表、标题策略、固定评分和 RelationScout。
- 保留现有 provider adapters、operation 生命周期、Prowlarr 搜索和下载交接。
- 将当前 AI “完整生成 media_metadata”替换为两个严格输出：关系假设与评分卡。
- deterministic planner 改为候选事实生成器和程序评分器，不再用“缺少双语标题”单项直接阻断正确实体。

### 13.2 telepiplex

- SDK ResponseAction 和 handler 白名单加入 `send_photo`、`edit_photo`。
- operation renderer 支持图片消息状态及安全降级。
- `media_metadata v1` validator 接受并校验可选语言策略字段；保留旧实体兼容。

### 13.3 rename

- 删除无 confirmed 元数据时的 legacy TVDB + AI 身份/关系推断路径。
- 保留并收紧文件到已确认集号的 AI 映射。
- 沿用 `identity.english_title` 作为规范拉丁标题，并增加日文罗马字回归测试。

三个分支各自提交和验证，不把模块源码合并进 `main`，本轮不推送远端。

## 14. 测试与验收

### 14.1 search 单元测试

- 同名 movie/series 不会因标题相同被错误合并。
- 用户年份错误只扣分，不直接排除正确标题候选。
- 《黑暗荣耀 2019》保留《黑暗荣耀 2022》为低意图分候选，不把《终结者：黑暗命运》因同年误判为标题匹配。
- 《杀马特我爱你》在 Wikipedia 噪声和 TVDB 缺失时，能用两个独立来源进入候选，不要求 AI 写 `media_metadata`。
- 《布达佩斯大饭店》选择官方英文 `The Grand Budapest Hotel`，不会沉淀其他语言标题。
- 《想见你》电影和剧集保持独立实体，关系验证发生在评分前。
- 日文输入、中文输入和英文输入最终都选择日文官方罗马字作为 `canonical_search_title`。
- 非日文作品选择官方英文标题。
- AI 引用未知 fact ID、分数越界或输出错误字段时被拒绝。
- 5/3/3 数量上限和 85/65/10 阈值固定。
- 请求确认、取消、失败、超时后，未选候选不进入 SQLite。
- 选中实体 UPSERT 幂等，原始证据和完整评分卡不入库。
- 90 秒总预算和各阶段降级可用虚拟时钟测试。

### 14.2 Telepiplex 单元测试

- `send_photo`、`edit_photo` 的白名单、参数校验和键盘命名空间正确。
- 图片编辑失败后新发照片；照片发送失败后纯文本降级。
- operation 恢复和 revision 保护在图片消息下仍成立。
- 旧的文本动作行为不变。
- 可选标题语言字段通过 `media_metadata v1` 校验，非法策略组合被拒绝。

### 14.3 rename 单元测试

- 日文系列目录为 `中文名 (罗马字)`，文件主体使用罗马字，不使用英文翻译。
- 缺少 confirmed 元数据且 search 无法解析时进入 `/未整理`。
- 不再调用 legacy TVDB + AI 身份推断。
- deterministic 文件映射仍优先；AI 只映射锁定集号且不能改写身份字段。

### 14.4 跨模块验收

完整链路至少验证：

1. `/s` 任意语言输入；
2. 规范标题检索；
3. 关系发现与固定评分；
4. 海报候选人工确认；
5. 只持久化选中实体；
6. Prowlarr 查询使用规范检索标题；
7. confirmed `media_metadata` 随下载事件传递；
8. rename 使用相同规范拉丁标题和已确认关系；
9. Plex 入队前路径与媒体身份一致。

验收底线：一个成功的外部查询不等于链路成功；必须验证 `search -> confirmation -> download handoff -> rename -> Plex enqueue` 的完整契约。
