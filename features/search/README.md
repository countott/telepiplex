# search Feature

search 1.5.0 使用“豆瓣发现、用户确认、确认后增强”的分阶段流程，避免首次搜索把不同来源、不同语言和不同作品混成一组候选。本版本不改变业务流程，修复 AI 禁用开关、Prowlarr torrent 重定向与纯文本 magnet 解析，以及 TVDB 零编号保真问题。

## 发起搜索

- `/s 片名` 或 `/search 片名` 只接受普通文本片名。
- `/s <链接>` 不再兼容，系统会提示直接发送链接。
- 可以把豆瓣、Wikipedia 或 TVDB 的 PC、手机、本地化页面、分享文本或短链接直接发送到当前 Telegram 对话；无需命令，适用于系统分享面板。
- 一条消息只能指向一个作品实体。多个不同作品链接会直接提示链接无效。
- 豆瓣、Wikipedia 和 TVDB 的稳定作品链接会锁定该实体并跳过文本候选发现。无法从分享页提取稳定实体但能读取可靠页面标题时，标题才会回到普通文本搜索。

## 普通文本发现

普通文本只由豆瓣承载作品发现。程序先清理标题、年份、电影/剧集类型和季集范围，再生成第一轮豆瓣 query；Wikipedia 和 TVDB 不参与首次召回。

豆瓣结果先按 subject ID 去重并过滤用户明确指定的媒体类型。只有标题、年份和类型形成唯一硬匹配时才自动确认，并且不调用 AI。其他情况进入一次保留完整上下文的统一 AI 搜索裁决：

- AI 只能引用本轮真实豆瓣 subject ID，不能生成作品或修改来源事实。
- AI 最多返回 1–5 个候选；原始池有多个结果时不得缩成一个来取得自动确认权。
- 第一轮零结果或全部不相关时，AI 只能改写一次业务 query；第二轮不能再次改写。
- AI 超时、服务错误或结构不合规时，程序用完全相同的上下文原样重试一次，不重新调用豆瓣。
- AI 技术重试仍失败但豆瓣已有结果时，按豆瓣原始顺序展示前 5 个；豆瓣为零时提示修改关键词。

一个非硬匹配候选仍显示 `就是它 / 都不是`；多个候选逐项选择，并提供 `都不是`。用户点击 `都不是` 后立即结束，不重搜、不改写，也不调用后续来源。

候选文案只显示：

```text
简中标题（年份）
Official English Title
类型：电影 / 剧集
来源：豆瓣
```

英文标题只有豆瓣提供可靠字段时才显示。界面不显示 AI 置信度、理由、内部评分、候选版本或未补全来源。

豆瓣混合标题会按来源字段拆分：`后室 Backrooms` 的简中主标题只保留
`后室`，`蜂蜜与四叶草 ハチミツとクローバー` 的日文部分进入原名字段。
不同文字系统不得整体写入简中标题。

## 确认后增强

用户或程序确认一个作品后，search 锁定其稳定身份，再执行确定性的顺序增强：

1. Wikipedia query 只使用已确认的简中标题、年份和媒体类型；只有唯一同作品结果才补简中/英文/原名、别名和 Wikidata 身份。
2. 剧集随后使用 Wikipedia 验证的英文标题，或豆瓣已有的可靠英文/原名，加年份和 series 类型约束查询 TVDB。
3. TVDB 只有唯一匹配时才补 Series ID 和季集 inventory。该阶段不调用 AI。

Wikipedia 失败或歧义不阻断搜索。TVDB 不可用、无可靠英文身份或无法唯一匹配时，剧集降级为 `whole_series`，写入 `warning:tvdb_inventory_unavailable`，不展示季/单集选择，也不会把未经验证的季集号写入 Prowlarr query。

中文 Wikipedia 请求使用 `zh-cn` 显示变体，同时保留规范标题和 Wikidata
身份；Wikipedia 标题不得覆盖已经确认的豆瓣简中标题。TVDB 英文标题只接受
明确的 `eng/en` 翻译，或 `original_language=en` 的主标题；没有可靠语言
标签的拉丁别名不再当作英文。

选中后只精确读取已保存的固定来源链接。豆瓣锚点必须保持可读且稳定 ID 一致；其他来源冲突会隔离并记录，不得改变用户确认的作品。

## Prowlarr 与下游

严格 `media_metadata v1` 形成后，程序才生成唯一、来源已验证的 Prowlarr
query；AI 不生成资源 query，来源别名和用户原始输入也不会混入 query：

- 单电影：`Canonical Title YYYY`；
- 多季全集：`Canonical Title`；
- 单季：`Canonical Title Sxx`；
- 单集：`Canonical Title SxxExx`。

Prowlarr 继续按 Indexer 和 query 有界并发搜索，执行身份与范围硬门禁、去重
和质量排序，最多展示 12 个结果。电影片源标题必须包含匹配年份；多季全集、
单季和单集不要求年份。整剧或单季标题出现年份时，只使用已验证的剧集播出
区间或目标季年份判断；单集年份只作为软证据。选中片源后继续交给
`download.provider`，下载完成后由 rename 复用确认过的 `media_metadata v1`。

search 仍提供无状态的 `media.search.resolve_metadata` capability。rename
提供的结构化 probe 会在无交互候选歧义判断前只按电影/剧集类型收窄候选，
再在唯一剧集候选上应用季集范围；probe 不修改作品标题或身份，也不会替用户
从多个同类型作品中做选择。运行配置位于
`/config/plugins/search/config.yaml`；Wikipedia 和豆瓣无需 API Key，TVDB
与 AI 使用服务端配置，凭据不会进入模型上下文或结构化日志。

## 日志

每个搜索会话使用稳定的 `search_session_id`。日志记录输入分类、直链解析、豆瓣 query 与结果摘要、硬匹配、统一 AI 请求/响应和原样技术重试、候选展示、用户确认或拒绝、Wikipedia/TVDB 增强、metadata probe 类型约束前后的候选数量、降级原因、最终 `search.prowlarr_query_built`、`search.release_gate_evaluated` 以及唯一终态 `search.completed`。日志不记录 API Key、Token、Cookie、Authorization、magnet 或完整 URL；TVDB inventory 只记录数量。

## 测试与构建

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

构建示例：

```bash
python tools/build_feature.py features/search /tmp/search-1.5.0.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
