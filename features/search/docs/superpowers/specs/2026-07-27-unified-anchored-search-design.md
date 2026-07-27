# Search 1.1.0 unified anchored search

本文件记录 Search 1.1.0 的现行实现合同，并取代早期基于程序评分、单一来源身份、1–7 候选展示以及 90 秒业务规划预算的搜索设计。历史文件保留用于追溯，不再代表当前运行语义。

## 入口与候选

- 文本入口并发广泛召回 Wikipedia、豆瓣和 TVDB。
- 直链入口精确读取 Wikipedia、豆瓣或 TVDB 稳定 ID，并锁定该事实为唯一锚点。
- 两个入口随后都调用同一套缺失来源补查、AI 事实绑定、冻结链接、精确链接读取、严格 `media_metadata v1` 和 Prowlarr Query 构建接口。
- AI 只能引用本次 Provider 返回的事实 ID。URL、稳定 ID、海报和季集 inventory 必须由程序从事实中固化。
- 每个 `AnchoredCandidate` 保存全部确认的多 Provider 链接、同 Provider 多层级链接、全部海报与 unresolved 来源。

## 候选数量与交互

- AI 输出 0–6 个作品候选。
- 文本 0 个候选返回 `no_match`。
- 文本 1 个候选自动选中。
- 文本 2–6 个候选展示一张编号海报拼图和候选按钮。
- 直链始终保持一个锁定候选，AI 只能补充来源和层级。
- 候选展示后冻结链接集合；选择操作不得重新按标题搜索 Provider。

## 系列层级

TVDB series root、Wikipedia series root 和豆瓣分季条目可以属于同一个剧集候选。豆瓣分季事实保留各自链接、年份和海报，并通过 TVDB inventory 验证季号。未通过验证的 AI 季集关系记录为 `unresolved_scope_link`，不能进入严格 v1。真人电影、重拍版和其他独立作品保持独立候选。

## 严格元数据与查询

选中后只重新读取候选的冻结链接。v1 保存根身份、全部来源链接、稳定 ID、标题、别名、海报、字段来源、季集层级、TVDB inventory、AI 判断和 unresolved 状态。类型冲突、必要字段不足与固定链接读取失败分别使用明确错误。

Prowlarr Query 从 v1 事实构建并去重，顺序为：

1. 日本动画罗马字；
2. 官方英文名；
3. 其他来源拉丁别名；
4. 原名；
5. 用户原文。

范围后缀只来自 v1，Prowlarr 不参与作品身份判断。

## 故障边界

搜索规划没有业务层 30/65/90 秒截止时间。Provider、AI 和 Prowlarr HTTP 请求仍受各自配置的故障超时保护。AI 技术故障不得降级为程序评分候选，交互需要区分来源失败、AI `no_match`、AI 故障、绑定失败、固定链接读取失败和 v1 不完整。
