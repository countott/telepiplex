# search 1.11.1 与 rename 1.5.2 回归加固设计

**状态：** 已批准方案，等待书面设计复核  
**目标版本：** search 1.11.1、rename 1.5.2  
**范围：** 候选中文标题、根作品年份、元数据校验诊断、季集解析、文件级整理后的空目录清理、完成状态与用户文案，以及覆盖这些链路的压力验证。

## 1. 背景与目标

search 1.11.0 与 rename 1.5.1 的运行日志暴露了三类不同问题：

1. search 能找到正确作品和中文来源事实，但候选展示阶段仍可能把英文回退值当成中文标题；选中候选后的严格 `media_metadata v1` 校验失败时只返回通用错误，无法定位失败字段。
2. rename 的文件级移动可以成功，但生产链路没有执行已经存在的空源目录清理能力，仍会把任务报告为完整成功；自动下载根与手动扫描根又共用了同一保护语义。
3. `S1 - 01` 既符合字幕组常见的季集格式，又会被组级规则误识别为季范围。该解析错误会污染 probe、候选约束和日志，即使某些完整全剧资源最终仍碰巧得到正确的 `whole_series` 范围。

本次目标不是放宽安全门槛，而是让每个阶段的事实、状态和副作用一致：真实中文才能进入 `chinese_title`，根作品年份不能被季页或关联作品覆盖，contract 失败必须说明字段，移动完成后必须执行有证据的目录清理，只有全链路实际完成才能显示完整成功。

## 2. 非目标

- 不引入 AI 搜索、AI 候选排序或 AI 中文翻译。
- 不降低豆瓣、Wikipedia、TMDB、TVDB、AniList 的来源绑定和唯一性要求。
- 不让 rename 删除无法确认内容、仍含文件的目录或用户选择的手动扫描根。
- 不改变现有媒体目标目录规范：`中文名 (English Title)/English Title Season NN/English Title SxxExx.ext`。
- 不修改 search 与 rename 之外 Feature 的版本号，不执行发布、Git、标签或 GitHub 操作。

## 3. 总体架构

修复分成两个独立补丁包，通过既有 `media_metadata v1` 和 `download.completed` 边界连接：

```text
文件树
  -> rename 确定性 probe
  -> search 候选发现
  -> 候选中文本地化
  -> 用户确认
  -> 精确来源补全
  -> 可诊断 contract 校验
  -> rename 文件级规划与执行
  -> 目标验证
  -> 空源目录清理
  -> 完成状态
  -> 可选 handoff
```

每个阶段只拥有一种职责。候选展示不得伪造 canonical 字段；contract 校验不得承担候选搜索；文件执行不得隐式代表目录清理成功；handoff 不得覆盖整理与清理的真实结果。

## 4. search 1.11.1 设计

### 4.1 中文标题字段语义

`identity.chinese_title` 只保存来源事实明确提供并通过绑定验证的中文标题。没有可信中文标题时保持空值，不再用 `original_title`、`official_english_title`、罗马字或其他拉丁标题回填。

用户界面的显示回退与 canonical identity 分离：

- 有可信中文标题：显示 `中文名 (Original/English Title)`。
- 没有可信中文标题：显示英文或罗马字标题，并附加“中文名待确认”。
- `display_title` 和 `title_status` 只属于候选预览，不进入 canonical 命名字段，也不能使严格 contract 误以为中文已经确认。

中文标题必须带来源和绑定方式，例如 `douban + wikidata_exact` 或 `douban + strong_fields`。单纯因为查询文本含中文，不得视为已确认中文标题。

### 4.2 候选展示前的有界中文本地化

候选本地化按以下顺序执行，最多处理当前页五个候选：

1. 优先使用 Wikidata P4529 精确读取豆瓣条目。
2. 没有豆瓣精确 ID 时，允许使用已有标题、年份、媒体类型、国家、主创或季号进行有界豆瓣查询。
3. 只有一个稳定豆瓣 subject 同时满足媒体类型且至少两个强字段时，才能将中文标题绑定到该候选。
4. 多个 subject、年份冲突或类型冲突时不绑定，候选保持英文并标记“中文名待确认”。

本地化结果进入候选自身的来源链接和字段来源，不允许只修改展示文字而不留下证据。查询继续使用现有并发上限与缓存，不增加无界调用。

### 4.3 选中候选后的精确补全

用户选择候选后，search 继续执行精确来源补全和 frozen candidate hydration。任何选择后新确认的中文标题都必须重新构造 `media_metadata`，而不是只追加 source link。

候选展示与最终 contract 使用同一标题选择函数，避免候选列表已经找到中文、最终命名却回退英文，或候选列表仍是英文而最终事实已经有中文但没有重新渲染的问题。

### 4.4 根作品年份与 TVDB 查询

剧集 scope 和根作品 identity 使用两个不同概念：

- `scope_year`：季或单集自身的年份，只能用于展示与 scope 事实。
- `root_year`：根剧集首播年份，用于根作品来源绑定和 TVDB 查询。

直接季页面在根作品尚未确认前可以进行无年份查询，但一旦 Wikipedia、Wikidata、TMDB 或已有 contract 确认根作品，`root_year` 必须保留。TVDB 查询优先使用稳定 TVDB ID；没有 ID 时使用根英文标题与 `root_year`。中间来源事实只有与同一稳定根 ID 绑定时才允许提供年份，不能把关联作品或前作年份带入 TVDB 查询。

### 4.5 可诊断的 contract 校验

共享 SDK 增加结构化校验结果，至少包含：

- `path`：失败字段路径，例如 `identity.search_title_policy` 或 `items[12].episode_number`。
- `reason_code`：稳定机器码，例如 `title_policy_mismatch`、`duplicate_episode_coordinate`、`category_library_mismatch`。
- `detail`：不含敏感数据的简短说明。

原有 `validate_media_metadata(...)` 的兼容行为保持不变；search 在 `confirm_media_metadata(...)` 失败时使用详细结果写日志，并把首个安全 reason code 返回 rename。用户文案使用中文摘要，不直接显示英文内部异常。

## 5. rename 1.5.2 设计

### 5.1 季集解析优先级

`S1 - 01`、`S01 - 26` 和 `Season 1 - 02` 先按季集格式解析。季范围必须具有明确的第二个季标记，例如：

- 接受：`S1-S2`、`S01 - S02`、`Season 1 - Season 2`。
- 不接受为季范围：`S1 - 01`、`Season 1 - 02`。

组级 probe 必须复用或服从文件级季集证据。已经得到高置信度 `(season, episode)` 的文件不得再被同一文本的低优先级季范围规则覆盖。

### 5.2 文件执行与目录清理分阶段

文件整理保持现有 file-first 原则：每个文件独立规划、移动和验证。所有文件执行完成后，新增显式目录清理阶段：

1. 只收集已经成功移动且源文件已删除的 source ancestor。
2. 从最深目录向上逐级处理。
3. 每个目录删除前重新获取 provider 信息并分页确认真实为空。
4. 目录内仍有未解析文件、字幕、样片、失败文件或新出现内容时保留。
5. 删除失败不回滚已验证成功的文件移动，但任务进入“整理完成、源目录清理未完成”状态。

### 5.3 自动下载与手动扫描的清理边界

清理 API 使用独立的 `cleanup_boundary`，不再复用文件扫描根：

- 自动 `download.completed`：保护 `selected_path` 对应的媒体分类根，允许删除其下已经核验为空的具体发布目录，包括 `download_root/final_path`。
- 手动 `/rename`：保护用户选择的扫描根，只允许删除扫描根内部已经核验为空的作品子目录。
- 任何无法证明位于边界内部的路径都不得删除。

目标目录如果恰好位于同一分类根内，也不能因为它是 source ancestor 候选而被删除；清理候选只从原始 source path 推导，并在删除前重新读取。

### 5.4 清理结果与完成状态

目录清理返回结构化摘要：

- `deleted_directories`
- `retained_directories`，包含 `not_empty`、`protected_root` 等原因
- `failed_directories`，包含 provider 错误类型
- `complete`

`cleanup_complete` 只能来自该摘要，不能再由 `failed_files == 0` 推导。公开 `file_results` 和持久任务结果保存清理摘要，使重放、人工诊断和压力测试能够验证没有重复删除。

### 5.5 用户文案与 handoff

完成标题统一为“剧集整理完成（文件级）”或“电影整理完成（文件级）”。只有存在实际 TVDB ID 时才追加 TVDB 信息，不再把 TVDB 写入通用完成标题。

状态分为：

- 文件与目录均完成：完整成功。
- 文件完成、目录清理有保留或失败：成功但带清理警告。
- 文件存在冲突或失败：部分完成。
- 没有可验证文件结果：失败或保持原位。

sync/Plex 未安装只影响后续 handoff，不改变前述整理与清理状态。

## 6. 测试驱动与压力验证路径

所有行为修改遵循 RED -> GREEN -> REFACTOR。压力验证使用固定 fixture 和 fake provider，不依赖实时网络。

### 6.1 阶段一：确定性解析

- 添加完整 Honey and Clover 两季 38 文件回归，断言只观察到第 1、2 季及正确集号。
- 覆盖 `S1-S2`、`S01 - S02`、`Season 1 - Season 2` 真季范围。
- 批量生成至少 10,000 个混合命名文件，验证没有把 episode number 扩展成季范围，没有重复坐标。

完成该阶段后运行 rename 的 probe、file facts 和 file-first inventory 测试。

### 6.2 阶段二：候选中文与年份

- 重放 `wikidata:Q3786532` 无豆瓣精确 ID、但强字段唯一命中“蜂蜜与四叶草”的场景。
- 覆盖无唯一中文来源时的“中文名待确认”，并断言 canonical `chinese_title` 为空。
- 重放《龙之家族》第三季链接，断言 TVDB 查询使用根年份 2022 或稳定 TVDB ID，绝不使用 2011 或季首播年份。
- 批量验证至少 1,000 组候选事实组合，覆盖精确绑定、强字段唯一、多候选冲突、年份冲突和类型冲突。

完成该阶段后运行 search 的 candidate locale、title policy、direct link、confirmed enrichment 和 feature service 测试。

### 6.3 阶段三：contract 诊断

- 为每个 validator 分支建立可定位失败测试，重点覆盖标题策略、分类映射、重复季集坐标和 scope placement。
- 重复校验至少 10,000 个合法/非法 contract，断言结果稳定、输入不被修改且 reason code 可重放。
- `metadata_unresolved` 集成测试必须验证日志和 capability 返回包含相同首要 reason code。

完成该阶段后运行 SDK 与 search contract 测试。

### 6.4 阶段四：文件移动与目录清理

- 自动下载单文件移动后删除空发布目录，但保留媒体分类根。
- 手动 `/rename` 删除空作品子目录，但保留用户扫描根。
- 有未解析文件、字幕、冲突文件、新出现文件或 provider 分页未完成时不得删除目录。
- provider 删除失败时文件结果保持成功，`cleanup_complete=false`，用户收到清理警告。
- 事件重放不得重复移动或重复删除。
- 压力 fixture 至少包含 10,000 个文件、500 个嵌套源目录、成功/保留/冲突/失败混合结果。

完成该阶段后运行 rename 的 file executor、processor、feature service 和 durable replay 测试。

### 6.5 最终全链路验证

最终门禁按顺序执行：

1. Core 全量测试。
2. download、search、rename、sync、caption 五个 Feature 全量测试。
3. 新增 search/rename 压力用例。
4. 构建 `/tmp/search-1.11.1.tpx` 与 `/tmp/rename-1.5.2.tpx`。
5. `unzip -t` 校验两个包；读取包内 manifest，确认 plugin ID 和版本。
6. 检查 `.git`、`.worktrees` 不存在且 `.stfolder` 保留，但不执行任何 Git 命令。

压力用例以正确性、幂等性、无误删和稳定 reason code 为硬门槛。耗时作为观测数据记录，不使用容易受机器负载影响的过窄墙钟阈值；若相较同一运行中的基线出现数量级退化，则停止交付并定位热点。

## 7. 版本和文档

search 维护文件统一更新到 1.11.1：manifest、pyproject、README、HTTP User-Agent 和版本契约测试。rename 维护文件统一更新到 1.5.2：manifest、pyproject、README、构建示例和版本契约测试。生成的 `build/` 与 egg-info 不作为源码手工修改目标。

README 需要记录：

- search 的中文标题不会由英文回填，候选可能显示“中文名待确认”。
- search contract 失败提供稳定 reason code。
- rename 会在文件验证后清理边界内的空源目录，并区分完整成功与清理警告。

## 8. 验收标准

- Honey and Clover 两季文件只产生第 1、2 季 probe，候选在强字段唯一匹配后展示“蜂蜜与四叶草”。
- 无可信中文来源时 canonical `chinese_title` 保持为空，UI 明确标识待确认。
- 《龙之家族》第三季的 TVDB 根作品查询不再使用 2011。
- 任意 strict contract 失败都有稳定字段路径和 reason code。
- 《龙之家族》示例中的源视频文件和核验为空的发布目录都被清理，`/真人剧集` 分类根与目标目录保留。
- 清理失败不会伪报 `cleanup_complete=true`，也不会回滚已经验证成功的文件。
- 无 TVDB 参与时完成标题不含“TVDB”。
- 所有针对性测试、全量测试、压力用例和两个安装包校验均通过。
- Mac 本地未执行 Git 或发布操作；最终由 Syncthing 交付用户检查。
