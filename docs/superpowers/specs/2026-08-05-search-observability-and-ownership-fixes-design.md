# search 链路正确性、所有权与可观测性修复设计

## 背景

2026-08-05 的 Telegram 实际交互暴露了四组相互放大的问题：

1. 豆瓣 App `dispatch/movie/<id>` 分享链接没有被识别为稳定豆瓣条目，被降级为网页跳转读取；豆瓣返回 HTTP 418 后，用户只能看到笼统的“固定链接读取失败”。同一条目在中文 Wikipedia 页面上又可能缺少英文标题，导致 `canonical_latin_title` 不完整。
2. Host 在任意 `awaiting_input` operation 下都放行普通文本。按钮选择阶段没有打开 Feature 文本会话，却仍能收到新的直链并启动后台 search；新 operation 到 Host 回报时才发现旧 operation 仍拥有该用户。
3. search action 的 `data` 携带了 Host 未声明的 `candidate_key`、`clarification` 字段，Host 的严格响应校验会把本来可渲染的候选结果判为无效响应。
4. Host 对 operation 回报冲突和 action 校验失败只发送笼统 Telegram 错误，没有记录失败原因、operation、update 和 search session 的关联信息；后台 search 二次回报失败也可能静默退出。

## 目标

- 对豆瓣 subject 链接和 App dispatch 分享链接都做纯本地、确定性的 subject ID 提取，不依赖分享页跳转。
- 从同一 Wikipedia 页面实体的英文跨语言链接补足正式英文工作标题，避免把中文页面缺少英文字段误判为严格元数据不完整。
- 只有实际存在且属于当前 operation Feature 的开放文本会话时，Host 才允许普通文本穿过 operation gate。
- search 发出的 action `data` 只包含 Host 允许的渲染字段。
- 每个失败分支记录结构化、脱敏日志，至少能用 `update_id`、`operation_id`、`search_session_id` 串起一次请求。

## 方案

### 1. 链接和影片身份解析

- 扩展 `extract_douban_subject_id`，同时接受：
  - `/subject/<digits>`
  - `/doubanapp/dispatch/movie/<digits>`
- 继续保留既有 host 白名单和嵌套 `uri/url/target` 参数处理。dispatch 链接一旦拿到 ID，直接进入豆瓣条目适配器，不再访问分享落地页。
- 对仍需读取的分享链接，`DirectLinkError.details` 只记录安全原因码，例如 `http_status:418`、`request_error:Timeout`、`redirect_missing`、`redirect_limit`，不记录原始 URL。
- Wikipedia API 查询增加英文 `langlinks`。中文页面与英文页面由同一 `wikibase_item` 绑定；将英文 article title 去除明确的媒体消歧后缀（如 `(film)`、`(TV series)`、`(2026 film)`），写入 `english_title` 和 `official_english_title`。不猜测没有跨语言证据的英文名。

### 2. operation 所有权和文本重入

- Host 以现有 `telepiplex_plugin_sessions` 为文本输入真相源。
- active operation 存在时，非命令普通文本仅在以下条件全部满足时放行：
  - operation 状态为 `awaiting_input`；
  - 当前用户存在未过期的开放 Feature session；
  - session 的 `plugin_id` 与 active operation 的 `plugin_id` 相同。
- 候选选择、片源选择等纯按钮阶段使用关闭 session，因此普通直链不会再次分发给 search，也不会创建第二个后台 operation。
- `dynamic_message_gateway` 增加同样的防御性检查，避免测试、未来 handler 排序变动或直接调用绕过全局 gate。
- 后台规划遇到 operation 所有权拒绝后，只记录一次失败并清理本地 plan；不再对同一无主 operation 递归提交终态。

### 3. Feature→Host 响应契约

- Host action `data` 继续只承载渲染字段：`keyboard`、`photo_url`、`poster_items`、`parse_mode`。
- 从 search 候选和澄清 action 的 `data` 中删除 `candidate_key`、`clarification`。候选身份继续保存在 Feature 内部 plan 与 callback payload 中，不需要暴露给 Host renderer。
- 增加真实 search action 到 Host `_keyboard_markup` / `_render_actions` 的边界回归测试。

### 4. 结构化、脱敏日志

- Host 新增统一的 Feature 结果错误日志入口，使用固定 `event` 和 `reason`：
  - `feature.response_invalid`
  - `feature.operation_report_rejected`
  - `feature.message_dispatch_failed`
- 日志包含可获得的 `plugin_id`、`update_id`、`chat_id`、`user_id`、`operation_id`、revision、action index；不记录 action 文本、URL、Token、Cookie 或配置值。
- search 日志上下文扩展 `operation_id` 和 `update_id`，由一次 `bind_search_log_context` 自动注入后续事件。
- search 后台 task 失败记录 `search.background_task_failed`；operation sink 回报失败记录 `search.operation_report_failed`。异常文本经过现有 sanitizer。

## 测试策略

- 先新增失败测试，再做最小实现。
- Feature 范围：
  - dispatch 链接分类与无网络解析；
  - Wikipedia 中文页 `langlinks` 补足英文正式标题；
  - action `data` 契约；
  - search 日志关联字段与脱敏。
- Host 范围：
  - 纯按钮阶段拒绝普通文本；
  - 同 Feature 开放 session 允许普通文本；
  - 不同 Feature 或过期 session 拒绝；
  - operation 回报冲突和 action 校验失败生成可关联日志。
- 最后运行 search 全量测试、Host 全量测试及五个 Feature 全量测试。

## 边界

- 不改变 operation 的持久化 schema。
- 不弱化 Host 对 Feature action 的严格校验。
- 不把原始用户输入或分享 URL写入日志。
- 不执行 Git，不发布；Mac 修改完成后交由 Syncthing 同步到 Unraid。
