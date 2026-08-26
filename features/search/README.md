# search Feature

search 2.0.0 使用 Wikipedia 与 Wikidata 的统一身份图确定根作品，并把人工确认候选冻结到持久状态。search 不调用 AI。用户原始片名只做空白归一化，不在 query 侧补写冒号、“篇”或别名；例如 `死神 千年血战` 由 Wikipedia 的排序结果承担匹配。规划期间先显示文字状态，候选海报数据就绪后 Host 才把有效消息游标迁移到图片候选并移除旧文字状态，不再提前展示海报占位图；新图片在成为权威游标并清理旧消息前不带按钮。候选按钮第一次点击即进入不可重复消费的确认状态，Host 不改写 Telegram 的只读 callback 对象，而是从已持久化的 claim 恢复原始 payload；后台进度 revision 不会清除 claim，只有对应 Feature RPC 完成后才按 generation、token 和 message ID 原子释放。重复 callback 只返回同一冻结结果。候选、正在确认和最终作品身份都属于 Host API 1.7 的同一条 `identity` 消息段；身份段封存后，Prowlarr 搜索结果才开启新的 `search` 消息，因此不会再出现两条有效身份卡片或两条仅后一条可点击的搜索结果。SDK 2.0.0 提供 v2-only 的最小 `media_metadata` 下游合同和 operation segment API。

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
3. Wikipedia 搜索结果按原始排名入选：规范标题精确相等可在任意排名入选；非精确结果只允许第 1 名，且其规范标题必须以前述查询标题开头；入选后仍要通过影视实体结构校验；
4. 简中 Wikipedia 只有在没有任何通过标题与结构门槛的结果时才查询英文；Wikipedia 与 Wikidata 的有效结果随后合并；
5. 同名且仍然有效的电影/剧集全部交给用户选择，超过五项时按每页五项展示；
6. 即使只有一个结果，也必须先显示身份海报卡片并等待用户确认。

候选以简中标题为主；没有可靠简中标题时直接显示英文，不做机器硬翻译。卡片包含年份、电影/剧集类型、国家/地区和实际来源。规划阶段只显示文字，不生成图片占位符；候选已经形成但个别远程海报不可用时，才在候选宫格内使用标题占位卡。图片投递失败时回退为同内容文本。

## 身份确认后的元数据补全

用户选择候选或发送唯一稳定链接后，search 才进入多维度元数据补全。锚点稳定 ID 不允许被补充来源改写：

1. Wikipedia/Wikidata 提供根作品身份、英文标题、年份、国家和可用的结构事实；Wikidata P4529 提供无需 IMDb API 的精确豆瓣绑定；
2. TMDB 补充 TMDB、IMDb、Wikidata、TVDB 跨站 ID、海报、发行信息、演职员、制作信息以及剧集清单；
3. TVDB 补充唯一 Series ID 和常规季集清单；
4. 精确 P4529 豆瓣条目在候选展示前写入权威简中根标题；没有精确绑定时，只对前五个候选使用标题之外至少两个强字段形成唯一匹配，歧义结果不写入中文标题；豆瓣不提供整剧季集结构；
5. 仅当作品已确认是日本动画电影或剧集时，AniList 补充官方罗马字标题和 AniList ID，不提供海报或季集结构。

字段以来源事实逐项收敛，不使用后返回覆盖，也不记录 Search AI 决策。海报优先级为 TMDB、豆瓣、Wikipedia、占位图、纯文本。丰富来源事实只保留在 search 私有确认状态和展示层；向 Download/Rename 交付的是严格 `media_metadata v2`，其中 `title_en` 是已验证英文名，`title_original` 是原语种标题，两者不再互相代替；其余字段只包含稳定主引用、已验证 provider 引用、媒体类型、年份、范围和目录类别。合同不携带海报、演员、分级、国家、完整分集表或 `naming_metadata`；Plex 需要的丰富资料继续由 Plex 自身 provider 完成。Rename 完成即为本链路终态，不自动触发 Plex 扫描。

## 剧集范围菜单

正剧结构优先使用确认的 Wikipedia 分集表；根页面只有明确的分集列表链接时，会沿该精确链接读取结构。Wikipedia 确实无表或不可用时，TVDB/TMDB 各自作为完整 order profile 比较，绝不通过坐标交集制造残缺季度。唯一兼容 profile 才能生成菜单，无法裁决时返回明确冲突。所有来源均排除 Season 0，不根据豆瓣分季结果猜整剧季数。

直接命中某一季的 Wikipedia 页面只用于确认季度身份，不视为已经拥有单集库存；search 仍会调用 TVDB/TMDB 补齐该季结构。因此 `/s 西部世界第三季` 与先搜索 `/s 西部世界` 再选择第三季遵循同一季集菜单合同。

- 未指定范围：显示“全剧”，随后列出每一季；确认只有一季时只显示“全剧（共 1 季）”。
- 已指定季度：显示该季度“全季”，随后列出常规单集。
- 已指定单集：验证季集坐标后直接进入对应资源查询。
- 没有可靠结构：不编造季集菜单，也不把未经验证的季集号写入资源 query。

## Prowlarr 与下游

私有确认合同形成后，程序生成最多三条、来源已验证且去重的最终 query，并在 `search` 消息中显示 query；界面不暴露 Prowlarr 产品名。日语动画优先使用 AniList 罗马字，再使用正式英文标题。程序不本地音译假名，用户描述性噪声不会混入 query：

- 单电影：`Canonical Title YYYY`；
- 多季全集：`Canonical Title`；
- 单季：`Canonical Title Sxx`；
- 单集：`Canonical Title SxxExx`。

Prowlarr 仍按 Indexer 和 query 有界并发搜索，执行身份与范围硬门禁、去重和质量排序，最多展示 12 个结果。电影片源标题必须包含匹配年份；剧集资源按已确认范围验证。特殊内容在进入资源搜索前即被排除。

search 提供 `media.search.resolve_metadata` 与持久冻结的 `media.search.confirm_metadata` capability。Rename 的结构化 probe 只用于补全已存在文件的确定身份；Rename 自身约束式文件映射能力不属于 search 2.0.0 的 AI 移除范围。剧集身份同时保存根作品年份与范围年份；TVDB 补全优先使用稳定 Series ID，否则只使用根作品年份，不使用季条目或补充来源的年份。最终契约失败会记录精确字段路径、原因码和说明。

## 配置与日志

运行配置位于 `/config/plugins/search/config.yaml`。Wikipedia、Wikidata、豆瓣和 AniList 无需 API Key；P4529 与 IMDb ID 都直接来自 Wikidata/来源事实，不接入 IMDb API。TMDB 使用 API Read Access Token，TVDB 使用自身凭据，均可通过 `/search_config` 配置。search 不再包含 AI 配置项。2.0.0 沿用配置 schema v2，并继续通过包内声明安全删除 1.8.0 遗留的顶层 `ai` 配置段，其余用户配置保持不变；回滚时 Host 会恢复升级前的完整配置。

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
python tools/build_feature.py features/search /tmp/search-2.0.0.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
