# Plex Management 扫描与增强收拢设计

## 目标

`sync` 不再重复管理 Plex 已经成熟的识别、匹配和元数据加载能力。

自动管线只负责：

1. `rename` 发布 `media.organized` 后，按照 `plex_library_id` 触发 Plex 扫描；
2. 在扫描步骤内部使用每个最终文件的 `final_path` 定位 Plex 条目；
3. 对已定位条目执行海报、原声音轨和中文字幕三项增强。

同时新增 `/scan` General Action。它实时读取 Plex 媒体库，允许用户扫描一个库或全部库，但不执行任何下载后增强。

## 运行边界

### 自动管线

自动管线对外只有以下阶段：

```text
scanning -> artwork -> audio -> subtitle -> completed
```

- `scanning` 包含分库、触发扫描和按 `final_path` 定位条目。
- Plex 自己负责识别电影或剧集、自动匹配、加载元数据和加载基础海报。
- 插件不再显示或执行独立的 `locating`、`matching`、`localizing` 阶段。
- 插件不再修正 Plex 匹配、不再刷新中文元数据，也不再写入 Special 自定义元数据。

### 手动扫描

`/scan` 是独立 General Action：

```text
/scan -> 实时列出 Plex 媒体库 -> 用户选择 -> 扫描 -> 汇总结果
```

- 菜单置顶显示“扫描全部媒体库”。
- 其余按钮使用 Plex 实时返回的媒体库名称，回调值使用媒体库 ID。
- 点击后立即扫描，不再二次确认。
- 扫描全部时逐库执行；单库失败不阻断其他库。
- `/scan` 不创建自动管线 Job，不定位媒体，也不执行 artwork、audio、subtitle。

## 单 Job 批次模型

一个 `media.organized` 事件只创建一个持久化 Job。

Job payload 保存：

- 事件身份：`operation_id`、`operation_revision`、`user_id`、`chat_id`；
- 媒体身份：canonical `media_metadata`；
- 批次信息：`resource_name`、`provider`；
- `targets`：本次事件中每个最终媒体文件的目标记录。

每个 target 至少包含：

```text
target_id
final_path
media_type
season_number
episode_number
category_kind
```

Job 的幂等键以 canonical `metadata_id` 为主；没有 canonical contract 时使用 provider、最终路径和资源名构造稳定身份。同一事件重投只返回原 Job，不重复扫描或增强。

旧数据库表继续使用，不做破坏性迁移。历史 Job 保持可查询；新 Job 通过 payload 中的 `targets` 使用新模型。Feature 的 `state_schema_version` 升为 `2`。

## 扫描和定位

扫描阶段先为每个 target 解析 `category_folder[].plex_library_id`，再按 library ID 分组。每个媒体库在一个 Job 中只扫描一次。

扫描后，插件轮询 Plex 最近入库条目，使用媒体文件实际路径与 target 的 `final_path` 匹配。路径匹配允许容器路径前缀差异，但必须保持完整目标路径后缀一致。

扫描结果保存为：

```text
step_results.scanning.libraries
step_results.scanning.targets
```

每个 target 的扫描结果记录 `library_id`、`rating_key` 和定位状态。

- 全部 target 未定位：Job 失败，不进入增强阶段。
- 部分 target 未定位：已定位 target 继续增强，未定位 target 记录 warning。
- 已完成扫描结果在重试时复用，不重复触发 Plex 扫描。

## 三项增强

三项增强按 target 执行，但仍属于同一个 Job。

### Artwork

- 电影使用电影条目。
- 剧集单集优先解析其所属剧集条目，同一剧集在一个 Job 中只处理一次海报。
- 候选来源为 TMDB 无字海报和 Fanart.tv 无字海报。
- 优先级为 TMDB，其次 Fanart.tv。
- 同一来源中按票数、评分、分辨率排序。
- 最高候选的业务评分唯一时自动应用。
- 最高候选业务评分并列时进入人工选择；URL 或数据库顺序不得用于静默打破平局。
- 没有候选或外部海报服务不可用时记录 warning，继续音轨步骤。

人工选择使用图片预览：

- 当前候选作为 Telegram 图片展示；
- 提供“选择这张”“上一张”“下一张”“取消任务”；
- 用户选择后写入 Plex，并继续当前 Job。

### Audio

- 通过 Plex 条目的媒体 parts 读取音轨。
- 原始语言优先从 TMDB 条目详情取得。
- 只在原始语言音轨中比较无损规格、codec、声道数和码率。
- 唯一最高候选自动应用。
- 最高候选并列时显示候选名称、codec、声道和码率供人工选择。
- 无法取得原始语言或没有对应音轨时记录 warning，继续字幕步骤。

### Subtitle

- 只选择语言为中文的既有字幕流。
- 优先级为：已选外挂字幕、稳定外挂字幕、内嵌字幕。
- 最佳优先级只有一个候选时自动应用。
- 最佳优先级存在多个候选时人工选择，不再按 stream ID 静默取第一个。
- 没有中文字幕时记录 unchanged，不使 Job 失败。

## 人工选择状态

统一使用 `awaiting_selection` Job 状态，不再使用 `waiting_match_confirmation`。

等待项持久化在当前步骤结果中，包含：

```text
kind: artwork | audio | subtitle
target_id
part_id
candidates
candidate_index
```

一个 Job 同时只展示一个待选项。用户完成选择后，插件应用选择并从当前步骤继续；后续如果还有歧义，再展示下一项。

取消只停止后续动作。Plex 已接受的扫描、海报或流选择不会自动回滚。

## Telegram 命令

### `/plex`

- 不带参数时显示最近 Job。
- 删除自然语言 AI 管理入口。
- 带参数时返回用法提示，不再调用 AI。

### `/scan`

- 实时调用 Plex `list_libraries()`。
- 每页最多显示八个媒体库，支持翻页。
- 页面始终提供“扫描全部媒体库”和“取消”。
- 点击具体库或全部库后启动独立后台 operation。
- 扫描完成后显示成功库和失败库。

### `/sync_config`

保留 Plex、TMDB 和 Fanart.tv 配置。

删除 AI 配置页和 `ai` 配置段。MCP 配置继续通过 YAML 管理。

## MCP 能力

MCP 继续使用 Streamable HTTP。非 loopback 监听必须配置 Bearer Token。

保留工具：

### 只读

- `plex_server_status`
- `plex_list_libraries`
- `plex_inspect_item`
- `plex_list_artwork_candidates`
- `plex_list_audio_candidates`
- `plex_list_subtitle_candidates`
- `plex_get_job`
- `plex_list_jobs`

### 写入

- `plex_scan_library`
- `plex_set_textless_poster`
- `plex_select_original_audio`
- `plex_select_chi_subtitle`
- `plex_retry_job`

所有 MCP 写操作继续使用十分钟有效、单次消费的确认令牌。

删除工具：

- 匹配候选查询和修正匹配；
- 中文元数据刷新；
- 旧完整管理管线执行；
- 元数据批量写入。

删除本地 AI orchestrator。MCP 和自动管线共享同一 Plex service，但自动管线属于可信 `media.organized` 业务流程，不额外要求人工写确认。

## 错误与恢复

- Plex 基础配置缺失时，自动事件和 `/scan` 明确失败；Feature 进程仍可启动。
- TMDB 或 Fanart.tv 不可用只影响对应增强，不能阻止扫描和其余增强。
- Job claim 保持原子性，完成态 Job 不重新打开。
- 进程停止时运行中的新 Job 标记为 `interrupted`。
- 有 Telepiplex `operation_id` 的协调任务保持现有所有权语义：重启后报告中断，不静默重放远端 Plex 写操作。
- 无协调身份的历史 Job 可以由显式重试继续。
- 重试复用已完成步骤和 target 结果，从第一个未完成增强继续。

## 版本与验证

- Feature 版本升为 `1.0.0`。
- `host_api` 为 `>=1.2,<2.0`，因为 Telegram 图片选择使用 Host API 1.2 的 `send_photo` / `edit_photo` action。
- `state_schema_version` 升为 `2`。
- 更新 manifest、默认配置、配置 schema、README 和构建示例。
- 使用测试先行覆盖：
  - 一个事件只创建一个 Job；
  - 同库只扫描一次并逐 `final_path` 定位；
  - 部分 target 定位失败仍处理其余 target；
  - 三类候选的自动选择和歧义人工选择；
  - `/scan` 单库、全部库、分页和部分失败汇总；
  - MCP 精简后的精确工具面；
  - 旧匹配、中文化和 AI 能力不再存在；
  - 中断、幂等和取消语义。
