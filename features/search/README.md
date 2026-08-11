# search Feature

search 1.8.0 使用“豆瓣发现、用户确认、同级来源收敛”的分阶段流程。豆瓣仍只负责首次候选发现；确认后由豆瓣、Wikipedia、TVDB、TMDB 共同形成字段级证据，日语动画再由 AniList 补充官方罗马字。最终身份通过独立消息确认，后续 Prowlarr 进度不会覆盖它。

## 发起搜索

- `/s 片名` 或 `/search 片名` 只接受普通文本片名。
- `/s <链接>` 不再兼容，系统会提示直接发送链接。
- 可以把豆瓣、Wikipedia、TVDB、TMDB 或 AniList 的稳定作品链接直接发送到当前 Telegram 对话；无需命令，适用于系统分享面板。
- 一条消息只能指向一个作品实体。多个不同作品链接会直接提示链接无效。
- 上述来源的稳定作品链接会锁定该实体并跳过文本候选发现。无法从分享页提取稳定实体但能读取可靠页面标题时，标题才会回到普通文本搜索。

## 普通文本发现

普通文本只由豆瓣承载作品发现。程序先清理标题、年份、电影/剧集类型和季集范围，再生成第一轮豆瓣 query；Wikipedia 和 TVDB 不参与首次召回。

豆瓣结果先按 subject ID 去重并过滤用户明确指定的媒体类型。只有标题、年份和类型形成唯一硬匹配时才自动确认，并且不调用 AI。其他情况进入一次保留完整上下文的统一 AI 搜索裁决：

- AI 只能引用本轮真实豆瓣 subject ID，不能生成作品或修改来源事实。
- AI 最多返回 1–5 个候选；原始池有多个结果时不得缩成一个来取得自动确认权。
- 第一轮零结果或全部不相关时，AI 只能改写一次业务 query；第二轮不能再次改写。
- AI 超时、服务错误或结构不合规时，程序用完全相同的上下文原样重试一次，不重新调用豆瓣。
- AI 技术重试仍失败但豆瓣已有结果时，按豆瓣原始顺序展示前 5 个；豆瓣为零时提示修改关键词。

一个非硬匹配候选仍显示 `就是它 / 都不是`；多个候选逐项选择，并提供 `都不是`。用户点击 `都不是` 后立即结束，不重搜、不改写，也不调用后续来源。

候选文案显示：

```text
简中标题（年份）
Official English Title
国家/地区：中国大陆
类型：电影 / 剧集
来源：豆瓣
总览：来源提供的作品简介（存在时）
```

英文标题只有豆瓣提供可靠字段时才显示。界面不显示 AI 置信度、理由、内部评分、候选版本或未补全来源。

豆瓣混合标题会按来源字段拆分：`后室 Backrooms` 的简中主标题只保留
`后室`，`蜂蜜与四叶草 ハチミツとクローバー` 的日文部分进入原名字段。
不同文字系统不得整体写入简中标题。

## 确认后增强

用户或程序确认一个作品后，search 锁定其稳定身份，再执行确定性的来源收敛：

1. Wikipedia query 只使用已确认的标题、年份和媒体类型；只有唯一同作品结果才进入证据图。
2. TMDB 使用已确认英文/原名或中文标题，加年份和媒体类型约束唯一匹配，并补充 TMDB、IMDb、Wikidata、TVDB 跨站 ID、发行信息、演职员和制作信息。
3. 剧集使用已确认的可靠英文标题查询 TVDB；TVDB 只有唯一匹配时才补 Series ID 和季集 inventory。
4. 只有确认 `original_language=ja` 且类型属于动画时才查询 AniList；AniList 的公共 GraphQL API 不需要 API Key，其罗马字必须再次通过标题、年份和类型校验。

各来源之间不使用总分或“后返回者覆盖前者”。外部 ID 一致，或规范标题、年份和媒体类型完全一致时才合并；`evidence.field_resolutions` 保存每个字段的选中值、所有来源和冲突状态。单个补充来源失败不改变已确认身份。TVDB 不可用时，剧集降级为 `whole_series`，不展示季/单集选择，也不会把未经验证的季集号写入 Prowlarr query。

中文 Wikipedia 请求使用 `zh-cn` 显示变体，同时保留规范标题和 Wikidata
身份；Wikipedia 标题不得覆盖已经确认的豆瓣简中标题。TVDB 英文标题只接受
明确的 `eng/en` 翻译，或 `original_language=en` 的主标题；没有可靠语言
标签的拉丁别名不再当作英文。

选中后只精确读取已保存的固定来源链接。豆瓣锚点必须保持可读且稳定 ID 一致；其他来源冲突会隔离并记录，不得改变用户确认的作品。

## Prowlarr 与下游

严格 `media_metadata v1` 形成后，程序才生成最多三条、来源已验证且去重的 Prowlarr query。英文标题单来源即可使用；日语动画优先使用 AniList 提供的罗马字，再使用正式英文标题。程序不再本地音译假名，AI 也不生成资源 query，用户原始输入不会混入 query：

- 单电影：`Canonical Title YYYY`；
- 多季全集：`Canonical Title`；
- 单季：`Canonical Title Sxx`；
- 单集：`Canonical Title SxxExx`。

Prowlarr 继续按 Indexer 和 query 有界并发搜索，执行身份与范围硬门禁、去重
和质量排序，最多展示 12 个结果。电影片源标题必须包含匹配年份；多季全集、
单季和单集不要求年份。整剧或单季标题出现年份时，只使用已验证的剧集播出
区间或目标季年份判断；单集年份只作为软证据。选中片源后继续交给
`download.provider`，下载完成后由 rename 复用确认过的 `media_metadata v1`。

search 仍提供无状态的 `media.search.resolve_metadata` capability，并返回
`resolved`、`confirmation_required` 或 `unresolved` 结构化状态。rename
提供的结构化 probe 会先按电影/剧集类型收窄候选；唯一候选直接补全元数据，
歧义候选由用户确认后从同一个 Rename job 继续，不会重新下载或丢失文件树。
probe 不修改作品标题或身份。运行配置位于
`/config/plugins/search/config.yaml`；Wikipedia、豆瓣和 AniList 无需 API Key。TMDB 使用 API Read Access Token，可通过 `/search_config` 配置；TVDB 与 AI 继续使用各自服务端配置。凭据不会进入模型上下文或结构化日志。

## 日志

每个搜索会话使用稳定的 `search_session_id`。日志记录输入分类、直链解析、候选确认、各元数据来源的开始/完成状态、最终 query 变体、release gate 结果和唯一终态。日志不记录 API Key、Token、Cookie、Authorization、magnet 或完整 provider payload。

## 测试与构建

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

构建示例：

```bash
python tools/build_feature.py features/search /tmp/search-1.8.0.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
