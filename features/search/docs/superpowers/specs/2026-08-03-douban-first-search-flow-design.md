# 豆瓣单源发现与确认后增强业务流程设计

**日期：** 2026-08-03  
**范围：** `features/search` 的作品发现、候选确认、直链入口、确认后元数据增强与可观测性  
**不在本次范围：** Prowlarr 扇出优化、手动 `/m` 流程、已记录的其他 P1 修复、发布与 Git 操作

## 1. 目标

将普通文本搜索从“Wikipedia、豆瓣、TVDB 多源同时召回并由 AI 合并”改为边界明确的分阶段流程：

```text
豆瓣发现作品
→ 程序去重与硬过滤
→ 统一 AI 搜索裁决
→ 用户确认
→ Wikipedia 补同一作品身份
→ TVDB 补剧集季集结构
→ Prowlarr 资源搜索
```

普通文本的作品发现源只有豆瓣。Wikipedia 和 TVDB 不产生首轮候选，也不作为“都不是”之后的候选回退来源。

## 2. 入口

### 2.1 普通文本

- `/s <关键词>` 和 `/search <关键词>` 只接受普通文本。
- `/s` 或 `/search` 不带参数时，仍可进入等待关键词的会话。
- 命令参数包含 URL 时拒绝执行，并提示：`作品链接请直接发送，无需 /s。`

### 2.2 直接分享链接

- 用户可以直接发送，或使用系统分享功能，把豆瓣、Wikipedia、TVDB 链接分享到机器人对话。
- 不需要先发送 `/s`。
- Host 只把受支持平台的无会话链接消息路由给 search；普通文本和其他网页链接不被 search 抢占。
- 一条分享消息可以包含标题、说明和一个作品链接。
- 重复出现但解析为同一平台稳定 ID 的链接按一个实体处理。
- 一条消息解析出多个不同作品实体时，直接提示：`链接无效，请一次只分享一个作品链接。`
- `/s <链接>` 旧入口不保留兼容。

### 2.3 链接解析

支持豆瓣、Wikipedia、TVDB 的：

- PC 页面；
- 移动页面；
- App 分享文本；
- 平台短链和受控重定向；
- 本地化语言路径；
- URL 编码后的标题或实体路径。

解析流程为：

```text
从整段文本提取 URL
→ 规范化 HTML entity、尾部标点和编码
→ 仅在平台允许列表内跟随有限次重定向
→ 读取 canonical URL / og:url
→ 提取 provider、entity type、stable ID
```

单个受支持链接不能取得稳定 ID，但分享文本或页面标题可用时，降级为普通文本豆瓣搜索。既没有稳定 ID，也没有可用标题时提示链接无效。

## 3. 普通文本发现状态机

### 3.1 首轮豆瓣查询

程序从原始输入中解析：

- 作品标题；
- 年份；
- 电影或剧集类型；
- 用户要求的季或集范围。

资源属性，例如 `4K`、`国语`、`全集`、字幕和编码，不进入作品标题。季集范围作为独立结构字段保留。

首次豆瓣 query 由程序按固定规则生成，不调用 AI。

### 3.2 程序规范化

豆瓣返回后，程序负责：

- subject ID 去重；
- 规范为简体中文展示；
- 提取可靠英文标题；
- 过滤与用户明确类型冲突的结果；
- 保留标题、年份、类型、subject ID、链接和海报；
- 判断标题、年份和类型是否形成唯一硬匹配。

豆瓣字段规范化必须拆开混合语言标题。主标题同时包含简中与英文、日文时，
`chinese_title` 只保存简中部分；英文只接受豆瓣明确字段或可靠的英文原名，
日文原名写入 `original_title`。不得因为整段包含汉字或拉丁字母，就把整段
归入一个语言字段。

只有程序确认“标题＋年份＋类型”唯一硬匹配时，才允许自动确认。系统先展示识别到的作品，再进入确认后增强。

### 3.3 统一 AI 搜索裁决器

非唯一硬匹配进入一个统一 AI 节点。AI 不拆分成候选评分、无匹配判断和 query 改写等多个无上下文小调用。

每次调用都传入完整 `SearchContext`：

```text
search_session_id
用户原始输入
程序解析的标题、年份、类型和季集范围
当前 attempt
当前豆瓣 query
当前豆瓣候选
上一轮 query、候选和 AI action
业务重试是否仍可用
prompt_version
```

AI 只能输出：

```json
{
  "action": "show_candidates | retry | no_match",
  "candidate_ids": ["真实豆瓣 subject ID"],
  "rewrite_query": ""
}
```

约束：

- AI 不能调用来源、生成作品、修改来源字段或补写元数据。
- `candidate_ids` 必须是本轮真实豆瓣 subject ID 的子集。
- 候选数量动态为 1–5。
- 当原始合格池超过一个时，AI 不能只返回一个来取得自动确认权；至少返回两个。
- AI 返回一个候选但该候选不是程序唯一硬匹配时，仍需要用户确认。
- AI 可以判断全部候选不相关。
- AI 不输出用户可见的置信分、理由或技术细节。

### 3.4 业务重试

以下情况共享一次业务重试预算：

- 豆瓣正常返回 0 条；
- AI 判断本轮全部候选不相关。

AI 在第一轮输出 `retry` 和一个改写 query。程序只用该 query 重搜豆瓣一次。第二次 AI 调用必须携带第一轮完整历史；第二轮不能再次改写。

整个搜索最多有两个 AI 逻辑裁决点：

- 正常多候选：一次；
- 触发 query 改写：两次；
- 直链或程序唯一硬匹配：零次。

### 3.5 用户确认

- 多候选显示 2–5 个规整候选。
- 单候选但不满足唯一硬匹配时，显示 `就是它 / 都不是`。
- 用户选择作品后锁定豆瓣 subject ID。
- 用户点击“都不是”立即结束本轮，不改写、不重搜、不调用 Wikipedia 或 TVDB。

## 4. 候选展示契约

用户界面固定为：

```text
简中标题（年份）
Official English Title
类型：电影 / 剧集
来源：豆瓣
```

- 简中标题为主标题。
- 英文标题只有在豆瓣提供可靠英文名时才显示。
- 首轮不会为了补英文名逐个查询 Wikipedia。
- 不显示 AI 置信度、模型理由、来源版本、未补全来源列表或内部评分。
- 没有可靠英文标题时留空，不让 AI 翻译或编写。

## 5. 确认后增强

确认后创建 `ConfirmedIdentity`，至少包含：

```text
anchor provider
stable ID
zh-CN title
known English/original titles
year
media type
requested scope
```

增强 query 不复用用户原始输入或 AI 改写词，而是由程序逐级构建。

### 5.1 Wikipedia

Wikipedia query 使用豆瓣确认后的：

- 简中标题；
- 年份；
- 电影或剧集类型。

Wikipedia 只补同一作品的：

- 简中标题；
- 英文标题；
- 原名；
- 别名；
- Wikipedia/Wikidata 稳定身份。

不扩展前传、续集、重制版或衍生作。无法唯一关联时记录失败并继续。

中文 Wikipedia API 请求使用 `zh-cn` 显示变体，保留规范标题和 Wikidata
身份；返回的标题不得覆盖豆瓣已经确认的简中主标题。缺少可验证电影/剧集
类型的同名人物、概念或列表页不视为同一作品。

### 5.2 TVDB

仅剧集进入 TVDB。TVDB query 优先使用：

1. Wikipedia 验证后的英文标题；
2. 豆瓣已有的可靠英文名或原名；
3. 首播年份；
4. `series` 类型约束。

没有可靠英文身份时不查询 TVDB。TVDB 只能在唯一匹配时补：

- TVDB Series ID；
- season ID；
- episode ID；
- 标准季集列表和播出日期。

无法唯一关联时不让 AI 猜测、不展示 TVDB 候选，也不阻断作品级资源搜索。

TVDB 的 Official English Title 只允许来自：

1. 明确标记为 `eng/en` 的翻译；
2. `original_language=en` 时的作品主标题；
3. 已确认的豆瓣或 Wikipedia 英文身份补空。

无语言标签的 `name_translated` 和“第一个包含拉丁字母的别名”都不能证明
该标题是英文。无法验证时留空，而不是选择西班牙语、波兰语等拉丁别名。

TVDB 不可用时，剧集按 `whole_series` 继续，不展示季或单集选择；未经 TVDB 验证的季集号不得静默进入 Prowlarr query。

### 5.3 Prowlarr query 与年份门禁

来源增强完成并生成严格 `media_metadata v1` 后，只构建一个确定性 query：

| 检索范围 | Query |
|---|---|
| 单电影 | `Canonical Title YYYY` |
| 多季全集 | `Canonical Title` |
| 单季 | `Canonical Title Sxx` |
| 单集 | `Canonical Title SxxExx` |

`Canonical Title` 来自已经确认的标题策略。来源别名、AI 改写词、用户原始
输入和版本属性不得再次进入 Prowlarr query。

片源门禁按范围处理年份：

- 单电影必须出现与确认上映年一致的年份；
- 多季全集不要求年份；有完整 TVDB inventory 时，出现的年份必须位于已
  验证播出区间，没有完整播出区间时不以首播年单独否决全集；
- 单季不要求年份；出现时可匹配整剧首播年或目标季播出年；
- 单集不要求年份，`SxxExx` 是主要范围证据，年份只作为软证据。

## 6. 直链确认后的增强

受支持的直链稳定 ID 是用户已经确认的作品身份，跳过豆瓣候选流程：

- 豆瓣直链：从豆瓣实体进入 Wikipedia，再按需进入 TVDB。
- Wikipedia 直链：从 Wikipedia 实体进入 TVDB；不要求先补出豆瓣 subject ID。
- TVDB 直链：保留 TVDB 实体和已有季集结构，并补 Wikipedia 身份。

直链入口不是普通文本作品发现，因此不违反“豆瓣是唯一文本发现源”的约束。

## 7. 技术故障与业务终态

| 情况 | 行为 |
|---|---|
| 豆瓣正常但 0 结果 | 使用一次 AI 改写业务重试 |
| AI 判断全部不相关 | 与 0 结果共享一次业务重试 |
| 豆瓣超时、限流、被拦截或服务错误 | 明确提示豆瓣暂时不可用，不调用其他来源兜底 |
| AI 超时、服务错误或结构不合规 | 用完全相同的 SearchContext 原样重试一次 |
| AI 技术重试后仍失败，且有豆瓣候选 | 程序按豆瓣原始顺序展示前 5 个，必须由用户确认 |
| AI 技术重试后仍失败，且豆瓣为 0 | 提示用户修改关键词 |
| 用户点击“都不是” | 终止本轮 |
| Wikipedia 失败或歧义 | 对应字段留空并继续 |
| TVDB 失败或歧义 | 整剧级继续，不提供季集选择 |
| 多个不同直链实体 | 提示链接无效 |

AI 技术重试不重新调用豆瓣，不消耗业务 query 重写次数。

## 8. 日志契约

每个搜索会话使用稳定的 `search_session_id`。入口绑定
`chat_id`、`user_id` 和起始时间，后续同一会话事件自动继承这些字段；
`search.completed` 写入后清理日志上下文。通用字段为：

```text
search_session_id
chat_id / user_id
event
elapsed_ms
```

豆瓣与 AI 轮次额外记录 `attempt`；技术重试额外记录
`technical_attempt`；只有 `search.completed` 记录 `terminal_status`。

至少记录：

```text
search.input_classified
search.command_url_rejected
search.direct_link_received
search.link_resolved
search.link_downgraded
search.douban_started
search.douban_completed
search.douban_failed
search.hard_match_evaluated
search.ai_request
search.ai_response
search.ai_technical_retry
search.ai_fallback
search.candidates_displayed
search.user_confirmed
search.user_rejected
search.wikipedia_started
search.wikipedia_completed
search.wikipedia_skipped
search.tvdb_started
search.tvdb_completed
search.tvdb_skipped
search.prowlarr_query_built
search.release_gate_evaluated
search.completed
```

AI 日志记录原始输入、清理 query、结构化约束、候选 ID/标题/年份/类型、attempt、重试预算、模型、prompt version、action、返回 ID 或 rewrite query、结构校验结果和耗时。

INFO 记录业务路径，WARN 记录降级、歧义、AI 非法输出和增强失败，ERROR 记录阻断来源故障。不得记录 API key、Cookie、Token、Authorization header 或其他凭据。TVDB 只记录匹配 ID、季数和集数，不展开全部单集内容。

每个会话最终必须有一条 `search.completed`，终态至少区分：

```text
success
user_rejected
no_match
source_unavailable
invalid_link
ai_fallback
cancelled
retry
internal_error
```

## 9. 验收

自动化测试至少覆盖：

1. 简中关键词唯一硬匹配零 AI 调用并自动确认。
2. 多个豆瓣结果由 AI 输出 2–5 个真实候选。
3. 单个非硬匹配候选仍要求用户确认。
4. 豆瓣 0 结果触发一次 AI 改写和一次豆瓣重搜。
5. AI 判断不相关与 0 结果共享同一业务重试预算。
6. 第二轮仍无匹配时提示用户修改输入。
7. “都不是”终止且不触发重试。
8. AI 技术故障原样重试一次，然后程序候选兜底。
9. 豆瓣技术故障不调用 Wikipedia/TVDB。
10. Wikipedia 失败非阻断。
11. TVDB 失败时按整剧继续且不展示季集选择。
12. Wiki query 只使用确认身份；TVDB query 优先使用 Wiki 英文身份。
13. PC、移动、App 分享、短链、本地化链接可解析。
14. `/s <链接>` 明确拒绝，直接分享同一链接成功。
15. 多实体链接明确无效。
16. 候选始终简中为主、可靠英文为辅。
17. 每个分支通过 `search_session_id` 可在日志中还原。

## 10. 交付边界

本次在 Mac 本地完成源码、测试和文档修改。Mac 工作区禁止 Git，不创建分支、提交、标签或发布。验证完成后等待 Syncthing 显示 `Up to Date / 最新`，由用户在 Unraid 权威路径完成后续发布。
