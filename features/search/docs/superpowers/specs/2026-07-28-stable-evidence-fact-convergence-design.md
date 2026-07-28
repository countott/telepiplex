# Search stable evidence fact convergence

本文件定义 Search 1.1.2 对 Provider 重复事实、TVDB 跨媒体类型 ID
碰撞、AI binding 修复边界和回归测试的补充合同。它延续 Search 1.1.1
的 unified anchored search，不改变 Provider、AI、Prowlarr 和 download
之间的职责。

## 问题

同一个 Wikipedia、豆瓣或 TVDB 条目可能因为首轮检索、补源检索、
不同语言或不同标题提示被返回多次。当前原始来源合并只按完整 JSON
相等去重；同一稳定 ID 只要 `query`、语言、标题或摘要不同，就会在
实体图中形成多个不相等的 `EvidenceFact`。候选注册表随后以
`duplicate_fact_id` 拒绝整个计划。

TVDB 的电影和剧集还可能拥有相同数字 ID。事实标识若只使用
`tvdb:<id>`，两个不同媒体实体会发生内部标识碰撞。

## 事实标识

- Wikipedia 使用 `wikipedia:<wikibase_item>`。
- 豆瓣使用 `douban:<subject_id>`。
- TVDB 使用 `tvdb:<movie|series>:<tvdb_id>`。
- 无 Provider 稳定 ID 的请求内事实继续使用请求内回退标识，不将该
  回退标识持久化。回退标识只由当前 Provider 载荷的规范哈希生成，
  不得借用其他 Provider 的外部 ID。

事实标识只用于当前搜索请求的来源图、AI fact binding 和诊断日志，
不是持久媒体实体 ID。

## 规范合并

`build_search_graph` 在聚类前按事实标识收敛 Provider 事实：

- 标题、类型别名、Genre、复杂关系信号取确定性并集；
- 外部 ID 取无冲突并集；
- TVDB episode inventory 按 episode ID，缺失 ID 时按季集号去重；
- 同一集的一条记录有 episode ID、另一条只有季集号时，在唯一匹配
  的前提下收敛成一条；同一 episode ID 的季集坐标冲突时拒绝该事实；
- 空字段由同一稳定事实的非空字段补全；
- 合并结果不依赖 Provider 返回顺序；
- 同一事实的年份、媒体类型或同名外部 ID 出现两个不同非空值时，
  抛出结构化 `source_fact_conflict`，不得任意挑选。

图记录每个成功合并的 Provider、事实 ID 和出现次数。Planner 在每次
建图后写入 `search_fact_merge status=merged`；冲突写入
`search_fact_merge status=conflict`，包含阶段、Provider、事实 ID 和
冲突字段，但不包含 URL、摘要、凭据或完整来源载荷。

## AI 修复边界

AI binding repair 只处理模型可改变的候选载荷错误，例如未知事实、
角色冲突或同一事实被绑定到多个候选。

`duplicate_fact_id` 属于来源图完整性错误。即使防御性注册表再次发现
它，也必须立即返回 `candidate_binding_failed`，记录具体事实 ID，
不得再次调用 AI。AI 永远不能修复或覆盖 Provider 事实图。

## 链路验收

- 同一 Wikipedia QID 由中英文查询返回两次时，AI 只看到一个事实，
  且标题并集保留。
- 同一 TVDB Series 由多个标题提示返回时，只保留一个 series 事实，
  episode inventory 去重且不丢失。
- TVDB Movie 与 Series 使用相同数字 ID 时仍是两个不同事实和候选。
- 首轮与 source supplement 重复返回同一稳定事实时，候选重新绑定
  成功，不进入 `binding_repair`。
- 同一稳定事实出现年份或外部 ID 冲突时，Planner 在调用候选 AI 前
  确定性失败并记录具体事实 ID。
- Search 的候选、metadata v1、Prowlarr 查询与 download handoff 既有
  合同保持不变。
- Search 版本升级为 1.1.2，所有当前版本源、包元数据、测试和构建
  示例保持一致。
- 日志中出现过的 `ODDTAXI`、`冰果`、`蜂蜜与四叶草`、`1917` 和
  `想见你`，以及 12 个复杂剧集家族形成固定 usability 语料；必须验证
  候选非空、作品可区分、metadata 可用、Prowlarr Query 非空且 UI
  不暴露内部字段。
- 复杂剧集语料必须覆盖多季事实归并、同名年代重启、电影/剧集冲突、
  跨地区改编、动画/真人改编和衍生作品。多季案例必须将分季来源事实
  绑定到一个 series root，并通过相应 TVDB season inventory。
- Wikipedia 与豆瓣提供显式启用的无凭据真实网络门禁；豆瓣必须返回
  与查询别名匹配的事实，Wikipedia 限流可降级但不得被误报为无候选。
- 真实 TVDB 与 AI 门禁由显式配置路径启用；缺少真实凭据时必须报告
  未执行，不得把模拟测试称为真实 API 验证。
