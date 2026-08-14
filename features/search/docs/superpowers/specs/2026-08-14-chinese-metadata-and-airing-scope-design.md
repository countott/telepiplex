# 简中元数据与播出中剧集范围设计

状态：方案已由用户确认，书面规格待用户复核。

## 1. 目标与边界

本设计解决 `/s` 搜索中的两个问题：

1. 简中主标题被 Wikipedia、Wikidata、TMDB 或 TVDB 的地区译名覆盖；
2. 正在播出的季度没有进入范围菜单，无法选择已经播出的单集搜索资源。

本阶段只记录设计，不修改实现。

## 2. 已批准：简中主标题契约

### 2.1 来源职责

- Wikipedia 是初始作品搜索、作品身份、Wikidata QID、英文名和原文名的
  主来源。
- 豆瓣是简中主标题的权威来源。
- Wikipedia、Wikidata、TMDB 和 TVDB 的中文标题只能作为搜索别名、
  交叉证据或诊断信息，不能覆盖豆瓣确认后的简中主标题。
- 同一 QID 下的 Wikipedia 结构化分集表负责用户可见的季集坐标和播出
  日期。TVDB 和 TMDB 是后置元数据补充者，并只在 Wikipedia 确实无法
  提供分集表时承担降级 inventory；这些职责不赋予它们简中主标题
  写入权。
- Prowlarr 资源搜索继续使用已确认的英文或原文标题，不受简中标题来源
  调整影响。

### 2.2 豆瓣分季标题

豆瓣分季条目必须拆成独立字段：

- `douban_title_raw`：豆瓣原始条目标题，例如 `副总统 第一季`；
- `chinese_title`：清洗后的根剧名，例如 `副总统`；
- `season_number`：独立保存的季度编号，例如 `1`；
- `douban_subject`：该分季条目的真实豆瓣 subject ID。

只清理剧集标题末尾明确且可验证的季度标记，例如 `第一季`、
`第 2 季`、`Season 2` 和 `S02`。`第二部`、`Part 2` 必须有季集结构
证据后才能清理。不得清理 `庆余年2`、`三体Ⅱ` 等可能属于作品名或
独立续作的数字。

清洗后的根标题不能作为合并豆瓣条目的唯一依据。每个豆瓣 subject ID
仍是独立来源事实；跨季归入同一根剧集必须另外验证作品身份和季度关系。

### 2.3 豆瓣与 Wikipedia 的跨源身份确认

IMDb 在这里仅作为跨来源稳定编号，不调用 IMDb API，也不增加 IMDb
API key。

身份确认顺序如下：

1. 从 Wikidata `P345` 读取 IMDb ID；
2. 如果 Wikidata 缺少 `P345`，使用现有 TMDB Read Access Token，按
   Wikidata ID 反查 TMDB，再读取 TMDB `external_ids.imdb_id`；
3. 从豆瓣条目详情读取其公开展示的 IMDb ID；
4. 两侧 IMDb ID 完全相同时，允许自动绑定；
5. 豆瓣没有 IMDb ID 时，只允许执行强交叉验证；
6. 只有标题相同属于弱匹配，不得自动绑定或覆盖简中主标题。

强交叉验证必须满足：

- 英文或原文根标题完全一致；
- 媒体类型一致；
- 候选唯一；
- 国家或原始语言、主创或主演、季度编号、季度首播年份中至少两项一致。

任一硬身份冲突都必须拒绝绑定。无法形成硬确认或强确认时，保留豆瓣
候选供用户确认，并记录 `douban_identity_unverified`，不得静默采用
豆瓣中文名。

### 2.4 写入与可追溯性

通过身份确认后：

- `identity.chinese_title` 使用清洗后的豆瓣根剧名；
- 豆瓣原始分季标题保留在来源事实和别名中；
- `field_sources` 必须记录豆瓣 subject ID 和原始值；
- 其他来源的地区中文译名保留为别名或冲突证据；
- 标题选择日志必须说明是否通过 IMDb 硬确认或多字段强确认。

## 3. 播出中剧集范围

### 3.1 数据流与来源顺序

播出中剧集不得改变现有的初始搜索顺序。完整数据流如下：

1. 使用现有 Wikipedia `action=query` 搜索候选并确认准确条目及 QID；
2. 通过跨源身份确认后，由豆瓣补充简中主标题；
3. 只对已经确认的电视剧条目读取 Wikipedia 结构化分集表；
4. 由 Wikipedia inventory 生成用户可见的季、集坐标和播出状态；
5. TVDB 和 TMDB 在作品确认后补充 episode ID、海报、演职员等元数据，
   并执行冲突诊断；
6. 用户选择范围后，Prowlarr 使用英文名或原文名搜索资源。

TVDB 和 TMDB 不参与初始作品裁决。Wikipedia 已完整覆盖季集结构时，
它们也不得改写用户看到的季号、集号、播出日期或菜单结构。

### 3.2 Wikipedia 分集表读取

确认 QID 后，使用 MediaWiki `action=parse` 读取准确页面渲染后的 HTML，
并至少保留 `page_id`、`revision_id`、语言和来源 URL。不得再次执行模糊
搜索，也不得通过正文正则或 AI 猜测季集结构。

读取顺序如下：

1. 优先读取准确的简体中文 Wikipedia 页面；
2. 中文页结构化表格不完整时，读取同一 QID 的英文页面补充；
3. 只接受能明确识别的剧集总览表、分季表和分集表字段；
4. 两个语言页面必须属于同一 QID，否则不得合并。

表格状态必须按可验证结果区分：

- `complete`：结构化表能够给出全部已知季度、常规剧集坐标，并能通过
  总集数、未来集或未知集判断是否存在缺口；
- `partial`：至少解析出一个有效季集事实，但不足以判断全部季度或总集数；
- `absent`：页面请求成功，但页面中没有受支持的结构化分集表；
- `parse_error`：已识别到受支持表格的结构特征，程序却无法生成有效
  来源事实。

`partial` 会触发同一 QID 英文页补充；`parse_error` 不得伪装成
`absent`。

典型搜索只增加一次准确页面请求；仅中文表不完整时增加第二次英文页
请求。请求发生在作品确认之后，不对搜索候选批量展开。解析结果可以按
`language + page_id + revision_id` 缓存，不需要新增 API key。

Wikipedia inventory 的每条常规剧集至少包含：

- `season_number`：季内坐标；
- `episode_number`：集内坐标；
- `overall_number`：存在时保留的全剧集号；
- `air_date`：ISO 日期或空值；
- `airing_state`：`aired`、`scheduled` 或 `unknown`；
- `source_language`、`source_url` 和 `revision_id`；
- 后置补充的 `tvdb_episode_id` 和 `tmdb_episode_id`。

标题、导演等字段可以随来源事实保留，但不作为生成范围菜单的必要条件。

### 3.3 播出状态和季度完整性

所有日期比较使用 telepiplex 当前配置时区中的当天日期：

- 有效播出日期早于或等于当天：`aired`；
- 有效播出日期晚于当天：`scheduled`；
- 日期缺失、无法解析或来源冲突：`unknown`。

当前实现把缺失日期视为已播的行为必须移除。尤其在正在播出的季度中，
未知日期不得被推断为已播。

季度状态按以下规则确定：

- 所有已知常规剧集均已播，且没有未来集、未知集或已知总集数缺口：
  `completed`；
- 存在未来集、未知集，或已知总集数大于已播集数：`incomplete`；
- 无法证明季度完整：`unknown`，其菜单权限按 `incomplete` 处理。

### 3.4 跨来源合并与降级

同一 QID 的中英文 Wikipedia 表格按字段合并：一侧有值而另一侧缺失时
可以补齐；季集坐标或日期发生硬冲突时记录 `wikipedia_fact_conflict`，
冲突剧集不得标为已播。

Wikipedia inventory 完整时：

- TVDB/TMDB 只能把唯一匹配的 episode ID 和附加元数据写入相同坐标；
- TVDB 的 alternate order 只能作为后台寻找匹配 episode ID 的手段；
- alternate order 不得反向改变 Wikipedia 的季集坐标；
- TVDB/TMDB 的冲突只进入诊断信息，不得否决 Wikipedia 已确认的菜单。

只有以下情况允许使用 TVDB/TMDB inventory 降级：

1. 准确的中英文 Wikipedia 页面确实没有可识别的结构化分集表；
2. Wikipedia 请求超时、限流或服务不可用；
3. 页面存在表格，但解析器发生未预期错误。

第三种情况属于程序缺陷候选，不属于正常的“Wikipedia 无数据”。线上流程
可以降级以避免阻塞用户，但必须保留 `wikipedia_parse_error` 高优先级
诊断；不得因为 TVDB/TMDB 降级成功而把这次搜索记录成正常成功。

降级时同时读取 TVDB 和 TMDB：

- 两者季集坐标和日期一致时合并采用；
- 只有一个来源可用时，可以采用并记录单来源降级；
- 两者发生硬冲突时，不得用 AI 猜测；冲突剧集按 `unknown` 处理；
- 降级 inventory 必须记录 `inventory_source=tvdb`、`tmdb` 或
  `tvdb_tmdb`。

### 3.5 范围菜单

如果所有季度均已完结，保留“全剧”和按季搜索入口。如果存在任一
`incomplete` 或 `unknown` 季度：

- 隐藏“全剧”；
- 已完结季度显示为可直接搜索的“第 N 季（全季）”；
- 未完结季度显示“第 N 季（已播 X/Y）”，点击后进入第三级单集菜单；
- 总集数未知时显示“第 N 季（已播 X 集）”；
- 第三级菜单只显示 `aired` 剧集；
- `scheduled` 和 `unknown` 剧集不得出现为可搜索按钮；
- 只有一个季度但仍在播出的剧集同样进入单集菜单，不能折叠成“全剧”。

以 2026-08-14 的《百年孤独》为验收样例：

- 第 1 季显示“全季”；
- 第 2 季显示“已播 7/8”；
- 进入第 2 季后只显示第 1 至第 7 集；
- 尚未播出的第 8 集不显示；
- 顶层不显示“全剧”。

## 4. 组件边界

实现保持四个可独立测试的职责：

1. Wikipedia HTTP 适配器只负责准确页面请求、状态码和修订信息；
2. 纯解析器把固定 HTML 输入转换为来源事实，不访问网络；
3. inventory 协调器负责 QID 校验、中英文合并、TVDB/TMDB 后置增强和
   降级状态；
4. 范围菜单只消费规范化 inventory，不自行判断来源优先级。

解析器应保持独立，避免继续把 HTML 结构识别、网络请求和 Telegram
菜单逻辑堆进现有服务文件。

## 5. 错误与可观测性

每次电视剧范围解析至少记录：

- Wikipedia 页面、语言、QID、page ID 和 revision ID；
- Wikipedia 表格状态：`complete`、`partial`、`absent` 或
  `parse_error`；
- 最终 inventory 来源和是否发生降级；
- 已播、未来、未知和冲突集数；
- TVDB/TMDB 是否只做增强，或承担了降级 inventory；
- 菜单隐藏“全剧”以及隐藏未来剧集的原因。

必须区分以下错误：

- `wikipedia_table_absent`：页面成功读取，但确实无结构化分集表；
- `wikipedia_unavailable`：请求失败、限流或服务不可用；
- `wikipedia_parse_error`：存在可识别表格但程序未能解析；
- `wikipedia_fact_conflict`：同一 QID 的结构化事实冲突；
- `series_inventory_fallback`：最终使用 TVDB/TMDB inventory。

## 6. 测试与验收

单元测试不得依赖实时 Wikipedia 页面。应保存带 revision ID 的固定
MediaWiki API/HTML 夹具，覆盖：

1. 中文页独立提供完整分集表；
2. 中文页不完整、同一 QID 英文页补足总集数和未来集；
3. 《百年孤独》在固定日期下得到第 1 季完结、第 2 季已播 7/8；
4. Wikipedia 为两季、TVDB 默认顺序错误地给出一季时，菜单仍采用
   Wikipedia 的两季坐标；
5. Wikipedia 完整时，TVDB/TMDB 只能补 ID，不能覆盖坐标或日期；
6. Wikipedia 确实无表格时，TVDB/TMDB 可以降级；
7. 标准分集表存在但解析器返回空或报错时，测试必须失败；即使编排层
   成功降级，也不能把解析器回归判为通过；
8. 缺失日期在播出中季度内是 `unknown`，不是 `aired`；
9. 单季播出中、单季已完结、多季含未完结季度的菜单分支；
10. 中英文 Wikipedia 日期冲突和 TVDB/TMDB 降级冲突。

简中元数据回归测试至少覆盖：

- 《副总统》不得被 Wikipedia 地区译名“副人之仁”覆盖；
- 《百年孤独》不得被其他来源的“百年孤寂”覆盖；
- 豆瓣分季标题清理后仍保留原始 subject ID 和原始标题；
- IMDb 只作为已有页面中的桥接 ID，不要求 IMDb API 或 API key；
- 只有标题相同不得自动绑定豆瓣条目。

可以另设不阻塞常规单元测试的实时 Wikipedia 契约测试，用于发现模板
变化。实时测试失败时必须先区分页面修订变化、网络失败和解析器缺陷；
不得未经调查就把失败归因于 Wikipedia 通常无法覆盖。

本规格获用户书面复核后再编写实施计划；在此之前不修改实现代码。
