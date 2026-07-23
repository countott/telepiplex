# Telepiplex AI 工具编排式媒体来源解析设计

日期：2026-07-20

状态：已完成会话设计确认，等待书面规格复核

主责模块：`feature/search`

## 1. 文档效力

本设计调整普通文本媒体搜索在 Prowlarr 之前的意图解析与来源查询链路。

与下列文档冲突时，本设计优先适用于“普通文本如何触发 AI、Wikipedia、豆瓣和 TVDB”：

- `2026-07-16-bounded-search-design.md`
- `2026-07-16-canonical-media-entity-scoring-design.md`
- `2026-07-17-scope-aware-prowlarr-release-filtering-design.md`

以下既有边界继续有效：

- 豆瓣、TVDB 直达链接由程序锁定稳定 ID；
- 来源事实、候选和 AI 结果只在当前请求内存在；
- 用户确认具体作品后才能建立 Prowlarr 搜索任务；
- 最终 Prowlarr Query 由确定性程序生成；
- Prowlarr 发布结果正确性门禁和 Preference Scoring 不交给 AI；
- `media_metadata` 只能由程序根据已验证事实生成。

本设计不修改 `release_gate.py` 的发布名语法与范围分类规则。发布门禁的双语标题、`Season 1` 和整季集数范围兼容性属于独立后续设计。

## 2. 背景与问题

search 1.4.0 的运行日志显示，多次普通片名查询已经从 Wikipedia、豆瓣和 TVDB 取得事实，但在来源事实合并、合格候选过滤或 AI 评分前变成空候选。AI 标题纠错只在早期零候选路径触发，无法覆盖“来源有结果、合格候选归零”的情况。

当前链路还存在以下结构性限制：

1. 同一个规则清洗结果被投影到多个来源，跨语言标题和来源专用查询不足；
2. AI 在来源查询外部运行，只能接收业务代码筛选后的候选；
3. AI 评分器收到空候选时不能重新发起来源查询；
4. Prompt 是主要行为约束，缺少工具权限、调用轮次和事实引用的硬限制；
5. 当前 `chat_completion()` 没有 `tools`、`tool_choice` 或工具调用循环；
6. 豆瓣无 Key 接口具有反爬、页面结构变化和临时不可用风险，不能成为单点依赖。

## 3. 目标

普通文本查询采用 AI 工具编排：

```text
用户普通文本
  -> AI 理解意图并发起首轮工具调用
  -> Wikipedia（中/英）+ 豆瓣 + TVDB 固定并行轻查
  -> AI 审阅规范化证据
  -> 0–2 轮自主定向深查
  -> 程序验证 AI 的事实引用与候选关联
  -> 展示 1–7 个候选供用户确认
  -> 程序生成 Prowlarr Query
```

具体目标：

- AI 成为普通文本的意图解释和证据关联层；
- Wikipedia、豆瓣、TVDB 通过受控工具直接向 AI 提供事实；
- 首轮始终覆盖三源，不让模型漏掉基础来源；
- 后续查询由 AI 根据首轮证据自主决定；
- AI 可以提出错别字、简称、跨语言别名和同实体关联；
- AI 不接触凭据，不直接访问任意 URL，不创建来源事实；
- AI 越界、不可用或工具协议不兼容时确定性降级；
- 每轮查询、停止原因和验证结果可以从日志复盘。

## 4. 非目标

- 不让 AI 自由浏览互联网；
- 不把 TVDB API Key、Subscriber PIN、Bearer Token 或其他凭据发送给模型；
- 不让 AI 直接生成稳定 ID、官方标题、年份、TVDB inventory 或海报；
- 不让 AI 自动选择同名候选；
- 不引入长期媒体实体数据库或用户搜索历史；
- 不让 AI 生成最终 Prowlarr Query；
- 不在本设计中修改 Prowlarr 发布门禁语法；
- 不通过模型在线学习或动态调整候选评分权重。

## 5. 核心原则

1. Prompt 约束 AI 的思考方式，代码约束 AI 实际能做什么。
2. 首轮三源查询是状态机要求，不依赖模型自行判断是否需要。
3. AI 只能引用本次工具结果中的 `fact_id`，以及程序在当前请求图中
   分配的 `candidate_key`。
4. AI 可以建立“语义等价假设”，但不能改写来源字段。
5. 普通文本最终仍要求至少两路独立来源支持；直达链接可以单源锁定身份。
6. 用户确认是同名、多版本和 AI 语义关联的最终选择边界。
7. 来源失败与作品不存在必须分别报告。
8. 豆瓣属于高价值 `best_effort` 来源，不是单点依赖。
9. 所有调用有轮次、数量、超时、缓存和熔断上限。

## 6. 组件边界

### 6.1 Input Router

负责把输入分成：

- 豆瓣直达链接；
- TVDB 直达链接；
- 结构合法的普通文本；
- 明确不支持的范围或 Special 输入；
- 无效链接。

豆瓣、TVDB 直达链接继续走来源专用确定性解析。AI 可以在链接锁定身份后解释补充事实，但不能改变链接锁定的稳定 ID。

### 6.2 AI Source Orchestrator

负责：

- 向模型提供 System Prompt 和工具 Schema；
- 强制普通文本首个工具为 `search_media_sources`；
- 执行并记录工具调用；
- 把规范化、裁剪后的工具结果回送模型；
- 允许最多两轮定向深查；
- 校验模型最终 JSON 的外层结构；
- 把 AI 判断交给 Evidence Verifier。

Orchestrator 不读取来源网页语义，不生成媒体契约。

### 6.3 Source Tool Gateway

负责：

- 接收已校验的工具参数；
- 从 `runtime_context.config` 读取来源配置；
- 并行执行来源适配器；
- 处理凭据、HTTP Header、登录 Token、超时和重试；
- 规范化来源状态和事实；
- 去除不应进入模型上下文的页面正文、Header 和凭据；
- 对豆瓣执行缓存、并发限制和熔断。

### 6.4 Source Adapters

保留来源专用适配器：

- Wikipedia：公开 MediaWiki API，中英文语言投影；
- 豆瓣：无 Key 搜索页、Subject Abstract 和 Rexxar 降级；
- TVDB：使用运行时 API Key 登录并缓存 Token。

适配器不接受 AI 提供的凭据、Base URL 或任意请求 Header。

### 6.5 Evidence Verifier

负责：

- 验证所有 `fact_id` 来自当前 operation；
- 验证所有 `candidate_key` 来自当前请求图；
- 拒绝 AI 添加或修改稳定 ID；
- 拒绝年份、类型和稳定 ID 的硬冲突；
- 验证语义等价边只连接现有事实；
- 生成可展示候选；
- 根据验证结果选择确认、澄清或失败路径。

### 6.6 Candidate and Prowlarr Handoff

通过验证的 1–7 个候选继续使用现有海报确认流程。用户确认后：

- 程序生成 confirmed `media_metadata`；
- 程序根据规范拉丁标题和范围生成 Prowlarr Query；
- Prowlarr 原始结果继续进入独立正确性门禁和质量评分。

## 7. AI 工具合同

### 7.1 首轮工具

工具名：

```text
search_media_sources
```

输入：

```json
{
  "intent": {
    "raw_query": "string",
    "title_hints": ["string"],
    "media_type_hint": "movie|series|unknown",
    "year_hint": "string",
    "scope": "work|whole_series|season|episode|unknown",
    "season_number": null,
    "episode_number": null
  },
  "source_queries": {
    "wikipedia_zh": ["string"],
    "wikipedia_en": ["string"],
    "douban": ["string"],
    "tvdb": ["string"]
  }
}
```

限制：

- `raw_query` 由 Orchestrator 注入，模型不能改写；
- 每个来源最多 3 个 Query；
- Query 最长 160 个 Unicode 字符；
- 不接受 URL、Header、API Key、Token、Base URL；
- 首轮固定并行尝试 Wikipedia、豆瓣和 TVDB；
- 来源关闭时返回 `disabled`，所需凭据缺失时返回
  `credential_missing`；来源槽位始终保留。

输出：

```json
{
  "round": 1,
  "sources": [
    {
      "source": "wikipedia",
      "status": "ok|not_found|disabled|credential_missing|authentication_failed|timeout|rate_limited|blocked|server_down",
      "query_summaries": ["string"],
      "facts": [
        {
          "fact_id": "wikipedia:...",
          "titles": ["string"],
          "year": "string",
          "media_type": "movie|series|unknown",
          "external_ids": {},
          "source_url": "string",
          "poster_url": "string",
          "aliases": ["string"],
          "relation_signals": ["string"]
        }
      ],
      "error_code": "string",
      "credential_state": "not_required|configured|missing"
    }
  ]
}
```

模型不接收完整 Wikipedia Extract、豆瓣 HTML、TVDB HTTP 响应或异常堆栈。

### 7.2 定向深查工具

允许的工具：

```text
lookup_wikipedia_entity
lookup_douban_subject
lookup_tvdb_entity
lookup_tvdb_episodes
```

通用限制：

- 参数只能引用首轮已返回的 `fact_id`、稳定 ID 或规范化标题；
- 不接受任意 URL；
- 每轮每个来源最多一个批量工具调用；
- 每个批量调用最多 3 个目标；
- 最多两轮定向深查；
- 两轮合计最多 6 个来源工具调用；
- TVDB Episode inventory 只在 series 候选和范围判断需要时允许；
- 豆瓣在熔断状态时直接返回 `blocked`，AI 不得立即重试。

## 8. System Prompt 合同

普通文本 Orchestrator 使用独立 System Prompt，不再把全部规则拼接到单条 user message。

Prompt 必须明确：

```text
你是媒体来源查询编排器。
首个动作必须调用 search_media_sources。
你只能根据本次工具返回的 fact_id 和字段判断。
你可以提出标题纠错、简称、跨语言别名和同实体关联假设。
不得凭自身知识生成稳定 ID、官方标题、年份、海报或 TVDB inventory。
不得请求任意 URL、Header、API Key、Token 或 Base URL。
存在多个合格候选时不得自动选择。
证据充分、达到查询上限或继续查询不会增加可验证信息时必须停止。
最终只能返回规定 JSON。
```

Prompt 是行为指导，不承担硬安全保证。工具 Schema、状态机和 Evidence Verifier 必须能独立拒绝所有越界输出。

## 9. 编排状态机

```text
received
  -> first_tool_required
  -> first_round_running
  -> evidence_review
       -> targeted_round_1
       -> targeted_round_2
  -> verifying
       -> awaiting_candidate_confirmation
       -> clarification_required
       -> failed
```

规则：

1. 普通文本首个模型动作不是 `search_media_sources`：
   - Orchestrator 返回一次协议纠正消息；
   - 第二次仍不合规则返回 `tool_protocol_invalid` 并走确定性降级。
2. 首轮工具成功后，AI 可以直接结束或请求定向深查。
3. 第二轮深查结束后禁止新的工具调用。
4. 超出每轮或总调用数量返回 `tool_budget_exceeded`。
5. operation 取消后不再启动新工具；已返回结果不再进入验证。
6. 每次模型与工具调用都受独立超时和总 operation 截止时间约束。

## 10. 事实关联与候选形成

首轮和定向深查结束后，程序先把确定性合并组件与尚未合并的单条事实
分别放入临时候选图，并为每个节点分配只在当前 operation 有效的
`candidate_key`。模型只能评估这些既有临时候选，并通过 fact ID 提议
节点之间的语义等价边。Verifier 接受或拒绝等价边后，由程序重新生成
最终展示候选及其最终 key；模型不能预先命名合并后的候选。

### 10.1 确定性合并

来源事实满足以下任一条件时确定性合并：

- 稳定 ID 或权威跨库链接明确对齐；
- 已验证官方别名、年份和媒体类型共同一致。

### 10.2 AI 语义等价边

AI 可以提出：

```json
{
  "left_fact_id": "douban:...",
  "right_fact_id": "tvdb:...",
  "relation": "same_entity",
  "reason": "string"
}
```

程序只在以下条件全部满足时接受为候选关联边：

- 两个 fact ID 均来自当前 operation；
- 来自不同独立来源；
- 年份不存在硬冲突；
- media type 不存在硬冲突；
- 稳定 ID 不存在硬冲突；
- AI 没有修改任一来源字段。

AI 语义等价边只用于把跨语言事实组成一个“待用户确认候选”，不把 AI 判断写回来源事实，也不自动跳过用户确认。

### 10.3 普通文本合格候选

普通文本候选必须：

- 至少两路独立来源支持；
- 能确定 movie 或 series；
- series 必须取得 TVDB Series ID；
- 能取得来源支持的规范拉丁标题；
- 年份、类型和稳定 ID 不存在硬冲突。

只有一个非直达来源支持时，系统要求用户补充年份、媒体类型或条目链接。

## 11. AI 最终输出

```json
{
  "status": "resolved|ambiguous|insufficient_evidence",
  "intent": {
    "title_hints": ["string"],
    "media_type_hint": "movie|series|unknown",
    "year_hint": "string",
    "scope": "work|whole_series|season|episode|unknown",
    "season_number": null,
    "episode_number": null
  },
  "equivalence_edges": [
    {
      "left_fact_id": "string",
      "right_fact_id": "string",
      "relation": "same_entity",
      "reason": "string"
    }
  ],
  "candidate_assessments": [
    {
      "candidate_key": "string",
      "supporting_fact_ids": ["string"],
      "conflicting_fact_ids": ["string"],
      "reason": "string"
    }
  ],
  "recommended_next_action": "confirm|clarify|stop"
}
```

Verifier 拒绝：

- 未知字段；
- 未知 `fact_id` 或 `candidate_key`；
- AI 返回的新稳定 ID；
- AI 修改候选标题、年份或类型；
- 临时候选评估数量与当前请求图不一致；
- 同一事实同时作为互斥候选的唯一支持；
- 超过工具轮次后要求继续查询。

## 12. 停止条件

### 12.1 确认成功

- 普通文本至少两路来源支持同一实体；
- 标题、年份、类型和稳定 ID 无硬冲突；
- series 已取得 TVDB Series ID；
- 候选数量为 1–7。

候选仍进入用户确认，不由 AI 自动选择。

### 12.2 要求澄清

- 合格候选超过 7 个；
- 同名候选无法通过年份或类型区分；
- 只有一个非直达来源支持；
- 裸数字角色无法验证；
- AI 判断继续查询不会增加可验证信息。

### 12.3 明确失败

- 两轮深查后没有合格实体；
- 来源事实存在不可消解的稳定 ID、年份或类型冲突；
- 所有来源不可用；
- AI 工具协议持续无效且确定性降级也无法完成。

## 13. 来源可用性和降级

### 13.1 Wikipedia

- 无 Key；
- 首轮固定查询中文和英文 MediaWiki；
- 单语言失败不阻断另一语言；
- 两种语言都失败时返回 `server_down` 或 `timeout`。

### 13.2 豆瓣

- 无 Key；
- 首轮固定尝试；
- 定位为 `best_effort` 多语言校对来源；
- 增加短期 Query/Subject 缓存；
- 限制并发和连续请求频率；
- 对 403、429、结构异常和连续失败执行短期熔断；
- 熔断期间返回 `blocked`，不阻断 Wikipedia + TVDB 链路。

新增配置：

```yaml
metadata:
  douban:
    enable: true
    timeout: 10
    cache_ttl: 900
    max_concurrency: 2
    circuit_breaker_failures: 3
    circuit_breaker_seconds: 300
```

### 13.3 TVDB

- 使用现有 `metadata.tvdb` 配置；
- API Key、Subscriber PIN 和 Bearer Token 只存在于服务端；
- AI 只看到 `credential_state=configured|missing`；
- 来源关闭时返回 `disabled`，已启用但 API Key 缺失时返回
  `credential_missing`；
- 已配置凭据但 TVDB 登录或 Token 刷新被拒绝时返回
  `authentication_failed`，不得降格为 `not_found`；
- 电影可以由其他两源形成候选；
- series 缺少 TVDB Series ID 时不得形成最终合格候选。

### 13.4 AI 不可用或不支持工具

以下路径仍可运行：

- 豆瓣/TVDB 直达链接；
- input contract 支持的标准片名、年份、Movie/Series、Sxx、SxxExx；
- 程序固定三源查询和确定性合并。

复杂错别字、口语简称和跨语言语义关联无法完成时提示：

```text
AI 来源编排暂不可用，请提供更完整片名、年份、Movie/Series 类型或豆瓣/TVDB链接。
```

不把 AI 不可用误报为“作品不存在”。

## 14. 凭据与安全

1. 工具输入 Schema 不包含凭据字段。
2. Source Tool Gateway 从 `runtime_context.config` 读取凭据。
3. TVDB 登录和 Token 缓存继续由 TVDB adapter 管理。
4. AI 不能提供或覆盖来源 Base URL。
5. 工具结果不包含 Header、Cookie、API Key、PIN、Bearer Token 或完整异常堆栈。
6. API Key 配置字段继续标记为 `writeOnly`。
7. 日志清洗器必须在结构化日志写入前递归遮蔽敏感字段。
8. Wikipedia Extract、豆瓣 HTML 和外部文本按字段白名单裁剪，避免把页面内容当成工具指令。
9. 模型请求只接收结构化事实，不接收未经处理的网页正文。

## 15. 日志与可观测性

每个 operation 记录：

- `operation_id`；
- 原始 Query 的脱敏摘要；
- AI 工具协议版本；
- 每轮允许和实际调用的工具；
- 每个来源的 Query 摘要、耗时、状态和事实数量；
- 豆瓣缓存命中、限流和熔断状态；
- TVDB 凭据仅记录 `configured|missing`，认证结果另记来源状态；
- AI 请求定向深查的结构化理由；
- AI 停止原因；
- equivalence edge 数量及验证结果；
- 合格、冲突、单源和被拒绝候选数量；
- 最终 `confirm|clarify|stop`；
- AI 或工具降级原因。

禁止记录：

- API Key、PIN、Token、Cookie、Authorization Header；
- 完整 Wikipedia Extract、豆瓣 HTML、TVDB 原始响应；
- 完整 Prompt；
- 完整 magnet 或下载 URL。

## 16. 错误语义

需要区分：

```text
ai_unavailable
tooling_unsupported
tool_protocol_invalid
tool_budget_exceeded
source_timeout
source_rate_limited
source_blocked
source_server_down
credential_missing
source_authentication_failed
not_found
insufficient_independent_support
hard_fact_conflict
clarification_required
```

`not_found` 只表示至少一个来源成功完成请求但没有匹配事实。所有来源失败时必须报告来源不可用。

## 17. 配置与兼容性

新增 AI 编排配置：

```yaml
ai:
  source_orchestration:
    enable: true
    max_targeted_rounds: 2
    max_tools_per_round: 3
    protocol: openai_tools_v1
```

规则：

- `max_targeted_rounds`发布值固定为 2，Schema 不允许超过 2；
- `max_tools_per_round`只约束定向深查轮，发布值固定为 3，Schema
  不允许超过 3；首轮统一工具内部的三源并行不计作三次模型工具调用；
- OpenAI-compatible endpoint/model 不支持工具调用时自动进入确定性降级；
- 不在同一次 operation 内从工具模式切换到另一种厂商私有工具协议；
- 旧配置没有 `source_orchestration` 时按启用处理，但必须通过启动能力检查；
- 能力检查失败只禁用 AI 工具编排，不禁用直达链接和确定性来源查询。

## 18. 测试与验收

### 18.1 Prompt 和工具协议

- 首个普通文本模型动作必须调用 `search_media_sources`；
- AI 尝试调用未注册工具时拒绝；
- AI 尝试传入 URL、API Key、Header 或 Base URL 时拒绝；
- AI 第二次违反首轮协议后进入确定性降级；
- 第三轮深查和超额工具调用被状态机拒绝；
- AI 最终 JSON 未知字段、未知 fact ID 和未知 candidate key 被拒绝。

### 18.2 来源工具

- 首轮固定并行包含 Wikipedia 中/英、豆瓣和 TVDB；
- Wikipedia 不需要 Key；
- 豆瓣不需要 Key 且支持缓存、限流和熔断；
- TVDB 使用运行时配置但模型上下文和日志没有凭据；
- 单来源超时不取消其他来源；
- 所有来源失败与正常零结果使用不同错误语义。

### 18.3 候选和验证

- 稳定 ID 明确对齐时确定性合并；
- 中文标题和英文标题不同但年份、类型一致时，AI 可以引用两个现有 fact ID 建立待确认语义等价边；
- AI 语义边遇到年份、类型或稳定 ID 冲突时被拒绝；
- 普通文本只有一个非直达来源时要求澄清；
- series 没有 TVDB Series ID 时不能成为最终合格候选；
- 1–7 个合格候选全部展示；
- 超过 7 个要求补充信息；
- AI 不可用时标准片名和直达链接仍可运行。

### 18.4 日志回放用例

以下输入必须进入候选确认或明确的多候选澄清，不能因为空 AI 评分列表静默失败：

```text
蝙蝠侠：谍影之谜
蝙蝠侠：黑暗骑士
蝙蝠侠黑暗骑士
蜂蜜与四叶草
布达佩斯大饭店
```

还必须覆盖：

- 同名 movie/series；
- 同名不同年份电影；
- Wikipedia 单独不可用；
- 豆瓣 403、429 和结构变化；
- TVDB Key 缺失、登录失败和 API 超时；
- TVDB 凭据已配置但认证失败必须返回 `source_authentication_failed`；
- AI 工具调用不兼容；
- AI 伪造稳定 ID、fact ID 或候选；
- operation 在首轮和深查轮取消。

### 18.5 Prowlarr 边界

- AI 最终输出不能包含 `prowlarr_query`；
- confirmed 候选仍由 `build_prowlarr_query()` 生成最终 Query；
- 本设计不改变现有发布门禁测试预期；
- 发布门禁兼容性修复需要独立失败用例和后续设计。

## 19. 实施顺序

1. 增加工具协议类型、状态机和结构化日志；
2. 把 Wikipedia、豆瓣、TVDB adapter 包装为 Source Tool Gateway；
3. 增加豆瓣配置、缓存、限流和熔断；
4. 实现首轮固定三源工具；
5. 实现四个定向深查工具；
6. 增加 System Prompt 和 OpenAI-compatible tool-call loop；
7. 实现 Evidence Verifier 和 AI 语义等价边；
8. 接入现有候选确认和 Prowlarr handoff；
9. 实现 AI/tooling 不可用时的确定性降级；
10. 增加日志回放、来源失败和安全越界测试；
11. 更新 README、配置默认值和配置 Schema；
12. 通过验证后单独评估发布门禁兼容性设计。

## 20. 验收结论

实现完成后，普通文本搜索的事实责任边界应为：

```text
AI：理解意图、选择后续查询、解释和关联现有证据
Source Tool Gateway：持有配置、调用来源、清洗和返回事实
Evidence Verifier：强制事实引用、冲突和候选边界
用户：确认同名、多版本和 AI 语义关联候选
确定性程序：生成 media_metadata、Prowlarr Query 和发布门禁结果
```

任何一层失败都必须返回可区分、可复盘的状态，不再以空候选或“没有资源”掩盖真实失败阶段。
