# Telepiplex 有界查询、Prowlarr 正确性门禁与结构化重命名设计

日期：2026-07-17

状态：设计已确认，等待书面规格复核

主责模块：`feature/media-search`

协作模块：`feature/telepiplex-core`、`feature/renaming`

## 1. 文档效力

本设计合并并收紧 2026-07-16 以来已经确认的媒体搜索规则。与下列文档发生冲突时，以本设计为准：

- `2026-07-16-bounded-media-search-design.md`
- `2026-07-16-canonical-media-entity-scoring-design.md`

被本设计明确废弃的旧条款包括：

- 不建立或复用持久化媒体实体注册表；
- Prowlarr Query 不再默认强制附加年份；
- 不支持 Special、OVA、OAD、SP、Extra 或 Extras 下载 scope；
- 显式包含 S00、Specials、OVA 或 Extras 的全剧包不能通过普通全剧门禁；
- 作品身份和下载范围属于正确性门禁，不计入 Preference Scoring；
- Prowlarr 发布结果上限为 12，媒体实体候选上限为 7。

## 2. 目标

`/s` 只服务于清晰、有限、可验证的媒体查询：

- 普通电影；
- 整部剧集；
- 指定季；
- 指定单集；
- 豆瓣或 TVDB 直达链接。

系统依赖 Prowlarr 和 Indexer 做宽松召回，在 Telegram 展示前用独立的正确性门禁排除错误作品和错误范围。Preference Scoring 只比较已经正确的片源质量。

目标链路：

```text
Query 清洗
  -> 外部来源确认媒体实体
  -> 海报候选人工确认
  -> 剧集范围确认
  -> 宽松 Prowlarr 检索
  -> 发布结果正确性门禁
  -> Preference Scoring
  -> Telegram 最多 12 个结果
  -> 下载任务携带 confirmed media_metadata
  -> renaming 结构化文件映射
```

## 3. 核心原则

1. Query 规则有限且公开。复杂范围表达要求用户改用标准格式。
2. 普通标题先确认作品身份，再确认剧集范围。
3. 任意输入语言最终都使用规范拉丁检索标题：
   - 非日文作品使用官方英文标题；
   - 日文作品使用来源确认的官方罗马字原题，不使用英文翻译标题。
4. Prowlarr Query 保持宽松，不添加 `Complete`、画质、编码、片源组等推测词。
5. 作品身份、下载范围和可下载性是硬门禁，不是片源质量分。
6. 门禁后没有精确结果时明确失败，不使用其他范围自动补位。
7. 媒体候选、证据、AI 输出、海报、Prowlarr 结果和评分均为当前请求内状态，不持久化。
8. `/s` 下载任务必须把已确认契约传给 renaming；renaming 不重新判断作品身份。
9. Special 不是检索 scope。相关电影是否整理进剧集 Specials 只属于下载后的放置关系。

## 4. 模块责任

### 4.1 media-search

负责：

- Query 清洗和有限意图解析；
- Wikipedia、豆瓣、TVDB 的来源适配与候选融合；
- 媒体实体硬门禁、固定评分和海报确认；
- 剧集范围确认；
- Prowlarr Query 生成；
- Prowlarr 原始结果规范化、正确性门禁和 Preference Scoring；
- confirmed `media_metadata` 生成；
- 当前请求内 operation、取消、超时和审计日志。

### 4.2 telepiplex-core

负责：

- Telegram 命令和 callback 分发；
- 安全的文本、海报、键盘和 operation 状态渲染；
- 消息编辑失败后的新消息降级；
- Feature RPC 生命周期和截止时间。

Core 不理解媒体身份、范围或片源评分。

### 4.3 renaming

负责：

- 消费 confirmed `media_metadata`；
- 扫描下载文件树并生成结构化内容形态；
- 把文件确定性映射到已锁定的电影或 TVDB 普通集；
- 在确定性映射不能唯一完成时，让 AI 只在已锁定 item 集合内辅助配对；
- 生成最终目录和文件名并交给 Plex 流程。

renaming 不重新搜索媒体实体，不从文件树创建新季集，不覆盖已确认标题。

## 5. Query 输入契约

### 5.1 支持格式

```text
/s 作品名
/s 作品名 2022
/s 作品名 Movie
/s 作品名 Series

/s 作品名 全剧
/s 作品名 全集
/s 作品名 Complete Series

/s 作品名 第一季
/s 作品名 第1季
/s Title Season 1
/s Title Season 01
/s Title S01

/s 作品名 第一季 第一集
/s 作品名 第1季第1集
/s Title Season 1 Episode 1
/s Title Season 01 Ep 01
/s Title S01E01

/s 豆瓣链接
/s TVDB链接
```

`Episode 5`、`E05` 或“第5集”只有在来源确认目标为单季剧时才能直接形成单集范围。多季剧必须先由用户提供或选择季号。

### 5.2 不支持格式

```text
1x02
S01E01-E05
S01-S03
第一到第三季
前五集
最新几集
除了第二季
Season One
```

这些表达不进入无限 AI 推断。系统提示用户改用数字季集格式或豆瓣/TVDB 链接。

`2160p`、`REMUX`、`DV`、字幕语言和片源组等质量偏好不能通过 `/s` Query 指定，继续由 Preference Scoring 配置处理。

### 5.3 Special 相关输入

以下词不再定义 scope：

```text
Special
Specials
SP
OVA
OAD
Extra
Extras
Bonus
特别篇
番外篇
特典
```

清洗层不能因为标题中出现这些词就立即拒绝正常作品。完整文本仍可进入来源验证：

- 来源证明它是普通电影或独立剧集：按普通 movie/series 路线继续；
- 来源只指向 TVDB Season 0/Special：返回 `unsupported_special_scope`；
- TVDB S00 Episode 直达链接：直接返回 `unsupported_special_scope`，不降级为文本搜索。

## 6. Query 清洗

URL 检测先于文本清洗。普通文本依次执行：

1. Unicode NFKC；
2. 全角空格、重复空格和首尾空白统一；
3. 全角/半角标点规范化；
4. 外围书名号和引号去除；
5. 括号去除但保留括号内容；
6. 冒号、破折号等转为空格分隔符，前后标题内容全部保留；
7. 提取明确年份、媒体类型提示、全剧/季/集范围；
8. 标题内容保持原顺序。

示例：

```text
《蝙蝠侠：黑暗骑士》
-> 蝙蝠侠 黑暗骑士
```

规则层不执行：

- 简繁体转换；
- 翻译；
- 拼写或口误修复；
- 裸数字用途猜测；
- 官方英文标题认定；
- 日文罗马字生成。

裸数字默认保留在标题中。只有外部来源证明数字属于正式标题、分卷或年份时才能形成合格实体。`蝙蝠侠1`不能自动解释为第一部、第一季或第一集。

清洗输出为结构化意图：

```json
{
  "raw_query": "黑暗荣耀 第一季",
  "normalized_title": "黑暗荣耀",
  "year_hint": null,
  "media_type_hint": null,
  "scope": "season",
  "season_number": 1,
  "episode_number": null
}
```

## 7. 外部来源解析

### 7.1 来源专用 Query

共享意图不能原样发送给所有来源，而是分别投影：

- 豆瓣：中文完整标题；季查询可生成“第一季”和“第1季”变体；
- 中文 Wikipedia：中文完整标题和明确年份；
- 英文 Wikipedia：取得可信拉丁标题后定向查询；
- TVDB：基础标题、年份和媒体类型分别传递，不能把 `S01`、`E01` 拼进标题字段。

普通中文 Query 采用两阶段发现：

1. 豆瓣和中文 Wikipedia 发现中文实体、原名、别名、年份、类型和海报；
2. 从已验证事实取得官方英文标题或日文罗马字，再定向查询 TVDB 和英文 Wikipedia。

### 7.2 候选合并

候选只在以下情况合并：

- 稳定 ID 或权威跨库链接明确对齐；或者
- 规范标题/官方别名、年份和媒体类型共同一致。

摘要提及、标题包含和搜索结果位置不能用于合并实体。

### 7.3 普通文本合格门

普通文本候选必须：

- 至少得到两路独立来源支持；
- 能确定 movie 或 series；
- series 必须锁定 TVDB Series ID；
- 能取得规范拉丁标题；
- 标题、年份、类型和稳定 ID 不存在硬冲突；
- 海报缺失可以文本降级，但不能伪造海报。

候选数量：

- 0 个：允许一次 AI 标题纠错假设并重新走同一来源链；仍为 0 则拒绝；
- 1 个：仍展示实体和海报，由用户确认；
- 2–7 个：全部展示；
- 超过 7 个：不截断、不让 AI 代选，要求更完整标题、年份、类型或豆瓣/TVDB 链接。

### 7.4 直达链接

豆瓣或 TVDB 链接直接锁定链接指向的实体，跳过文本候选竞争。

豆瓣链接：

- 读取中文名、原名/别名、年份、类型和海报；
- 拉丁原名/别名可作为官方英文候选；
- 缺少规范拉丁标题时，定向查询 Wikipedia/TVDB；
- 只有标题、年份和类型唯一对齐时才能补字段。

TVDB 链接：

- 使用 TVDB ID 锁定官方英文名、年份、类型、别名、海报和剧集 inventory；
- 中文名优先读取官方 translation/alias；
- 缺失时定向查询豆瓣或中文 Wikipedia；
- 补充来源不能改变链接锁定的稳定身份。

每个补充字段保存当前请求内的来源标记。链接解析失败直接报错，不降级为普通文本。

## 8. AI 在 Prowlarr 前的角色

AI 不学习权重，不写媒体数据库，也不生成最终 Prowlarr Query。

### 8.1 一次标题纠错

只在结构合法的简单 Query 首轮得到 0 个合格实体时触发。AI 可以提出：

- 可能的错别字或口语简称；
- 可能的跨语言标题/别名；
- 裸数字可能属于正式标题的假设；
- movie/series 类型提示。

AI 输出必须重新进入 Wikipedia、豆瓣和 TVDB 验证链。来源不能证明时仍然失败。禁止循环改写。

明确不支持的范围表达不调用此兜底。

### 8.2 实体候选评分员

AI 可根据已存在的 fact ID 填写固定、版本化的语义评分卡：

- 标题和别名是否等价；
- 年份和媒体类型是否符合用户意图；
- 同名电影、剧集、续作或分卷是否容易混淆；
- 已验证关系是否与用户意图一致。

AI 不能生成稳定 ID、官方标题、年份、海报、季数或集数。无效引用和越界分数由程序拒绝。

实体评分只排序和标记推荐，不隐藏已经通过硬门槛的 1–7 个候选，也不替用户选择。AI 不可用时，清晰 Query 和直达链接仍可依靠确定性事实运行。

### 8.3 选中作品后的关系假设

用户选中具体电影后，AI 可根据来源事实提出前传、续集、衍生、电影版或与某剧的放置关系。假设必须经过定向来源验证。

关系只影响用户可见标签和下载后的 placement，不改变 Prowlarr 检索身份。Special 检索已经删除。

## 9. 实体确认交互

候选卡显示：

- 海报；
- 中文显示名；
- 规范拉丁标题；
- 年份；
- movie/series 类型；
- 已验证关系；
- 实体候选分和简短理由。

海报必须绑定候选稳定键。海报发送/编辑失败时降级为纯文本卡片，保留相同 callback。用户选中前不能创建 Prowlarr 任务。

## 10. 剧集范围状态机

媒体实体确认与下载范围确认严格分开。

### 10.1 裸剧名

单季剧：

```text
全剧（推荐） | 指定集
```

多季剧：

```text
全剧（推荐） | 指定季 | 指定集
```

不能从 TVDB 第一集、`items[0]` 或默认值推导 `S01E01`。

### 10.2 已指定季

`/s 黑暗荣耀 第一季`：

```text
整季（推荐） | 指定集
```

指定集时用户只输入集号。输入必须由 TVDB 验证存在且已经播出。

### 10.3 已指定集

`/s 黑暗荣耀 第一季 第一集`在实体确认后验证 S01E01 是否存在和已播，随后直接进入单集搜索。

### 10.4 只指定集号

- 单季剧：集号可直接落到唯一普通季；
- 多季剧：先要求季号，再要求集号；
- 未播或不存在的季集在 Prowlarr 前阻断。

TVDB 不可用时不能进入 series 范围搜索。

## 11. Prowlarr Query

Prowlarr Query 只从已确认的规范拉丁标题和范围生成：

| Scope | Query | Category |
| --- | --- | --- |
| movie | `Canonical Title` | Movie |
| whole_series | `Canonical Title` | TV |
| season | `Canonical Title S01` | TV |
| episode | `Canonical Title S01E01` | TV |

Query 不添加：

- `Complete`；
- 默认年份；
- 画质、编码、HDR/DV；
- 字幕或音轨；
- 片源组；
- TVDB S00/Special 编号。

年份、类型和稳定 ID 继续用于已确认身份和结果门禁，而不是强迫所有 Indexer 的发布名都包含相同 Query 词。

## 12. Prowlarr 原始结果预处理

处理顺序：

```text
所有可用 Indexer 原始结果
  -> 字段规范化
  -> 重复项合并
  -> 可下载性检查
  -> 作品身份门禁
  -> 发布范围分类
  -> 目标范围门禁
  -> Preference Scoring
  -> 最多 12 个
```

重复项优先按 infohash 合并，其次使用稳定下载 URL 或规范标题+大小。合并项保留命中的 Indexer 列表和最终采用的下载来源，不能重复占用 Telegram 按钮。

Indexer 行为：

- 单个 Indexer 超时或失败：记录健康报告，其余继续；
- 所有 Indexer 失败：报告搜索失败，不能显示为“没有片源”；
- Indexer 正常但返回 0：报告“Prowlarr 未召回结果”；
- 有原始结果但全部被门禁拒绝：报告“没有符合目标作品和范围的结果”。

## 13. 发布结果正确性门禁

正确性门禁不产生质量分。每条结果输出：

```json
{
  "identity_match": true,
  "release_scope": "single_season_pack",
  "observed_seasons": [1],
  "observed_episodes": [],
  "scope_match": true,
  "evidence": ["canonical_alias", "season_marker:S01"],
  "rejection_reason": null
}
```

### 13.1 作品身份

必须使用完整词边界匹配规范标题或已验证别名，不能使用简单子串或前缀包含。

`The Office Wife`不能因为包含 `The Office`而通过。发布名多出的语义标题、续作/分卷标记必须与选中实体一致。

年份规则：

- 发布名含年份时必须与选中实体一致；
- 已知存在同名不同年份实体时，缺少年份且没有其他稳定区分证据的发布结果也拒绝；
- 唯一作品的精确规范标题可以在发布名无年份时通过。

### 13.2 发布范围分类

正向范围只有：

```text
movie
single_episode
multi_episode
single_season_pack
multi_season_pack
whole_series_lexical
unknown
```

结果侧可以识别 Indexer 发布名中的常见变体，包括 `1x02` 和集号范围；这些变体不因此成为受支持的用户输入语法。

出现明确 `S00`、Special、OVA、OAD、Extras 或 Bonus 范围的结果标记为 `unsupported_special_content`，不能作为任何普通 movie/whole_series/season/episode 结果放行。官方普通电影标题本身包含这些单词时，以已确认实体标题为准，不能按单词机械拒绝。

文件数和大小只能作为展示或质量信息，不能把 `unknown` 猜成整季或全剧。

### 13.3 movie

只接受目标电影身份。普通剧集、续作、前作、同名其他年份电影和附加内容均拒绝。

### 13.4 whole_series

通过条件满足其一：

1. 发布名明确标记 `Complete Series`、`Full Series`、`All Seasons` 等全剧语义，并且没有显式 Special/Extras 范围；
2. 发布名提取到的普通季集合与 TVDB 当前全部已播普通季集合一致。

示例：

| TVDB 已播普通季 | 发布名 | 结果 |
| --- | --- | --- |
| S01 | `The Glory S01` | 通过：单季剧全剧 |
| S01–S09 | `The Office S01-S09` | 通过 |
| S01–S09 | `The Office S01-S08` | 拒绝：缺 S09 |
| S01–S09 | `The Office S02-S09` | 拒绝：缺 S01 |
| S01–S09 | `The Office S01-S10` | 拒绝：S10 未验证 |
| S01–S09 | `The Office S00-S09` | 拒绝：显式包含 Special |
| S01–S09 | `Complete Series + Extras` | 拒绝：显式包含附加内容 |

`single_season_pack`和`multi_season_pack`是发布名形态；是否满足全剧请求由 TVDB 普通季覆盖语义决定。

### 13.5 season

指定季只允许：

- 目标季号唯一；
- 没有集号；
- 没有其他普通季；
- 没有显式 Special/Extras。

请求 S01 时：

| 发布名 | 结果 |
| --- | --- |
| `Title S01` | 通过 |
| `Title Season 1 Complete` | 通过 |
| `Title S01E01` | 拒绝：单集 |
| `Title S01-S09` | 拒绝：多季 |
| `Title S02` | 拒绝：错误季 |
| `Title` | 拒绝：范围未知 |

### 13.6 episode

指定集只允许精确单集：

| 请求 | 发布名 | 结果 |
| --- | --- | --- |
| S01E01 | `Title S01E01` | 通过 |
| S01E01 | `Title S01E02` | 拒绝 |
| S01E01 | `Title S01E01-E02` | 拒绝：多集 |
| S01E01 | `Title S01` | 拒绝：整季 |
| S01E01 | `Title S01-S09` | 拒绝：多季 |

不使用多集包、整季包或多季包自动补位。

### 13.7 门禁后为 0

门禁后没有精确结果时：

- 不降低作品身份标准；
- 不切换到其他 scope；
- 不用其他范围填满 12 个；
- 显示原始数量和各拒绝原因；
- 结束本次片源选择或让用户重新发起明确 Query。

## 14. Preference Scoring

只有通过正确性门禁且拥有可用下载链接的结果进入 Preference Scoring。

现有质量模型保持：

```text
keyword_scores
  + indexer_scores
  + seeders
  + size
  = final_score
```

作品身份、scope、TVDB 覆盖和可下载性不加入质量分。

排序：

1. `final_score` 降序；
2. seeders 降序；
3. 现有 size 次级规则；
4. 取前 12 个。

每个结果保留：

- 原始发布名；
- 最终分；
- 命中的片源关键词和对应权重；
- Indexer 名称和对应权重；
- seeders；
- size；
- 正确性范围标签；
- 下载来源。

## 15. Telegram 输出

结果正文示例：

```text
Prowlarr Query：The Office US S01

Indexer：
- Indexer A：返回 48，门禁合格 9
- Indexer B：返回 22，门禁合格 4
- Indexer C：超时

正确性门禁：
- 原始结果：70
- 作品不符：18
- 单集：25
- 多季包：7
- 范围未知：3
- 无可用链接：4
- 精确第一季：13

最终展示：12

① The.Office.US.S01.1080p.BluRay.REMUX
范围：第一季整季
得分：86
命中：REMUX +30 | BluRay +20 | Indexer A +10
种子：24 | 大小：137 GB
```

按钮使用圈号，每行三个：

```text
[①][②][③]
[④][⑤][⑥]
[⑦][⑧][⑨]
[⑩][⑪][⑫]
[退出]
```

门禁后为 0 时不显示片源按钮，只显示明确报告：

```text
没有找到“第一季整季”的精确片源。

Prowlarr 返回 70：
单集 25 | 多季包 7 | 错误作品 18 | 范围未知 3
未自动展示其他范围。
```

## 16. 日志与可观测性

每次 operation 使用同一 `operation_id`记录：

- 原始 Query 和结构化意图摘要；
- 各 provider 的 Query、耗时、结果数和失败原因；
- AI 是否触发、输入 fact ID 摘要、结构化输出和校验结果；
- 实体候选数量、硬门禁和分数；
- 用户确认的实体、scope 和 TVDB inventory 摘要；
- 最终 Prowlarr Query、媒体分类和各 Indexer 原始数量；
- 去重数量、可下载性拒绝数量；
- 正确性门禁各分类数量和拒绝原因；
- 每个输出结果的质量分组成；
- 下载交接和取消状态。

日志不记录：

- API Key；
- 完整 magnet；
- 完整来源正文；
- 未裁剪的 AI 提示词；
- 海报二进制。

`timed_out`、`deadline_exceeded`、`server_down`、`invalid_contract`和`no_exact_release`必须使用不同原因码。

## 17. 超时、取消和 Telegram 错误

- `/s`快速返回后台 operation，Telegram command RPC 不等待完整规划；
- provider、AI、Prowlarr 和下载链接解析使用独立阶段预算；
- 总预算到达时结束 operation 并返回当前阶段和原因，不能停留在“正在规划”；
- 用户取消后在 provider、AI、Prowlarr 和提交 115 前检查取消信号；
- 单个 Indexer 健康检查失败不阻断其他 Indexer；
- 所有 Indexer 失败与“无精确结果”使用不同用户提示；
- `edit_message`或媒体编辑返回 Telegram `BadRequest`时，新发一条消息并保留同一 operation 状态；
- revision/idempotency 保护必须防止旧任务覆盖取消或更新后的状态。

## 18. 下载任务交接

用户选中片源后，下载任务携带当前请求的最小 confirmed 契约：

```json
{
  "identity": {
    "chinese_title": "办公室",
    "canonical_title": "The Office US",
    "year": 2005,
    "media_type": "series",
    "external_ids": {
      "tvdb_series_id": 73244
    }
  },
  "retrieval": {
    "scope": "season",
    "season_number": 1,
    "episode_number": null,
    "prowlarr_query": "The Office US S01"
  },
  "placement": {
    "library_type": "series",
    "category_kind": "live_action_series"
  },
  "items": [
    {
      "season_number": 1,
      "episode_number": 1,
      "tvdb_episode_id": 123
    }
  ]
}
```

`items`包含本次目标范围内全部已确认、已播出的 TVDB 普通集。该契约只随下载任务流转，renaming/Plex 交接完成后释放。

## 19. renaming 结构化文件映射

### 19.1 `/s`任务

renaming 必须直接使用 confirmed 契约：

- 不根据下载目录重新搜索作品；
- 不用文件名覆盖中文名、规范拉丁标题、年份或媒体类型；
- 不调用 AI 判断“这是哪个作品”；
- 不把文件树拼成自然语言 Query；
- 文件名推断只能映射文件，不能修改已确认实体。

文件树解析为：

```json
{
  "root_name": "The Office US",
  "content_shape": "multi_season_pack",
  "observed_seasons": [1, 2, 3, 4, 5, 6, 7, 8, 9],
  "videos": [
    {
      "path": "Season 01/The.Office.S01E01.mkv",
      "season_number": 1,
      "episode_numbers": [1],
      "size": 123456789
    }
  ]
}
```

内容形态：

```text
movie
single_episode
episode_set
season_pack
multi_season_pack
unknown
```

确定性映射：

- movie 使用已确认电影身份；
- episode 只能映射到锁定目标集；
- season 文件季号必须等于目标季，集号必须属于 `items`；
- whole_series 文件只能映射到已锁定 TVDB 普通季集；
- 不在目标集合内的 S00、Special、Extra 文件不自动整理；
- 样片、花絮和无法识别的视频不改变主任务身份。

确定性映射不能唯一完成时，AI 只能从已锁定 `items`中选择文件对应项。AI 不能创建新季集、修改实体、扩大范围或根据第一个文件决定整个目录身份。

### 19.2 手动 `/m`任务

没有 confirmed 契约时，renaming 只构造结构化探针：

```json
{
  "identity_query": "The Office US",
  "year_hint": null,
  "content_shape": "multi_season_pack",
  "observed_seasons": [1, 2, 3, 4, 5, 6, 7, 8, 9]
}
```

规则：

- `identity_query`只从顶层目录名清洗得到；
- 季集信息只作为内容形态证据，不拼入标题；
- 原始文件树不能作为一句自然语言 Query；
- AI 标题纠错后仍必须重新走 media-search 来源验证；
- 唯一、硬门禁合格的实体可以生成当前任务临时 confirmed 契约并继续；
- 0 个或多个合格实体时进入 `/未整理`并报告原因，不让 AI 代选。

手动 `/m`任务不能自动建立电影到剧集 Specials 的放置关系。

## 20. 相关电影和 Specials 放置边界

Special 下载 scope 已删除，但已确认的相关电影放置关系可以保留：

```text
检索身份：movie
Prowlarr Query：电影规范拉丁标题
正确性门禁：movie
下载对象：完整电影
最终放置：renaming 根据已确认 placement 整理
```

media-search 只有在用户选定电影后，关系假设被来源验证且用户确认时，才能写入当前任务 `placement.mapping_kind`。renaming 不能下载后自行建立关系。

## 21. 迁移边界

### 21.1 media-search

- 保留 provider adapters、operation 生命周期、候选海报和下载交接；
- 将 Query 清洗、provider Query 投影和候选融合拆成清晰单元；
- 新增独立发布正确性分类器和门禁；
- Preference Scoring 只接收门禁合格结果；
- 恢复质量分详情和 Indexer 报告；
- 删除 Special 正向搜索 scope；
- 删除请求结束后的实体/候选持久化。

### 21.2 telepiplex-core

- 保持 callback 命名空间和 operation revision 保护；
- 支持 12 个圈号按钮的三列键盘；
- 保持海报失败文本降级和 `BadRequest`新消息降级；
- 不引入媒体业务规则。

### 21.3 renaming

- `/s`任务优先消费 confirmed 契约；
- 删除把完整文件树拼成媒体搜索文本的路径；
- 增加结构化内容形态探针；
- AI 只做已锁定 item 集合内的文件映射；
- 手动 `/m`歧义任务进入 `/未整理`。

## 22. 测试与验收

### 22.1 Query 清洗

- 中文全角标点和半角标点得到相同结构化意图；
- `蝙蝠侠：黑暗骑士`保留主副标题；
- `Season 01`、`S01`和“第一季”得到相同 season；
- `Season One`、范围表达和质量词得到明确拒绝；
- Special 词作为普通电影正式标题时不被机械拒绝；
- 只解析到 TVDB S00 的输入返回 `unsupported_special_scope`。

### 22.2 来源与候选

- 豆瓣、Wikipedia、TVDB 使用来源专用 Query；
- 普通文本需要两路来源，series 必须有 TVDB Series ID；
- 0 个候选只允许一次 AI 标题纠错并重新验证；
- 1 个候选仍展示海报；
- 2–7 个全部展示；
- 超过 7 个阻断；
- 豆瓣/TVDB 链接锁定身份，补充来源不能换实体；
- 日文作品使用官方罗马字，非日文使用官方英文。

### 22.3 Prowlarr Query

- movie 不附加质量词；
- whole_series 不附加 `Complete`或默认年份；
- season 只附加 `Sxx`；
- episode 只附加 `SxxExx`；
- 不生成 Special/S00 Query。

### 22.4 正确性门禁

- `The Office Wife`不能匹配 `The Office`；
- 同名不同年份电影在缺少区分证据时拒绝；
- 单季剧 S01 可满足 whole_series；
- S01–S09 在 TVDB 只有 S01–S09 时满足 whole_series；
- S01–S08、S02–S09、S01–S10 拒绝；
- S00–S09 和 `Complete Series + Extras`拒绝；
- season 结果不混入 episode 或 multi-season；
- episode 结果不混入 multi-episode、season 或 whole-series；
- unknown 不使用文件数或大小猜测；
- 门禁后为 0 不自动降级。

### 22.5 Preference Scoring 与 Telegram

- 正确性门禁在质量评分之前执行；
- 错误作品或范围不能凭质量高分进入结果；
- final score、关键词权重、Indexer、seeders 和 size 可见；
- Indexer 原始/合格数量和健康错误可见；
- 结果先全部评分，再取前 12；
- 圈号按钮每行三个；
- 只有 3 个精确结果时只显示 3 个；
- Telegram 编辑 `BadRequest`后新发消息且 operation 不重启。

### 22.6 renaming

- confirmed `/s`任务不调用媒体身份解析；
- 文件树中的第一个 S09E23 不能把完整剧集识别为单集；
- root、content shape、season/episode 和 video files 分字段传递；
- AI 不能映射到 `items`之外；
- S00/Special/Extra 文件不自动整理；
- 手动 `/m`唯一硬门禁实体可继续；
- 手动 `/m`零个或多个实体进入 `/未整理`；
- 相关电影只有 confirmed placement 才能整理到剧集。

### 22.7 跨模块验收

至少验证：

1. 中文、英文和日文普通标题；
2. 同名 movie/series 候选；
3. 豆瓣和 TVDB 直达链接；
4. whole_series、season 和 episode；
5. Prowlarr 多 Indexer 部分失败；
6. 正确性门禁过滤和质量评分报告；
7. 12 个三列结果按钮；
8. 115 下载交接；
9. renaming 使用 confirmed 契约；
10. Plex 入队前路径与媒体身份一致。

完整成功标准是：

```text
search
  -> entity confirmation
  -> scope confirmation
  -> Prowlarr recall
  -> correctness gate
  -> preference score
  -> download handoff
  -> rename
  -> Plex enqueue
```

单次外部查询成功或 Prowlarr 有返回都不等于链路成功。

## 23. 非目标

- 不做影视百科、系列宇宙或任意自然语言搜索系统；
- 不支持 Special/OVA/OAD/SP/Extra/Extras 下载；
- 不支持跨季、跨集、最新几集或排除式 Query；
- 不让 AI 创建媒体事实、稳定 ID 或最终 Prowlarr Query；
- 不在线学习评分权重；
- 不持久化媒体实体、候选、关系偏好或搜索历史；
- 不让 Preference Scoring 补偿错误作品或错误范围；
- 不让 renaming 重新决定作品身份。
