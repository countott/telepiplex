# Telepiplex Media Search 规则优先规划设计

日期：2026-07-13

状态：已确认，等待实施计划

目标分支：`feature/search`

对应业务决策：TODO-01、TODO-02 的搜索阶段

## 1. 背景

当前 `search` 每次搜索都强制执行两阶段 AI：第一阶段生成搜索假设和证据查询，第二阶段根据 Wikipedia、豆瓣和 TVDB 证据生成 `media_metadata` 草案。任一 AI 阶段不可用都会令搜索失败。

本轮改为严格的“规则优先、AI 处理歧义”：证据足以唯一确认普通媒体时不调用 AI；只有规则门槛未通过时才让 AI 入场。AI 一旦入场，可以完整分析媒体身份、关系和范围，不人为限制其推理权限。AI 或规则生成的结果仍受同一 contract 校验和用户确认约束。

当前 worktree 还存在两个需要在本轮补齐的事实差异：豆瓣 provider 仍是 `disabled` 占位，`config.default.yaml` 中 TVDB 默认关闭。

## 2. 范围

### 2.1 本轮包含

- 为普通电影、整剧、整季和明确单集建立可解释的确定性规划路径。
- 增加无需 Key 的豆瓣证据 provider。
- 保留 Wikipedia provider，并令 Wikipedia、TVDB 和 AI 的默认启用状态符合已确认配置方向。
- 在确定性门槛未通过时执行两阶段 AI。
- 统一规则路径和 AI 路径的 `media_metadata v1` 输出、校验、确认和后续 Prowlarr 行为。
- 为放行、阻断和 AI 入场记录可解释证据与用户可见原因。

### 2.2 本轮不包含

- 不修改 Host API、capability 或事件协议。
- 不修改 `download`、`rename` 或 `sync`。
- 不处理下载后的盲盒文件树或无 context magnet；这些场景由后续 `rename` 设计处理。
- 不改变用户确认后的 canonical contract 权威性。
- 不合并、不推送，也不修改其他 worktree。

## 3. 核心原则

1. **确定性优先**：只有能够逐项解释并由多源证据验证的普通媒体才能绕过 AI。
2. **AI 按需入场**：高置信度普通媒体不调用 AI；歧义、复杂关系和证据不足才调用 AI。
3. **AI 不受推理范围限制**：AI 可以提出电影、剧集、关联关系、OVA、Special 等完整假设，但其草案必须通过证据一致性、schema 校验和用户确认。
4. **低置信度不静默降级**：规则失败且 AI 不可用或无效时，搜索明确阻断。
5. **模块隔离**：所有改动留在 `feature/search`，不得依赖 sibling Feature 包。

## 4. 目标数据流

### 4.1 第一遍：确定性规划

1. 使用现有规则解析原始输入，取得标题、年份、媒体类型倾向、整剧/整季/单集范围和季集编号。
2. 确定性查询生成器为 Wikipedia、豆瓣和 TVDB 构造第一遍查询，不依赖 AI。
3. 三个 provider 并发取证；单个 provider 的失败转换为该来源的结构化状态。
4. 归一化候选的标题、年份、媒体类型、外部 ID、来源 URL 和季集信息。
5. 严格确定性门槛评估候选集合。
6. 门槛通过时，由确定性 plan builder 生成 `media_metadata v1` 草案，进入现有 finalize 和用户确认流程；整个路径不得调用 AI。

### 4.2 第二遍：AI 处理未决场景

1. 第一遍门槛未通过时，将原始输入、规则解析结果、第一遍证据和明确的失败原因交给第一阶段 AI。
2. 第一阶段 AI 生成扩展假设及 Wikipedia、豆瓣、TVDB 查询，但不得生成 Prowlarr query。
3. 对扩展查询进行第二遍取证，与第一遍证据归一化、去重并合并。
4. 第二阶段 AI 根据完整上下文生成 `media_metadata v1` 草案和英文或原始 Prowlarr 查询依据。
5. 草案经过现有来源真实性检查、contract 校验、finalize 和用户确认。
6. 任一 AI 阶段不可用、返回无效结果或最终校验失败时阻断，不回退为低置信度规则计划。

## 5. Provider 设计

### 5.1 Wikipedia

- 默认启用，继续支持 `zh`、`en` 查询。
- 返回实际来源 URL、可用的 Wikibase ID、标题、年份和媒体类型事实。
- 禁用、未找到和网络失败分别记录为 `disabled`、`not_found` 和 `server_down`。

### 5.2 豆瓣

- 作为无需 Key 的常驻证据源，不新增虚假的 API Key 配置或关闭开关。
- adapter 负责查询、解析豆瓣 subject 身份，并返回实际 subject URL、subject ID、中文名、英文或原始名、年份和媒体类型事实。
- 页面或接口形态差异由 adapter 内部吸收，对 planner 只暴露统一 provider 结果。
- 未找到和网络失败分别返回 `not_found` 与 `server_down`，不得抛出导致整个搜索中止的未处理异常。

### 5.3 TVDB

- `config.default.yaml` 中改为 `enable: true`。
- 缺少 API Key 时返回 `disabled`，不伪装成网络错误。
- 为剧集确定性路径提供唯一 series ID、英文名、年份、季列表及可验证的单集身份。
- TVDB 不可用时，电影仍可由 Wikipedia 与豆瓣满足双来源门槛；精确剧集范围不得确定性放行。

## 6. 严格确定性门槛

### 6.1 普通电影

以下条件必须全部满足：

- 至少两个独立 provider 支持同一候选。
- 标准化标题集合存在交集。
- 年份一致，且不存在同标题的其他年份候选。
- provider 对媒体类型均指向普通电影。
- 合并后的候选集合只有一个符合项。
- 取得包含拉丁字符的英文或原始标题，可用于 Prowlarr 查询。
- 不存在前传、续集、关联电影、OVA、Special、spin-off 或其他关系信号。

### 6.2 普通剧集

以下条件必须全部满足：

- TVDB 返回唯一 series ID。
- Wikipedia 或豆瓣至少一个来源在标准化标题、年份和剧集类型上与 TVDB 交叉验证。
- 不存在同标题不同年份、电影/剧集冲突或多个 TVDB series 候选。
- 取得包含拉丁字符的英文或原始剧名。

范围还必须满足：

- **整剧**：用户显式输入整剧含义，或输入未指定范围但多源证据唯一指向一个普通剧集。
- **整季**：用户输入包含明确 season number，且 TVDB 剧集数据证明该季存在。
- **单集**：用户输入包含明确 season number 和 episode number，且 TVDB 证明该集存在。

### 6.3 必须转交 AI 的情况

- 同名不同年份、同名电影与剧集、多个候选并列。
- 年份、媒体类型、标题身份或外部 ID 冲突。
- 只有单一来源支持。
- 精确剧集范围缺少 TVDB 验证。
- 关联电影、前传、续集、OVA、Special、花絮或其他复杂关系。
- 无法取得可靠的英文或原始 Prowlarr 查询标题。
- 任何无法逐条解释为唯一普通媒体的场景。

### 6.4 媒体库分类

- 任一可信 provider 明确给出动画、Animation 或 anime 信号时，选择对应的 `animated_movie` 或 `animated_series`。
- provider 对动画属性存在冲突时，不得确定性放行，转交 AI。
- 没有动画信号且其他唯一性门槛全部通过时，按普通真人媒体选择对应的 `live_action_movie` 或 `live_action_series`，并在 decision evidence 中记录 `default_live_action_without_animation_signal`。

## 7. Plan 与证据记录

规则路径和 AI 路径都输出 `media_metadata v1`，不增加新的 schema version。`evidence` 中增加向后兼容的解释字段：

```json
{
  "decision": {
    "mode": "deterministic|ai",
    "gate_status": "passed|failed",
    "media_class": "movie|series",
    "matched_providers": ["wikipedia", "douban", "tvdb"],
    "candidate_count": 1,
    "reason_codes": ["unique_cross_source_identity"],
    "ai_required": false,
    "ai_stage_one_status": "not_needed|ok|unavailable|invalid",
    "ai_stage_two_status": "not_needed|ok|unavailable|invalid"
  }
}
```

- 确定性路径的 `source_entry` 必须引用 provider 实际返回的 URL 或稳定 ID，不能使用 `ai_supplied_unverified`。
- AI 路径保留第一遍门槛失败原因，使用户能看出 AI 为什么入场。
- 新字段为附加信息，不改变现有消费者必须读取的 contract 字段。

## 8. 错误与用户体验

规则门槛失败且 AI 无法完成时，planner 返回结构化原因，并由 Telegram 入口显示具体说明。至少区分：

- `ambiguous_candidates`：存在多个可行候选。
- `evidence_conflict`：来源在年份、类型或身份上冲突。
- `insufficient_independent_support`：只有一个来源支持。
- `missing_original_query_title`：缺少可靠英文或原始标题。
- `tvdb_identity_required`：剧集缺少唯一 TVDB series ID。
- `tvdb_scope_not_verified`：TVDB 无法验证请求的季或集。
- `complex_identity_requires_ai`：场景包含复杂媒体关系。
- `ai_unavailable_after_gate_failure`：规则未通过且 AI 不可用。
- `ai_invalid_after_gate_failure`：AI 返回无效或无法通过校验的草案。

阻断时不得保存可确认计划、占用临时 S00 编号、查询 Prowlarr 或提交下载。日志记录 plan ID、provider 状态、reason code 和 AI 阶段状态，但不得包含 AI Key、TVDB Key 或未经清洗的敏感响应。

## 9. 配置行为

- `metadata.wikipedia.enable: true`。
- 豆瓣 provider 常驻启用且无需配置 Key。
- `metadata.tvdb.enable: true`，API Key 仍由用户填写。
- `ai.enable: true`，API URL、Key 和 model 仍由用户填写。
- 启用但缺少凭证时，运行态必须如实报告 provider/AI 不可用状态，不能影响其他来源继续取证。

## 10. 测试设计

### 10.1 确定性成功路径

- Wikipedia 与豆瓣唯一确认普通电影时生成计划，并断言所有 AI 函数未调用。
- TVDB 唯一 series ID 与 Wikipedia 或豆瓣一致时生成整剧计划，并断言 AI 未调用。
- 明确整季和单集通过 TVDB 范围验证后生成计划，并断言 AI 未调用。
- 规则路径输出可由现有 `validate_media_metadata` 接受的 `media_metadata v1`。
- 规则路径在用户确认前不查询 Prowlarr。

### 10.2 AI 入场路径

- 同名、多候选、单一来源、证据冲突和复杂关系触发两阶段 AI。
- 第一阶段 AI 收到第一遍证据和门槛失败原因。
- 扩展查询进行第二遍取证，合并结果不重复来源 URL 或稳定 ID。
- 第二阶段 AI 输出仍受来源真实性、TVDB Official Special 和 contract 校验约束。
- AI 不可用、无效或最终校验失败时明确阻断。

### 10.3 Provider 与配置

- 豆瓣 adapter 覆盖成功、未找到、接口或页面差异、超时和网络失败。
- Wikipedia、豆瓣、TVDB 任一失败不阻止其他 provider 完成。
- TVDB 与 AI 默认启用；缺少凭证时状态准确。
- 日志清洗测试确保凭证和敏感响应不泄漏。

### 10.4 负面门槛

- 同名不同年份不能确定性放行。
- 电影与剧集类型冲突不能确定性放行。
- 只有一个来源不能确定性放行。
- TVDB 不可用时，整季和单集不能确定性放行。
- TVDB 中不存在的 season/episode 不能确定性放行。
- 关联电影、OVA、Special 和其他复杂身份不能确定性放行。
- 缺少英文或原始标题不能进入 Prowlarr。

### 10.5 回归与构建

- 保持双语 canonical identity、英文 Prowlarr query、确认后下载提交行为。
- 保持 TVDB Official Special 不可被 AI 降级、临时 S00 分配和同名媒体保护。
- 运行 `feature/search` 完整测试套件。
- 验证 `config.default.yaml` 可解析且默认值正确。
- 构建并校验 `.tpx`，确认 manifest、wheel metadata 和测试资源完整。

## 11. 验收标准

1. 高置信度普通媒体不调用 AI，也能生成可确认计划。
2. 歧义、复杂关系和低证据场景才调用 AI。
3. 规则失败且 AI 不可用时，用户获得具体阻断原因而不是低置信度计划。
4. Wikipedia 与豆瓣无需 Key 即可参与，TVDB 和 AI 默认启用并正确报告缺少凭证。
5. 两条路径输出同一 `media_metadata v1` contract，并在用户确认前不查询 Prowlarr。
6. 现有同名、双语、TVDB Special、临时 S00 和确认后下载行为无回归。
7. 改动仅存在于 `feature/search`。
