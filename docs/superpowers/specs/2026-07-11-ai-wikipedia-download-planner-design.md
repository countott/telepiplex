# AI + Wikipedia 全量搜索与下载方案设计

日期：2026-07-11
状态：已确认设计，待实施计划

## 1. 背景

“想见你”同时存在 2019 年剧集和 2022 年同名续篇电影。中文标题完全相同，英文标题仅多出 `The Movie`，而电影又与剧集共享人物和故事线。现有搜索管线能够在用户确认后形成正确的电影查询，但仍暴露出三个边界问题：

1. 豆瓣和 TVDB 的标题、年份、媒体类型适合校对条目，却不足以稳定表达续作、前传、OVA、bonus、外传和延伸电影等作品关系。
2. 搜索确认结果可能以 `source=confirmed` 进入整理层，而下游仍以来源字符串白名单判断元数据是否可用，导致字段完整的计划被拒绝。
3. 当前保存目录选择与已经确认的媒体类型、主线剧集及 TVDB `Season 00` 坐标没有形成同一份不可变计划。

本设计将 AI 从失败兜底升级为每次搜索都必须经过的语义编排器。Wikipedia、豆瓣和 TVDB 作为可选信息提供者帮助 AI 减少误判；它们单独或同时不可用时不阻断搜索。AI 不可用时搜索必须停止。

## 2. 目标

- 每次搜索固定执行两段式 AI 编排。
- 使用 Wikipedia 描述识别强作品关系，包括续作、前传、外传、OVA、bonus 和延伸电影。
- 使用豆瓣与 TVDB 补充标题、年份、媒体类型、外部 ID 和官方季集坐标。
- 默认将 TVDB 已收录为 `S00E…` 的延伸电影或特别内容放入剧集库 `Season 00`，即使它也有独立电影条目。
- 当 TVDB 没有对应 Special、但 Wikipedia 存在可定位的强关联作品条目时，生成临时关联特别篇，并从 `S00E100` 开始分配临时编号。
- 在 Prowlarr 搜索前向用户展示完整下载方案，只保留一次确认。
- 让后续搜索、下载和整理模块消费同一份已确认计划，避免各层重复猜测。
- 支持一个资源包同时包含主线剧集、OVA、bonus、延伸电影及非叙事花絮。

## 3. 非目标

- 不在本次搜索模块中实现 Plex 扫描、Plex 元数据覆盖或 Plex 重试。
- 不建立 `/config` 持久化元数据库，不保存长期自建特别篇注册表。
- 不让搜索模块直接依赖未来的 Plex module。
- 不把 Wikipedia、豆瓣或 TVDB 变成硬依赖。
- 不允许 Prowlarr、下载模块或整理模块在用户确认后静默改变作品关系、目标媒体库或季集方案。
- 不改动与本设计无关的 Prowlarr 搜索计时器工作。

## 4. 总体架构

```text
用户输入
  -> AI 第一次调用：生成检索假设
  -> Wikipedia / 豆瓣 / TVDB 并行补充信息（全部软失败）
  -> AI 第二次调用：生成结构化 DraftDownloadPlan
  -> 确定性编号分配器：生成最终 DownloadPlan
  -> 用户确认一次
  -> Prowlarr 搜索与片源选择
  -> 下载模块携带已确认 DownloadPlan
  -> 下载后受约束的文件映射
  -> 重命名 module 执行目录与文件操作
  -> 独立 Plex module 可消费结果事件，但不属于本次范围
```

两次搜索阶段 AI 调用都是硬依赖。下载完成后，如果实际文件树包含多个需要语义匹配的文件，可以调用现有的下载后 AI 映射能力；该调用只能把文件绑定到已经确认的作品范围，不能重新决定主线剧集或媒体库。

## 5. 模块职责

### 5.1 AI 检索假设生成器

第一次 AI 调用接收用户原始输入，输出：

- 标准标题、别名和可能年份。
- 电影、剧集、OVA、bonus、特别篇或延伸电影等候选身份。
- 可能关联的主线剧集。
- 用户明确表达的全集、整季、单集或单部作品范围。
- Wikipedia、豆瓣和 TVDB 各自应查询的关键词。
- 仍需外部信息确认的关系问题。

第一次调用不生成最终 Prowlarr 查询、不冻结目标目录，也不分配临时 `S00E100+` 编号。

### 5.2 信息源适配器

Wikipedia、豆瓣和 TVDB 按第一次 AI 输出并行查询。所有适配器返回统一结构：

```json
{
  "source": "wikipedia",
  "status": "ok",
  "facts": [],
  "source_urls": [],
  "error": ""
}
```

`status` 至少支持：

- `ok`
- `not_found`
- `server_down`
- `timeout`
- `invalid_response`
- `disabled`

任何信息源失败都进入第二次 AI 调用的上下文，不提前终止搜索。

### 5.3 AI 下载规划器

第二次 AI 调用接收：

- 用户原始输入。
- 第一次 AI 的检索假设。
- 所有可用来源事实、来源 URL 和来源状态。
- 当前 TVDB Specials 及其排序方式（如果可用）。
- 当前目标 `Season 00` 中可观察到的已占用编号（如果可用）。
- 当前进程内尚未完成任务预约的 `S00E100+` 编号。

第二次 AI 输出唯一结构化 `DraftDownloadPlan`。它必须成功且通过 JSON schema 校验，否则停止搜索。对于 `temporary_related_special`，AI 只声明需要临时编号，`episode_number` 保持为空；AI 不得自行挑选 `S00E100+`。

### 5.4 确定性编号分配器

编号分配器在第二次 AI 调用成功后、用户确认前运行。只有 `mapping_kind=temporary_related_special` 才进入该步骤。分配器读取可用的 TVDB Specials、目标 `Season 00` 已有文件和当前进程内预约编号，填充第一个空闲的 `S00E100+`，生成最终 `DownloadPlan`。

### 5.5 确认层

用户确认的是完整下载方案，而不是单独确认一个电影或剧集候选。确认内容至少包括：

- 目标作品及年份。
- 内容身份和主线剧集关系。
- 关系来源。
- 电影库或剧集库归属。
- TVDB 官方 `S00E…`、AI 推断 `S00E…` 或临时 `S00E100+`。
- Prowlarr 查询词。
- 证据状态和风险说明。

确认后冻结作品关系、目标媒体库和季集方案。后续执行层只能实现该方案。

### 5.6 下载与整理层

`DownloadPlan` 随 `DownloadRequest` 和 `DownloadCompletedEvent` 传递。整理层按字段完整性和计划状态判断是否执行，不再用 `source` 字符串白名单决定元数据是否可信。

下载后 AI 映射器可以读取实际文件树，完成主线、OVA、bonus 和延伸电影的逐文件绑定。无法可靠绑定的文件进入未整理目录，不拖累已经成功绑定的文件。

### 5.7 Plex module 边界

搜索模块不调用 Plex。未来独立 Plex module 可以消费整理结果事件，实现扫描、元数据覆盖或重试。本设计只传递临时关联元数据，不规定 Plex module 的内部实现。

## 6. DownloadPlan 数据契约

以下示例是确定性编号分配器处理后的最终计划：

```json
{
  "plan_id": "short-stable-task-id",
  "display_title": "想见你",
  "english_title": "Someday or One Day The Movie",
  "year": "2022",
  "content_identity": "extension_movie",
  "relation": {
    "type": "sequel",
    "target_series_title": "Someday or One Day",
    "target_series_year": "2019",
    "tvdb_series_id": "",
    "source": "wikipedia"
  },
  "plex_placement": {
    "library_type": "series",
    "season_number": 0,
    "episode_number": 100,
    "mapping_kind": "temporary_related_special",
    "mapping_source": "local_allocator"
  },
  "source_entry": {
    "title": "想见你 (电影)",
    "url": "https://zh.wikipedia.org/wiki/想見你_(電影)",
    "provider": "wikipedia",
    "availability": "ok",
    "verification": "verified"
  },
  "prowlarr_queries": [
    "Someday or One Day The Movie 2022"
  ],
  "evidence_status": "partially_verified",
  "warnings": [],
  "items": []
}
```

`content_identity` 至少支持：

- `movie`
- `series`
- `main_episode`
- `ova`
- `narrative_bonus`
- `non_narrative_extra`
- `special`
- `prequel_movie`
- `sequel_movie`
- `extension_movie`
- `spin_off`

`mapping_kind` 至少支持：

- `tvdb_official`
- `ai_inferred_tvdb`
- `temporary_related_special`
- `plex_local_extra`
- `movie_library`

## 7. 作品关系与媒体库归属规则

优先级从高到低：

1. TVDB 返回明确 series ID、Specials 集号或 episode ID 时，采用对应官方 `S00E…`。
2. 同一内容同时是独立电影、又被 TVDB 收录为某剧集 Special 时，默认按剧集身份进入 `Season 00`；独立电影身份仅作为搜索关键词和关系说明保留。
3. TVDB 不可用，但 AI 能依据可定位作品条目推断真实 TVDB `S00E…` 时，允许使用具体编号，并必须显示“仅 AI 推断，未实时通过 TVDB 校验”。
4. TVDB 没有对应 Special，但 Wikipedia 明确说明作品是某剧集的续作、前传、外传、衍生或同一故事线时，建立临时关联特别篇并进入该剧集 `Season 00`。
5. OVA、叙事性 bonus、特别篇、前传、后传和延伸电影优先尝试映射到 `Season 00`。
6. 花絮、采访、预告片和制作特辑等非叙事内容，只有 TVDB 明确收录时才使用 `S00E…`；否则标记为 Plex local extra。
7. 只有不存在 TVDB 关系、也不存在 Wikipedia 强叙事关系的独立作品才进入电影库。

仅共享演员、导演、制作公司、题材或相似标题不构成强关系。AI 必须能说明续作、前传、外传、衍生或同一故事线中的至少一种明确关系。

## 8. 可定位来源条目约束

临时关联特别篇不能是 AI 凭空生成的孤立条目，必须指向可找到的对应作品条目。`source_entry` 必须至少包含：

- 页面或条目标题。
- 可解析 URL 或稳定外部定位信息。
- provider。
- 当前可用性。
- 验证状态。

Wikipedia API 宕机不表示条目不存在。AI 可以提供它所知道的页面标题或 URL，但必须标记：

```json
{
  "availability": "server_down",
  "verification": "ai_supplied_unverified"
}
```

如果 AI 连可查找的对应条目都无法给出，则不得创建临时关联特别篇。

## 9. 临时 S00E100+ 分配

- TVDB 官方条目保留其原始编号。
- 临时关联特别篇从 `S00E100` 开始。
- 分配前读取可用的 TVDB Specials、目标 `Season 00` 已有文件及当前进程内预约编号。
- 选择第一个未占用编号，例如 `S00E100`、`S00E101`。
- 预约仅存在于当前进程和当前任务生命周期内，不写入 `/config`。
- 同一进程内并发任务不得获得相同编号。
- 容器重启后预约丢失；恢复的下载必须重新执行 AI 识别和编号检查，无法恢复时进入未整理。

计划确认后不得静默改变编号。如果执行前发现编号已被其他任务占用，停止该文件的自动整理并向用户报告冲突，而不是自动跳到另一个未经确认的编号。

## 10. 多文件资源包

`DownloadPlan.items` 用于表示预期的逐文件角色；实际文件名只有在下载后才能完整获得，因此下载后映射器执行受约束的补全：

```json
{
  "items": [
    {
      "source_match": "*S01E01*",
      "content_role": "main_episode",
      "season_number": 1,
      "episode_number": 1
    },
    {
      "source_match": "*OVA*",
      "content_role": "ova",
      "season_number": 0,
      "episode_number": 3
    },
    {
      "source_match": "*The Movie*",
      "content_role": "extension_movie",
      "season_number": 0,
      "episode_number": 100,
      "mapping_kind": "temporary_related_special"
    }
  ]
}
```

允许一个资源包拆分到：

- 主线文件：`Season 01/02…`
- OVA、官方 Special、强关联延伸电影：`Season 00`
- 非叙事花絮：Plex local extras
- 无法匹配的文件：未整理目录

下载后映射器不得改变已确认的主线剧集或把剧集方案改为电影库方案。

## 11. Prompt 契约

### 11.1 第一次 AI Prompt

硬性要求：

- 只输出 JSON。
- 区分用户明确意图与模型推断。
- 不生成最终 Prowlarr 查询。
- 不冻结最终季集编号。
- 为 Wikipedia、豆瓣和 TVDB 分别生成查询候选。
- 保留同名电影与剧集歧义，直到第二次 AI 融合证据。

### 11.2 第二次 AI Prompt

硬性要求：

- 只输出符合 `DownloadPlan` schema 的 JSON。
- 所有关键字段逐字段记录证据来源。
- 允许所有外部信息源处于 `server_down`。
- AI 推断的 TVDB 编号必须生成显著警告。
- 临时关联特别篇必须包含可定位 `source_entry`。
- 对临时关联特别篇输出 `episode_number=null`；由确定性分配器填充最终 `S00E100+`。
- 不能把弱关系当成强叙事关系。

## 12. 用户确认消息

官方或已验证方案示例：

```text
📋 下载方案

目标：想见你 / Someday or One Day The Movie (2022)
内容身份：2019 剧集的续篇电影
关系依据：Wikipedia
Plex 归属：想见你 / Season 00
集号：S00E100
元数据：临时关联特别篇
来源条目：想见你 (电影)
搜索词：Someday or One Day The Movie 2022

⚠️ TVDB 未收录对应 Special。
本任务将依据可定位的 Wikipedia 条目临时归入主线剧集。

[确认并搜索] [取消]
```

AI 推断 TVDB 编号时必须显示：

```text
集号：S00E05
依据：仅 AI 推断
⚠️ 未实时通过 TVDB 校验，可能与 TVDB 当前排序不一致。
```

用户只确认一次。Prowlarr 结果选择不重新询问作品关系和媒体库归属。

## 13. 错误处理

- 第一次 AI 调用失败：停止搜索并说明 AI 不可用。
- 第二次 AI 调用失败或 schema 校验失败：停止搜索。
- Wikipedia、豆瓣或 TVDB 失败：记录状态并继续进入第二次 AI。
- 找不到可定位来源条目：不得创建临时关联特别篇。
- Prowlarr 失败：保留已确认计划直到当前搜索任务过期，允许用户重试搜索。
- 下载后文件映射 AI 失败：保留原始文件并进入未整理。
- `S00E100+` 发生执行前冲突：停止对应文件整理并通知用户重新确认。
- 容器重启导致临时计划丢失：重新识别；不能恢复时进入未整理。
- Plex module 不存在或不可用：不影响本搜索和整理模块完成自身职责。

## 14. 可观测性

日志必须包含但不得泄露密钥或完整下载链接：

- 两次 AI 调用的脱敏输入摘要和结构化输出摘要。
- 每个信息源的查询、状态、命中数量和耗时。
- 最终关系类型、来源条目和证据状态。
- 用户确认后的计划 ID。
- Prowlarr 最终查询。
- 下载后逐文件映射结果。
- 临时 `S00E100+` 预约、冲突和释放。
- 未整理原因。

## 15. 测试矩阵

### 15.1 AI 与来源状态

- Wikipedia、豆瓣和 TVDB 全部宕机，但两次 AI 调用成功。
- 任一信息源单独宕机时仍能生成计划。
- 第一次 AI 不可用时立即停止。
- 第二次 AI 返回无效 JSON 时立即停止。
- AI 提供可定位但未实时验证的 Wikipedia 条目时显示警告。
- AI 无法提供可定位条目时禁止临时关联。

### 15.2 同名与关系

- “想见你”同时生成 2019 剧集与 2022 电影关系候选。
- TVDB 官方 Special 优先于临时编号。
- 同时存在独立电影和 TVDB Special 时默认进入剧集库。
- Wikipedia 明确续篇关系时创建临时 `S00E100`。
- 仅演员或导演相同的弱关系不合并。
- 完全独立电影进入电影库。

### 15.3 临时编号

- 首个临时条目获得 `S00E100`。
- `S00E100` 已占用时获得 `S00E101`。
- 并发任务不会预约同一编号。
- 执行前发生新冲突时停止，不静默改号。
- 容器重启后不假装恢复旧预约。

### 15.4 多文件与整理

- 同一资源包同时映射主线、OVA、bonus 和延伸电影。
- 非叙事花絮不会占用虚构的 TVDB 集号。
- 无法匹配的文件进入未整理，已匹配文件继续完成。
- `source=confirmed` 不再使字段完整的计划被拒绝。
- 下载后映射器不能改变已确认的主线剧集或媒体库。

### 15.5 用户交互与模块隔离

- 用户只确认一次完整下载方案。
- 确认消息显式展示 AI-only 和 server-down 风险。
- 搜索模块不导入或调用 Plex 实现。
- 没有 Plex module 时搜索、下载和整理仍可运行。
- 现有模块注册、下载 provider 和 post-download pipeline 契约保持兼容。

## 16. 验收标准

实现完成后应满足：

1. 每次 `/search` 或 `/s` 都能从日志证明执行了两次搜索阶段 AI 调用。
2. 任意外部信息源宕机不会阻断 AI 下载方案生成。
3. AI 不可用时不会绕过 AI 直接搜索 Prowlarr。
4. “想见你”案例能展示剧集、续篇电影、关系依据、目标 `Season 00` 和对应风险。
5. TVDB 官方 Special 使用官方编号；强关联但未收录作品使用临时 `S00E100+`。
6. 临时关联方案始终包含可定位来源条目。
7. 用户确认后执行层不重新猜测媒体类型、主线剧集或季集方案。
8. 搜索模块不持久化临时关联元数据，也不直接接入 Plex。
9. 多文件资源包能部分成功，无法匹配部分安全进入未整理。
10. 目标测试、完整测试、Python 编译检查和 Git diff 检查全部通过。
