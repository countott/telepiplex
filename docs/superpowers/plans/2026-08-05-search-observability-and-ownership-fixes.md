# search 链路正确性、所有权与可观测性修复实施计划

> **For Codex:** Use the test-driven-development and verification-before-completion skills. Execute each task locally without Git.

**Goal:** 修复 search 链路中稳定链接解析、跨语言正式标题、operation 文本重入、响应契约和错误定位能力。

**Architecture:** 保持 Feature 负责业务状态、Host 负责全局 operation 所有权与渲染校验。复用既有 Feature session 作为“当前是否接受自由文本”的契约，并通过安全的结构化日志上下文贯通 Telegram update、Host operation 和 search business session。

**Tech Stack:** Python 3、pytest/unittest、python-telegram-bot、requests。

---

## Task 1: 豆瓣 dispatch 链接

- [x] 在 `features/search/tests/test_input_contract.py` 增加 dispatch 链接分类测试。
- [x] 在 `features/search/tests/test_direct_link.py` 增加 dispatch 链接不访问分享页的回归测试及安全失败原因测试。
- [x] 运行新增测试，确认当前实现失败。
- [x] 修改 `features/search/src/telepiplex_search/search_query.py` 和 `direct_link.py`。
- [x] 重跑新增测试并确认通过。

## Task 2: Wikipedia 跨语言正式标题

- [x] 在 `features/search/tests/test_wikipedia_adapter.py` 增加中文页面英文 langlink 测试。
- [x] 运行新增测试，确认当前实现失败。
- [x] 修改 `features/search/src/telepiplex_search/adapters/wikipedia.py`，只从同一页面实体的英文 langlink 补足英文正式标题。
- [x] 重跑适配器和元数据确认测试。

## Task 3: operation 文本重入

- [x] 更新 `tests/test_interaction_handler.py`，锁定开放同 Feature session 才允许文本。
- [x] 在 `tests/test_plugin_handler.py` 增加 gateway 防绕过测试。
- [x] 运行新增测试，确认当前实现失败。
- [x] 修改 `app/handlers/interaction_handler.py` 和 `app/handlers/plugin_handler.py`。
- [x] 重跑 Host 交互测试。

## Task 4: Feature→Host action 契约

- [x] 在 `features/search/tests/test_feature_service.py` 断言候选和澄清 action 只含 Host 渲染字段。
- [x] 在 `tests/test_plugin_handler.py` 增加 search action 渲染边界测试。
- [x] 运行新增测试，确认当前实现失败。
- [x] 从 `features/search/src/telepiplex_search/service.py` 删除非渲染 action data。
- [x] 重跑 Feature service 和 Host renderer 测试。

## Task 5: 结构化日志与关联 ID

- [x] 在 `features/search/tests/test_search_logging.py` 增加 `operation_id`、`update_id` 上下文继承与脱敏测试。
- [x] 在 `tests/test_plugin_handler.py` 增加无效响应、operation 回报冲突日志测试。
- [x] 增加后台 operation 回报拒绝只清理一次且有日志的测试。
- [x] 运行新增测试，确认当前实现失败。
- [x] 修改 `features/search/src/telepiplex_search/search_logging.py`、`service.py` 和 `app/handlers/plugin_handler.py`。
- [x] 重跑相关测试。

## Task 6: 完整验证

- [x] 运行 search Feature 全量测试。
- [x] 运行 Host 全量测试。
- [x] 运行 download、rename、sync、caption Feature 全量测试。
- [x] 运行 Python 语法检查和本地工作区边界检查。
- [x] 汇总实际修改文件和实际测试结果，提醒等待 Syncthing `Up to Date / 最新`。

## 实际验证记录

- search Feature：`414 passed, 2 skipped, 95 subtests passed`
- Host：`380 passed, 1 skipped, 176 subtests passed`
- download Feature：`60 passed, 25 subtests passed`
- rename Feature：`83 passed, 2 subtests passed`
- sync Feature：`136 passed, 64 subtests passed`
- caption Feature：`1 passed`
- 实时身份检查：dispatch URL 解析为 `douban / 36235977`；Wikipedia 返回 `Q125131076 / Backrooms / 2026 / movie`
- 工作区边界：`.git` 不存在、`.worktrees` 不存在、`.stfolder` 存在
