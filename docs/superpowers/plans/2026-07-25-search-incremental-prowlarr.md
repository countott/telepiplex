# Search 按索引器增量片源实施计划

> 依据：
> `docs/superpowers/specs/2026-07-25-search-incremental-prowlarr-design.md`

## Task 1：锁定 Adapter 契约

**Files**

- Modify: `features/search/tests/test_prowlarr_adapter.py`
- Modify: `features/search/src/telepiplex_search/adapters/prowlarr.py`

1. 先写测试：已启用 Indexer 过滤；单 Indexer 搜索使用独立 ID 与
   `indexer_timeout=75`；错误继续保留结构化类型。
2. 运行测试并确认因接口尚不存在或参数错误而失败。
3. 实现 Indexer 列表和单 Indexer 查询接口。
4. 运行 Adapter 测试至通过。

## Task 2：锁定紧凑报告与稳定 ID

**Files**

- Add: `features/search/src/telepiplex_search/release_identity.py`
- Add: `features/search/tests/test_release_identity.py`
- Modify: `features/search/src/telepiplex_search/release_report.py`
- Modify: `features/search/tests/test_release_report.py`

1. 先写测试：相同 magnet 生成相同 ID；不同片源 ID 不同；按钮携带 ID；
   每个结果只占一行；长标题的 12 条报告不超过 4096；规格字段仍可见。
2. 运行测试并确认旧的序号回调和多行报告导致失败。
3. 实现稳定 ID、去重与紧凑报告。
4. 运行报告和身份测试至通过。

## Task 3：锁定增量编排与选择冻结

**Files**

- Modify: `features/search/tests/test_feature_service.py`
- Modify: `features/search/src/telepiplex_search/service.py`

1. 先写测试：快 Indexer 的结果在慢 Indexer 结束前上报；失败 Indexer 不移除
   成功结果；增量重排后旧 ID 仍选中原片源；点击后剩余搜索被取消且不覆盖提交
   状态；聚合兜底仍工作。
2. 运行测试并确认旧的单请求和序号选择导致失败。
3. 实现并发任务、节流上报、稳定映射、冻结和取消语义。
4. 运行 Feature service 测试至通过。

## Task 4：配置和版本

**Files**

- Modify: `features/search/config.default.yaml`
- Modify: `features/search/config.schema.json`
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/README.md`
- Modify: `features/search/tests/test_feature_service.py`
- Modify: `tests/test_technical_identity_migration.py`

1. 增加 `indexer_timeout: 75` 及 schema。
2. 同步 Search `1.0.5` 五处版本契约。
3. 运行配置、版本和 Search 全部测试。

## Task 5：完整验证与交付

1. 运行 Search Feature 全测试。
2. 运行根项目全测试及其他 Feature 测试。
3. 检查 `.git`、`.worktrees` 不存在且 `.stfolder` 存在。
4. 汇总实际修改和验证结果，提醒等待 Syncthing
   `Up to Date / 最新`，不执行 Git 或发布。

