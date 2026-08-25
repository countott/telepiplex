# Search 候选准入、消息段与最小媒体合同设计

**日期：** 2026-08-25  
**状态：** 已完成分段设计确认，等待书面设计复核  
**范围：** Host、Plugin SDK、search、download、rename  
**目标版本：** Host `v3.6.0-host`、Host API `1.7`、SDK `1.4.0`、search `1.12.0`、download `1.1.0`、rename `1.6.0`

## 1. 目标

本次迭代把 2026-08-25 Search 全流程复测暴露的问题收敛到三个明确合同：

1. Host 用持久化消息段管理 Telegram 生命周期，消除并发 revision 产生的重复候选、重复身份状态和重复 Prowlarr 消息。
2. search 直接使用 Wikipedia 搜索结果的匹配与排名能力决定候选召回，不在 query 层人工补词，也不再用标题精确相等阻断有效搜索结果。
3. 新建最小 `media_metadata v2`，只把身份索引、命名、范围和分类交给 download 与 rename；完整 Provider 事实留在 search 内部，不再沿全链路传播。

同时终结已经取消或进入其他终态的旧 rename durable job，修正 Provider 错误分类和 operation 日志串线，并保持当前自动产品流程止于 rename：

```text
search -> confirmation -> Prowlarr -> download -> rename -> completed
```

sync 与 caption 不在本次生产改动范围内，也不恢复自动 Plex 接力。

## 2. 事实基线

新版复测中的 Telegram 消息实际形成以下拓扑：

```text
1996  正在识别媒体                  未被覆盖
1997  作品候选                      孤儿消息，Feature callback 无效
1998  相同作品候选 -> 最终身份       当前有效消息
1999  正在确认媒体身份              孤儿消息
2000  Prowlarr 5/25                 孤儿消息，Feature callback 无效
2001  Prowlarr 11/25 -> 片源 -> 下载 当前有效消息
```

根因不是 milestone 重复投递或 Prowlarr 结果未去重，而是旧 revision 已经进入 Telegram I/O、新 revision 又已被 Host 接受。旧消息发送成功后，精确 revision CAS 只能阻止它取得当前游标，无法撤销已经产生的 Telegram 副作用。Feature callback 又只认可当前 `message_id`，因此旧卡片看起来可以点击，实际无效。

同一归档中的实际连续点击还证明，候选选择后同步 hydration 可持续约 22 秒。第一次点击已经成功，但原卡片没有立即移除键盘或显示持久处理中状态，诱导用户再次点击。

`死神 千年血战` 与 `死神千年血战` 都完整进入 search，失败点不是空格丢失，而是 Wikipedia 结果进入根作品候选前被标题精确相等门槛排除。

最终身份中的 `Q17、日本` 说明现有 `media_metadata v1` 同时携带原始 Wikidata QID 和本地化 Provider 值。国家字段本身不参与下载或确定性重命名；这个问题的正确边界是缩减下游合同，而不是继续扩大跨 Provider 资料归一化。

本次死神任务在下载约 95.54% 时由用户取消，没有产生 `download.completed`，也没有进入 rename。另一个历史 rename job 则持续以旧 revision 恢复，但 Host 中对应 operation 已经取消；更新后仍重复出现 `operation_rejected`，证明 durable job 缺少终态收敛。

## 3. 规范优先级

本设计对下列旧设计的对应部分具有更高优先级：

- `2026-08-13-operation-stage-sealing-and-candidate-posters-design.md` 中的单一 operation 游标与阶段封口实现，升级为持久消息段；“跨 Feature 新建消息”的产品边界保持不变。
- `2026-08-12-wikipedia-first-deterministic-search-design.md` 中的根作品精确标题 seed 门槛，改为 Wikipedia 搜索结果准入；Wikipedia-first 和用户消歧原则保持不变。
- `2026-08-12-wikipedia-first-deterministic-search-design.md` 与 `2026-08-15-runtime-log-and-metadata-confirmation-hardening-design.md` 中的完整 `media_metadata v1` 下游合同，改为最小 `media_metadata v2`。
- `2026-08-15-rename-convergence-without-sync-design.md` 中自动流程止于 rename 的边界继续有效。

旧文档中的发布、字幕、文件写入验证、目录清理和手工 sync 能力不因本设计改变。

## 4. 非目标

- 不引入 AI query 改写、AI 候选排序或 AI 身份确认。
- 不在 query 层枚举冒号、空格、“篇”、季名或其他标题变体。
- 不让 Wikipedia 搜索排名直接成为已确认媒体身份；非精确结果仍需用户选择。
- 不把完整国家、简介、海报、演职员、评分或 Provider evidence 带入 download/rename。
- 不让 Feature 持有或自行管理 Telegram message ID。
- 不恢复 rename 到 sync/Plex 的自动事件或 handoff。
- 不改变现有外挂字幕选择、文件冲突、写后验证和目录清理安全语义。
- 不在 Mac 本地执行 Git、发布、标签或 GitHub 操作。

## 5. Host 消息段模型

### 5.1 数据模型

Host 新增 `operation_message_segments` 持久表。每行表示一个 Feature 拥有的 Telegram 消息生命周期：

| 字段 | 语义 |
|---|---|
| `segment_id` | Host 生成的不可变 ID |
| `operation_id` | 所属 operation |
| `sequence` | operation 内严格递增序号 |
| `owner_plugin_id` | 唯一允许更新该段的 Feature |
| `role` | `identity`、`search`、`download`、`rename`；迁移期间允许只读 `legacy` |
| `generation` | 恢复或替换段时递增，用于 callback 失效 |
| `presentation_kind` | 不可变的 `photo` 或 `text` |
| `state` | `creating`、`open`、`sealing`、`sealed`、`delivery_uncertain`、`failed` |
| `message_id/message_kind` | Telegram 实际送达目标 |
| `business_revision` | 该段已接收的最新业务 revision |
| `rendered_revision` | 已成功渲染到 Telegram 的 revision |
| `projection_hash` | 最新成功渲染内容摘要，用于 no-op 去重 |
| `callback_generation` | 当前有效键盘代次 |
| `delivery_state` | `reserved`、`delivering`、`delivered`、`uncertain` |
| `created_at/updated_at/sealed_at` | 恢复和诊断时间戳 |

`operations` 增加 `active_segment_id`。同一 operation 最多只有一个 `creating/open/sealing` 消息段；同一 owner 最多更新当前开放段。旧 `operations.message_id/message_kind` 在本次迁移中保留为只读兼容字段，但新代码不得再通过它们决定 callback 或渲染目标。

已有非终态 operation 如果仍带旧游标，Host 启动迁移时把它转换为一个 `role=legacy` 的开放段。迁移完成后，所有写入只进入消息段表。

### 5.2 Feature 与 SDK 契约

Feature 继续通过 operation 协议报告业务状态，但报告必须声明：

```json
{
  "segment": {
    "role": "identity",
    "presentation_kind": "photo"
  }
}
```

Host 的行为规则是：

- 当前没有开放段时，为当前 owner 和声明 role 创建消息段。
- 当前开放段 owner、role 和 kind 相同时，更新该段。
- role 或 kind 与当前开放段冲突时，返回 `segment_role_conflict`，不得隐式新发消息。
- owner 与当前开放段不同时，返回 `segment_owner_conflict`；跨 Feature 必须先封口和 handoff。
- Feature 只接收 `segment_id/generation/state` 作为诊断结果，不接收可写 Telegram message ID。

SDK `1.4.0` 增加：

- operation report 的 segment 声明与响应类型；
- `seal_operation_segment(...)`；
- 带最终投影的 `publish_operation_milestone(...)`，内部封口当前段；
- v2 媒体合同 validator 与 v1→v2 converter。

既有 `operation.milestone` 幂等存储继续使用，但 milestone 必须绑定实际 `segment_id/generation`。identity milestone 和 stage milestone 都通过同一消息段渲染器更新并封口，不再有独立 Telegram 投影通道。

### 5.3 单次创建与最新快照渲染

消息段创建遵守以下顺序：

1. 在数据库中预留唯一 `segment_id/generation`，状态为 `creating/reserved`。
2. 在 per-operation 串行队列中把 delivery 标记为 `delivering`。
3. 只允许预留该段的 creator 执行一次 Telegram `sendPhoto` 或 `sendMessage`。
4. 发送期间到达的新 revision 只替换数据库中的最新待渲染快照，不启动第二个 creator。
5. 首次发送返回后，以 `segment_id + owner + generation` CAS 保存 message ID；该 CAS 不要求业务 revision 仍等于发送开始时的 revision。
6. 在同一 message ID 上循环渲染最新快照，直到 `rendered_revision == business_revision`。

这替代现有“首次在飞 render 加最后 pending render 都各自执行”的行为。业务 revision 推进不再导致第二次 Telegram `send`。

`projection_hash` 相同视为 UI no-op：Host 不调用 Telegram，search 也不得因为海报补全结果未变化而产生新的候选业务 revision。

### 5.4 presentation kind 不可变

消息段创建后不能在 text 和 photo 之间转换：

- identity 段从开始就是 Host 本地生成占位图的 photo 消息；caption 初始为“正在识别媒体…”。
- 候选海报宫格通过 `editMessageMedia` 替换占位图，按钮和文字通过 caption 更新。
- 海报生成或媒体编辑失败时保留现有图片，只更新精简 caption 和按钮；不得另发 text fallback。
- search、download 和 rename 执行段使用 text 消息，只做文本与键盘编辑。

占位图必须由 Host 本地确定性生成，不依赖远程 Provider。Telegram caption 必须在 Host 边界内压缩到合法长度。

## 6. Callback 与首次点击反馈

Feature callback 必须同时满足：

- operation 为当前活动 operation；
- callback message ID 等于当前 segment message ID；
- segment owner 等于目标 Feature；
- callback 中的 segment generation 与持久记录一致；
- callback token 属于当前 keyboard generation。

候选或片源点击通过 gate 后，Host 必须在调用 Feature RPC 前完成：

1. 递增 `callback_generation`，使原键盘立即失效；
2. 原位移除键盘；
3. 把 caption/text 更新为“正在确认媒体身份…”或“正在处理所选片源…”；
4. 持久化 busy 状态和所选 callback token；
5. 再调用可能耗时的 search hydration 或下游 capability。

同一 token 的重放返回当前 busy/result 状态，不重复调用 Feature。不同 token 在 busy 状态下返回当前任务状态。正常 Telegram 网络条件下，点击后一秒内必须出现持久可见反馈。

operation 级 cancel/exit 仍可由 Host 处理，但终态投影也必须进入当前消息段串行队列，不能绕过段 owner/generation 后直接写 Telegram。

## 7. 消息拓扑与 Feature handoff

### 7.1 Search identity 段

```text
photo A
正在识别 -> 作品候选 -> 正在确认身份 -> 最终身份 -> sealed
```

- 候选缺失海报时，在候选首次显示前执行有总时限的并行补全。
- 补全结束或超时后只报告一次候选；后续无变化结果不得刷新。
- 用户选择后立即进入 busy 投影，再执行冻结候选的精确 hydration。
- 最终 identity milestone 覆盖当前 photo caption、移除键盘并封口。

### 7.2 Search/Prowlarr 段

```text
text B
Prowlarr 进度 -> 片源列表 -> 已选片源/解析 -> Search 完成 -> sealed
```

identity 段持久封口后，第一条 Prowlarr 可见状态创建 text B。所有 indexer 进度、片源排名与选择都原位更新 text B。

Search 在交给 download 前执行：

1. `prepare_handoff(download)` 确认目标 Feature 和 capability 可用，并持久化带 idempotency key 与 payload reference 的 handoff intent，但不改变 owner；
2. 将 text B 封口为 Search 完成摘要；
3. Host 根据 durable handoff intent 提交 owner 变化并调度 download capability；
4. download 接受相同 handoff ID 后开始报告并创建 text C。

prepare 或 seal 失败时，text B 原位终止或保留可重试状态，不预先创建 download 消息。seal 之后的 dispatch 失败由 Host 重放 durable handoff intent；text B 保持封口，不重新开放，也不回退 owner。

### 7.3 Download 段

```text
text C
提交离线任务 -> Provider 进度 -> 文件树确认 -> Download 完成/取消 -> sealed
```

Download 取得 owner 后由首个非静默 report 创建 text C。下载完成且 `download.completed` 输入已经持久化后，先封口 text C，再 handoff 给 rename。用户取消时在 text C 原位写入取消终态，不产生 rename 段；保留的部分下载内容不自动进入 rename。

### 7.4 Rename 段

```text
text D
读取文件 -> 校验 v2 -> 规划命名 -> 文件变更与验证 -> 整理终态
```

Rename 取得 owner 后创建 text D。自动流程在 text D 的 completed、partial、failed 或 cancelled 终态结束，不自动接力 sync/Plex。

### 7.5 Handoff 不变量

- 只有 `sealed` 段允许 owner handoff。
- owner handoff 清空 `active_segment_id`，但保留历史 segment 行和 Telegram 内容。
- 新 owner 不能继承或恢复上一个 Feature 的 message ID。
- foreground callback 返回不能调用无条件 `set_message_id`；所有消息写入只通过 segment renderer。
- handoff、segment seal 和 operation owner 变化必须在一个 Host 事务或可恢复的两阶段状态机内完成。

Host 使用 `operation_handoffs` 持久化两阶段接力，至少保存 `handoff_id`、operation、from/to owner、idempotency key、payload reference，以及 `prepared/source_sealed/owner_committed/delivered/failed` 状态。source seal 前的确定性失败由 source 消息展示；source seal 后的瞬时 dispatch 失败只重放相同 handoff，不创建第二个下游任务。downstream 必须在确认相同 handoff ID 后才创建自己的消息段。

## 8. Wikipedia 搜索结果准入

### 8.1 Query 责任

Plain-title query 只做传输级规范化：

- 去除首尾空白；
- 把连续空白合并为一个普通空格；
- 保留原始词序、标点和用户输入内容；
- 不补“篇”、冒号、年份、季名或其他字符；
- 不生成空格版、无空格版或语义改写版 query。

规范化后的完整文本原样提交中文 Wikipedia 搜索。中文搜索没有结构有效候选时，英文 Wikipedia 使用同一文本做固定 fallback。

### 8.2 搜索与结构过滤

每种语言最多读取 MediaWiki 搜索前 10 项，保留：

- MediaWiki rank；
- page ID；
- 命中标题、重定向和 snippet 信息；
- Wikidata QID；
- `pageprops.disambiguation`；
- 用户明确提供的年份或类型约束。

search 批量读取结果页面和 Wikidata `P31`，只排除确定不是根电影/剧集的实体，例如人物、小说、游戏、列表、季页面、单集页面和 specials。用户明确指定的年份或媒体类型只用于排除确定冲突。

搜索结果不再要求 query 与标题、别名或重定向精确相等。Wikipedia 已经返回且结构有效的结果可以进入候选集。精确标题、精确别名、精确重定向、年份/类型一致只作为排序加分，MediaWiki rank 继续作为主要稳定顺序。

disambiguation 页面本身不是候选。search 可以在一次有界请求中读取其文章命名空间链接，并对链接页面执行相同结构过滤。

过滤、去重和排序后最多展示 5 个候选。`死神 千年血战篇` 只要被 Wikipedia 对 `死神 千年血战` 返回、结构为剧集且没有显式约束冲突，就必须进入候选。

### 8.3 自动确认边界

- 直接 Provider 链接解析出稳定实体后可以直接冻结身份。
- Plain-title query 只有在唯一候选且 query 与标题、已验证别名或重定向精确规范化相等时才允许自动冻结。
- 部分匹配、snippet 命中或多个结构有效结果必须由用户选择。
- MediaWiki rank 只能影响候选顺序，不能代替用户确认。

### 8.4 Search 内部 resolution record

候选搜索、Provider 绑定和精确 hydration 使用 search 自有的持久 resolution record。它可以保存：

- 原始 query、MediaWiki 结果和排序证据；
- 冻结候选、source links 和 Provider 原始 facts；
- 候选 UI 所需的国家、摘要、海报和类型证据；
- 已验证 Provider ID crosswalk；
- 创建、过期、已选择候选和最终 v2 结果。

record 默认 TTL 为 24 小时，最多保留 256 条，并沿用确认幂等重放。完整 evidence 只属于 search 的诊断与确认边界，不进入 download/rename handoff。

## 9. `media_metadata v2`

### 9.1 合同形状

新任务使用以下唯一媒体业务合同：

```json
{
  "schema_version": 2,
  "metadata_id": "media-v2:<sha256>",
  "confirmed": true,
  "identity": {
    "primary_ref": {
      "provider": "wikidata",
      "id": "Q112631839"
    },
    "provider_refs": {
      "wikidata": "Q112631839"
    },
    "media_type": "series",
    "title_zh": "死神 千年血战篇",
    "title_original": "BLEACH 千年血戦篇",
    "year": 2022
  },
  "scope": {
    "kind": "whole_series",
    "season_number": null,
    "episode_number": null
  },
  "placement": {
    "category_kind": "animated_series"
  }
}
```

顶层、`identity`、`primary_ref`、`scope` 和 `placement` 使用严格键集合。除 `provider_refs` 的受控 Provider key 外，validator 拒绝未知字段，防止 v2 再次膨胀成完整资料容器。

### 9.2 Identity

`primary_ref` 是冻结身份锚点：

- provider 与 ID 均为非空字符串；
- provider 必须存在于 `provider_refs`，值必须相同；
- 后续 Provider 不得替换它；
- Provider 冲突返回 `provider_identity_conflict`，不能静默覆盖。

`provider_refs` 保存已经验证属于同一作品的稳定外部 ID，用于精确反查、恢复、去重和受约束文件映射。允许的 key 为：

- `wikidata`
- `zhwiki_page_id`
- `enwiki_page_id`
- `douban_subject`
- `tmdb_movie`
- `tmdb_tv`
- `tvdb_movie`
- `tvdb_series`
- `anilist`

每个值必须是 Provider 返回的稳定非空 ID。不得从标题、URL 路径或其他 Provider ID 猜测。存在 Provider ID 时，后续反查必须优先使用该 ID，不得重新做模糊标题搜索。

`media_type` 只允许 `movie` 或 `series`。`title_zh` 与 `title_original` 都是经过合同名称清理的字符串，至少一个非空；两者独立，不能用英文回填中文字段。`year` 为正整数或 `null`。

### 9.3 Scope

`scope.kind` 和季集坐标遵守：

| kind | media_type | season_number | episode_number |
|---|---|---:|---:|
| `movie` | `movie` | `null` | `null` |
| `whole_series` | `series` | `null` | `null` |
| `season` | `series` | `>=1` | `null` |
| `episode` | `series` | `>=1` | `>=1` |

scope 表示用户已经确认的下载范围，不包含 aired inventory、全季 episode 列表、Provider 决策记录或 Prowlarr query。

### 9.4 Placement

`placement.category_kind` 只允许现有四个值：

- `live_action_series`
- `live_action_movie`
- `animated_movie`
- `animated_series`

library type 由 category kind 确定，不重复保存。真实目录路径、显示名和 Plex library ID 继续从运行时 `category_folder` 配置解析，不写入媒体合同。

### 9.5 Metadata ID

`metadata_id` 使用规范 JSON 的 SHA-256：

```text
media-v2:sha256({schema_version, primary_ref, media_type, scope})
```

标题、年份和后续补充的 `provider_refs` 不参与 ID，因此增加新的已验证 Provider ID 不改变同一已确认身份与范围的幂等键。primary ref、媒体类型或 scope 变化必须产生新 ID。

### 9.6 明确移除的字段

v2 不包含：

- countries、genres、language、summary；
- cast、crew、companies、studios、networks；
- certifications、ratings、runtime、artwork；
- aliases 和未验证 external IDs；
- Provider 原始 facts、字段来源、状态、warnings 和 decision；
- TVDB/TMDB season 或 episode inventory；
- source/final path、文件执行结果和清理结果；
- Prowlarr query、片源、indexer 和下载状态。

`Q17`、`Q148` 等原始国家 QID 不需要在下游归一化，因为这些字段不再进入 v2。

## 10. Search、Download 与 Rename 的数据责任

### 10.1 Search 输出

Search 在身份、scope 和分类确认后构造 v2。Prowlarr 使用 search 内部冻结候选的标题、别名和 Provider facts生成查询，但查询与片源属于执行参数。

Search 调用 download 时提交：

```text
operation identity/revision/idempotency key
selected release {release_id, title, download_url, indexer, size}
selected save path
media_metadata v2
```

新链路不再提交独立 `naming_metadata`。片源 URL 继续遵守现有敏感日志边界。

### 10.2 Download

Download 把 v2 作为不透明、不可修改的业务值持久化。在 `download.completed` 中原样传递相同 JSON；任何字段变化都视为合同破坏。

下载任务、Provider 进度、文件树和部分内容保留状态属于 download job，不写回 v2。Download 取消时不发布 `download.completed`。

### 10.3 Rename

Rename 只使用 v2、下载文件树、目标分类配置和必要的文件级推断：

- 电影：根据已确认标题和下载文件树选择主视频，目录为中文标题加原始标题，文件基础名优先原始标题。
- 全剧：从文件树解析全部常规 `SxxEyy` 坐标；所有媒体文件必须得到唯一合法坐标。
- 指定季：所有媒体文件必须属于 `scope.season_number`。
- 指定单集：媒体文件必须精确匹配 scope 季集坐标。
- season zero、special、OVA/ONA、无法解析或范围外文件继续失败关闭，除非现有独立字幕/特殊内容合同明确允许。

Rename 不需要远端完整 episode inventory 才能接受实际下载文件。观察到的文件坐标是文件映射范围；scope 是不可突破的上界。受约束 AI 文件映射如被启用，只能读取 v2 锁定身份、Provider refs、scope 和文件树，不能改变标题、分类或范围。

Rename 的 source/final path、冲突、写后验证和目录清理摘要写入独立 `organization_result`，不得修改或扩充 v2。

## 11. v1 兼容与迁移

### 11.1 新旧生产边界

- search `1.12.0` 只生产 v2。
- download `1.1.0` 接受 v2，并为仍在运行的旧任务继续原样传递 v1。
- rename `1.6.0` 原生消费 v2；v1 只在恢复/输入边界转换一次。
- 新代码不得生产新的 `naming_metadata`；旧任务携带的 naming metadata 只供 converter 补足必需标题或分类。

### 11.2 v1→v2 converter

converter 只提取：

- identity 或 relation target 中的确认标题、根作品类型和年份；
- 已验证 external IDs，映射为受控 `provider_refs`；
- 冻结 anchor，映射为 `primary_ref`；
- retrieval/placement/decision 中已经确认的 scope；
- category kind。

已有冻结 anchor 时必须使用该 anchor 作为 `primary_ref`。历史 v1 缺少显式 anchor、但存在已经写入确认合同的多个 verified external IDs 时，converter 按 `wikidata`、TVDB、TMDB、Douban、中文 Wikipedia page ID、英文 Wikipedia page ID、AniList 的固定顺序选择 primary ref；其余已验证 ID 保留为 provider refs。没有任何稳定 verified ref 时转换失败，不能用标题生成伪 ID。

converter 不复制 countries、evidence、warnings、inventory、artwork、人物、公司或 resolved file paths。转换结果必须通过严格 v2 validator，再原子写回 rename durable job。成功转换后重启只读取已保存的 v2，不重复转换。

缺失 primary identity、标题、媒体类型、合法 scope 或 category kind 时返回：

```text
legacy_metadata_incomplete
```

文件保持原位，job 进入明确失败状态，不回退到猜测式自动命名。

## 12. Rename Durable Job 收敛

Rename 启动时先读取 Host operation snapshot，再决定是否恢复 durable job：

| Host operation | Durable job 行为 |
|---|---|
| `cancelled` | 持久化 `cancelled_external`，停止恢复 |
| `completed` | 持久化 `completed_external`，停止恢复 |
| `failed` | 持久化 `failed_external`，停止恢复 |
| 不存在 | 持久化 `orphaned`，文件保持原位 |
| owner=rename 且非终态 | 对齐 Host revision，继续幂等恢复 |
| 合法 handoff 尚未完成 | 持久化等待状态，不提交旧 revision |
| owner 为其他 Feature 且无合法 handoff | 持久化 `ownership_conflict`，停止自动恢复 |

只有 owner 和 operation 状态允许恢复后，才执行 v1→v2 converter 或恢复文件处理。Host 拒绝 operation report 时，Rename 必须把稳定 reason code 和 Host snapshot 写入 job；确定性拒绝不得只记录 warning 后保持原 resumable 状态。

用户取消 download 后保留的部分内容不自动创建 rename job。用户之后可以通过明确的手工 Rename 流程重新扫描和整理。

## 13. Provider 错误与日志

### 13.1 Search 错误分类

Wikipedia/Wikidata 错误使用稳定 reason code：

- `wiki_rate_limited`
- `wiki_unavailable`
- `wikidata_rate_limited`
- `wikidata_unavailable`
- `wiki_result_invalid`
- `no_supported_work`
- `provider_identity_conflict`

Wikidata 瞬时失败允许一次遵守 `Retry-After` 和现有 circuit breaker 的有界重试。仍失败时保留可重试状态，不返回通用 `internal_error`，也不使用缺少结构证据的结果伪造媒体身份。

Prowlarr 单 indexer HTTP 400、超时或格式错误只隔离该 indexer。search 继续其他 indexer，并在同一消息中展示已检查、成功和失败数量。最终有合格片源时允许继续；全部失败时返回稳定汇总原因。

### 13.2 Operation 与 Segment 可观测性

每条 operation/Telegram 诊断事件必须包含当前真实：

- `operation_id`
- `owner_plugin_id`
- `business_revision`
- `segment_id/role/generation/state`
- `message_id/message_kind`，如已知
- `delivery_state`
- `callback_generation`，如适用

operation ID 必须来自当前 dispatch/report 的权威字段，不能继承上一条诊断上下文。Telegram side effect 前后分别记录 `delivery_started` 和 `delivery_completed/failed`；segment CAS 结果必须写日志，不能再依靠时间线推断。

日志继续禁止输出凭据、完整 magnet、download URL、请求 header 或未摘要的大型 Provider response。

## 14. 失败与恢复语义

### 14.1 Telegram 创建结果不确定

Telegram Bot API 不提供业务幂等键。Host 如果在 Telegram 已接受 send、但本地尚未保存 message ID 的窗口中崩溃，无法严格恢复物理 exactly-once。

该窗口采用：

1. send 前已持久化 `delivery_state=delivering`；
2. 重启发现无 message ID 的 delivering 段时标记 `delivery_uncertain`；
3. 不自动重发该段；
4. operation 恢复或用户重新进入时，先递增 generation 并创建恢复段；
5. 旧段即使实际送达，其 callback 因 generation 过期而无效。

硬性保证是任何时候最多只有一条有效交互消息。正常并发 revision、Provider 进度和 callback 不得产生重复可见消息；只有 Telegram send 响应丢失加 Host 进程中断这一不可判定窗口允许留下无效历史消息。

已知 message ID 的 edit 响应丢失可以安全重试同一目标，不得新发消息。

### 14.2 Seal 与 Handoff 失败

- 最终投影 edit 失败：segment 保持 `open` 或可重试 `sealing`，不 handoff。
- seal 数据库提交失败：使用相同 segment/milestone ID 重试，不新建消息。
- downstream 不可用：当前 owner 的段原位显示失败或重试，owner 不变化。
- owner handoff 已提交但 downstream 尚未报告：Host 保持无开放段；downstream 重放首个 report 时创建同一个 owner 的新段。
- cancel 与完成竞争：Host 以 operation 终态 CAS 决定唯一终态，失败一方只能读取结果，不能继续渲染。

## 15. 验证与验收

### 15.1 Host 消息段

- 阻塞首次 `sendPhoto`，期间连续接收 100 个候选 revision；断言只调用一次 send，并在返回后把最新投影编辑到同一 message ID。
- 阻塞首次 Prowlarr `sendMessage`，期间推进 5/25、11/25、25/25；断言只有一个 Prowlarr message ID。
- 候选 photo、确认 busy、identity milestone 共享一个 segment/message ID。
- foreground callback 与 background report 竞争时，不能发生无条件 cursor 回写或跨 owner message 复用。
- projection hash 相同不调用 Telegram；无变化海报补全不产生新候选 revision。
- 旧 segment 的 message ID、generation 或 callback token 均被 gate 拒绝。
- 慢 hydration fixture 下，首次点击后一秒内移除键盘并持久显示处理中；相同 token 重放不重复调用 Feature。
- identity、Search、Download、Rename 的 seal/handoff 顺序严格成立。
- Host reload 后开放、封口、delivery uncertain 和 callback generation 状态保持一致。

### 15.2 Wikipedia 候选

- `死神 千年血战` 原样进入 Wikipedia adapter，不生成补词 query。
- Wiki 返回 `死神 千年血战篇` 且结构为剧集时进入候选，即使规范化标题不完全相等。
- `死神千年血战` 使用相同结果准入逻辑。
- 精确标题只影响排序/唯一自动确认，不再作为候选 seed 硬门槛。
- 人物、小说、游戏、列表、季页和单集页继续被结构过滤。
- 年份或媒体类型明确冲突继续排除。
- 部分匹配唯一结果仍需用户选择；直接稳定链接可以直接确认。

### 15.3 `media_metadata v2`

- movie、whole_series、season、episode 四类合法 fixture 通过严格校验。
- primary ref 必须存在于 provider refs；Provider 冲突失败关闭。
- 后补已验证 provider ref 不改变 metadata ID。
- v2 不含国家、简介、海报、人物、inventory、evidence、warnings、Prowlarr 或文件路径。
- 未知顶层或 nested 字段被 validator 拒绝。
- Search 到 Download、Download job 到 `download.completed` 的 v2 JSON 字节语义不变。
- 新链路不存在 `naming_metadata`。

### 15.4 Rename

- 电影按确认标题生成目录与主文件名。
- 全剧只接受文件树中全部可唯一解析的常规季集文件。
- 指定季拒绝其他季；指定集拒绝其他坐标。
- 无法解析、重复坐标、范围外文件和 specials 保持原位并产生明确结果。
- 外挂字幕、冲突、写后验证和目录清理现有回归全部通过。
- organization result 独立保存，v2 不被写入 source/final path。
- 可恢复 v1 只转换并持久化一次；不完整 v1 返回 `legacy_metadata_incomplete`。
- Host 已取消的旧 job 第一次启动即终结，后续重启不再发 operation report。

### 15.5 全链路门禁

Telegram 顺序必须是：

```text
1. Search identity photo
2. Search/Prowlarr text
3. Download text
4. Rename text
```

全链路覆盖成功、无候选、Provider 限流、无片源、单 indexer 失败、用户退出、下载取消、rename 部分完成、Host 重启和 Feature 重启。最终运行 Host 全量测试和 download、search、rename、sync、caption 五个 Feature 全量测试，并构建三个变更 Feature 的 `.tpx` 包执行完整性检查。

## 16. 实现分解边界

本设计属于一个版本列车，但后续实现计划拆成三个有顺序、可独立验收的工作流：

1. **Host/SDK 消息段基础：** 数据迁移、串行 renderer、callback generation、milestone 合流、durable handoff 与 Host API 1.7。
2. **Search 候选与 v2 生产：** Wikipedia 结果准入、候选单次投影、即时 busy 状态、resolution record、Provider 错误分类和 `media_metadata v2` producer。
3. **Download/Rename 消费与恢复：** v2 不透明传递、移除新 `naming_metadata`、文件树范围验证、organization result、v1 converter 和 durable job 收敛。

工作流 2、3 可以分别开发，但集成验收必须建立在工作流 1 的 Host API 1.7 上。版本提升和全链路测试在三个工作流全部通过后统一完成，不允许先发布只理解一半消息段或媒体合同的组合。

## 17. 发布身份与兼容要求

| 组件 | 当前 | 目标 | 要求 |
|---|---:|---:|---|
| Host | `v3.5.6-host` | `v3.6.0-host` | 提供消息段存储、渲染与 Host API 1.7 |
| Host API | `1.6` | `1.7` | 兼容旧 operation report，新增 segment 契约 |
| Plugin SDK | `1.3.2` | `1.4.0` | v2 validator、converter、segment RPC |
| search | `1.11.7` | `1.12.0` | 要求 Host API `>=1.7,<2.0`、SDK `1.4.0` |
| download | `1.0.20` | `1.1.0` | 要求 Host API `>=1.7,<2.0`、SDK `1.4.0` |
| rename | `1.5.11` | `1.6.0` | 要求 Host API `>=1.7,<2.0`、SDK `1.4.0` |
| sync | 不变 | 不变 | 不恢复自动订阅 |
| caption | 不变 | 不变 | 无生产改动 |

SDK 包版本保持 1.x，因为它新增 v2 并保留 v1 读取与转换 API；媒体合同自身通过 `schema_version=2` 表达破坏性数据形状变化。

升级顺序为 Host/SDK、search、download、rename。新 Feature manifest 的 Host API floor 防止安装在不理解消息段的旧 Host 上。

## 18. 本地交付边界

设计、实现和测试只在 `/Users/young/Documents/telepiplex` 完成。Mac 本地不得执行 Git、worktree、发布、标签或 GitHub 操作。

实现完成后必须列出变更文件、实际测试与结果，并等待 Syncthing 显示 `Up to Date / 最新`，再由用户在 Unraid `/mnt/user/archives/life hacker/telepiplex` 手工检查和发布。
