# search Feature

search 1.11.5 使用 Wikipedia 与 Wikidata 的统一身份图确定根作品，并把需要人工确认的候选计划冻结到持久状态；确认时只读取原计划，不再重新规划或因来源顺序变化选中另一作品，重复确认会返回同一结果。相同且通过身份选择器的原始 provider 请求会在进程内短时 single-flight，返回值仍由每个调用方独立执行稳定 ID、范围和严格 hydration 校验；Prowlarr Indexer 按确定性首波与延迟尾波启动，首个合格资源仍会立即进入既有门禁与稳定 callback。对 rename 的能力响应移除了重复的大体积证据和节目清单，同时使用 SDK 1.3.2 的有界非阻塞诊断传输。简中候选优先使用 Wikidata P4529 精确读取豆瓣条目；没有 P4529 时，仅对前五个候选执行唯一强字段豆瓣定位。语义 `chinese_title` 不再写入英文、罗马字或日文兜底，展示层在没有可靠简中标题时单独回退英文。剧集范围由完整的 Wikipedia/TVDB/TMDB order profile 裁决；rename 提交存量文件 probe 时，只把能够与已确认官方坐标唯一对应的项目写入合同。资源若采用 absolute、DVD、alternate 或平台自定义顺序，多出的歧义坐标会进入 `inventory_reconciliation.unresolved`，不再拖垮其余明确匹配的剧集；一个坐标都匹配不到时仍然失败关闭。裸剧名在身份确认后会先进入季/集范围菜单，只有用户明确选择全剧、季度或单集后才开始资源搜索。Host 已接受 milestone、但落账或回执中断时，Feature 会复用同一个 milestone ID 恢复，不会把暂态 `internal_error` 误判为资源搜索失败。search 不调用 AI，也不支持自然语言描述搜索；用户需要提供明确片名，或直接发送受支持的稳定作品链接。作品身份消息先完整保留，后续资源搜索进度不会覆盖它。

## 发起搜索

- `/s 片名` 或 `/search 片名` 接受明确的中文或英文影视名称，可附年份、电影/剧集类型以及明确的 `S01`、`S01E01` 范围。
- 描述性需求、口语改写、错别字推断和特殊内容检索会被拒绝；请改用准确片名。
- `/s <链接>` 不再兼容，系统会提示直接发送链接。
- 豆瓣、Wikipedia、Wikidata、TVDB、TMDB 或 AniList 的稳定作品链接可以直接发送到 Telegram 对话。精确链接锁定作品身份并跳过根作品发现。
- Wikipedia 消歧义链接不会被当成作品；页面标题会回到确定性根作品菜单。
- Season 0、Special、OVA、OAD 和其他附加内容不进入 Search。

## Wikipedia 根作品发现

普通片名由 Wikipedia 简中/英文搜索和 Wikidata 搜索共同召回。程序读取 MediaWiki 页面与 Wikidata QID，再由 Wikidata `P31` 判断电影或剧集；人物、列表、组织和其他非影视实体会被过滤。精确标题种子会沿 `adaptation_ids` 与 `part_ids` 做最多两层、最多 60 个实体的结构扩展，以覆盖同名改编、系列与电影集合。

结果按以下规则展示，而不做跨作品复杂映射：

1. 用户明确的年份、电影/剧集类型和季集范围是硬条件；
2. 同一 Wikidata QID 的中英文页面合并为一个根作品；
3. 只有精确标题/别名或经 Wikidata 关系边到达的电影与剧集可以展示；子串结果只能用于召回，不能成为候选；
4. Wikipedia 与 Wikidata 的有效结果始终合并，不因已有一个弱结果而停止扩展；
5. 同名且仍然有效的电影/剧集全部交给用户选择，超过五项时按每页五项展示；
6. 即使只有一个结果，也必须先显示身份海报卡片并等待用户确认。

候选以简中标题为主；没有可靠简中标题时直接显示英文，不做机器硬翻译。卡片包含年份、电影/剧集类型、国家/地区和实际来源。远程海报不可用时使用 Host 的既有占位图；图片投递失败时回退为同内容文本。

## 身份确认后的元数据补全

用户选择候选或发送唯一稳定链接后，search 才进入多维度元数据补全。锚点稳定 ID 不允许被补充来源改写：

1. Wikipedia/Wikidata 提供根作品身份、英文标题、年份、国家和可用的结构事实；Wikidata P4529 提供无需 IMDb API 的精确豆瓣绑定；
2. TMDB 补充 TMDB、IMDb、Wikidata、TVDB 跨站 ID、海报、发行信息、演职员、制作信息以及剧集清单；
3. TVDB 补充唯一 Series ID 和常规季集清单；
4. 精确 P4529 豆瓣条目在候选展示前写入权威简中根标题；没有精确绑定时，只对前五个候选使用标题之外至少两个强字段形成唯一匹配，歧义结果不写入中文标题；豆瓣不提供整剧季集结构；
5. 仅当作品已确认是日本动画电影或剧集时，AniList 补充官方罗马字标题和 AniList ID，不提供海报或季集结构。

字段以来源事实逐项收敛，不使用后返回覆盖，也不记录 Search AI 决策。海报优先级为 TMDB、豆瓣、Wikipedia、占位图、纯文本。最终形成严格 `media_metadata v1`；相同合同会随选中片源进入 Download，由 Rename 使用确认身份、目录类型和季集项目完成整理。Rename 完成即为本链路终态，不再自动触发 Plex 扫描。

## 剧集范围菜单

正剧结构优先使用确认的 Wikipedia 分集表；根页面只有明确的分集列表链接时，会沿该精确链接读取结构。Wikipedia 确实无表或不可用时，TVDB/TMDB 各自作为完整 order profile 比较，绝不通过坐标交集制造残缺季度。唯一兼容 profile 才能生成菜单，无法裁决时返回明确冲突。所有来源均排除 Season 0，不根据豆瓣分季结果猜整剧季数。

- 未指定范围：显示“全剧”，随后列出每一季；确认只有一季时只显示“全剧（共 1 季）”。
- 已指定季度：显示该季度“全季”，随后列出常规单集。
- 已指定单集：验证季集坐标后直接进入对应资源查询。
- 没有可靠结构：不编造季集菜单，也不把未经验证的季集号写入资源 query。

## Prowlarr 与下游

严格 `media_metadata v1` 形成后，程序生成最多三条、来源已验证且去重的最终 query，并在 Telegram 进度消息中显示 query；界面不暴露 Prowlarr 产品名。日语动画优先使用 AniList 罗马字，再使用正式英文标题。程序不本地音译假名，用户描述性噪声不会混入 query：

- 单电影：`Canonical Title YYYY`；
- 多季全集：`Canonical Title`；
- 单季：`Canonical Title Sxx`；
- 单集：`Canonical Title SxxExx`。

Prowlarr 仍按 Indexer 和 query 有界并发搜索，执行身份与范围硬门禁、去重和质量排序，最多展示 12 个结果。电影片源标题必须包含匹配年份；剧集资源按已确认范围验证。特殊内容在进入资源搜索前即被排除。

search 提供 `media.search.resolve_metadata` 与持久冻结的 `media.search.confirm_metadata` capability。Rename 的结构化 probe 只用于补全已存在文件的确定身份；Rename 自身约束式文件映射能力不属于 search 1.11.5 的 AI 移除范围。剧集身份同时保存根作品年份与范围年份；TVDB 补全优先使用稳定 Series ID，否则只使用根作品年份，不使用季条目或补充来源的年份。最终契约失败会记录精确字段路径、原因码和说明。

## 配置与日志

运行配置位于 `/config/plugins/search/config.yaml`。Wikipedia、Wikidata、豆瓣和 AniList 无需 API Key；P4529 与 IMDb ID 都直接来自 Wikidata/来源事实，不接入 IMDb API。TMDB 使用 API Read Access Token，TVDB 使用自身凭据，均可通过 `/search_config` 配置。search 不再包含 AI 配置项。1.11.5 沿用配置 schema v2，并继续通过包内声明安全删除 1.8.0 遗留的顶层 `ai` 配置段，其余用户配置保持不变；回滚时 Host 会恢复升级前的完整配置。

每个搜索会话使用稳定的 `search_session_id`。日志记录输入分类、直链解析、候选确认、元数据来源状态、最终 query 变体、片源门禁结果和唯一终态；不记录 API Key、Token、Cookie、Authorization、magnet 或完整来源 payload。

## 测试与构建

大范围真实来源审计（默认只对精选样本跑完整链；加 `--all-full` 可让全部样本进入下游 dry-run，仍不会调用 Prowlarr 或提交下载）：

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=features/search/src:sdk/src \
  "$PY" features/search/tools/run_live_pipeline_audit.py \
  --output /tmp/search-live-audit.json --all-full
```

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests
```

构建示例：

```bash
python tools/build_feature.py features/search /tmp/search-1.11.5.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
