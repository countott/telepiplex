# telepiplex 本轮改动清单

依据实施前的本地源码副本与 SHA-256 比较生成，未使用 Git。已有构建缓存/egg-info 不作为源码交付；没有重命名。

新增 27 个文件；修改 61 个文件；删除 0 个文件。

| 状态 | 文件 | 用途 |
|---|---|---|
| 修改 | [app/115bot.py](../../app/115bot.py) | Host 版本 3.6.9。 |
| 修改 | [app/handlers/interaction_handler.py](../../app/handlers/interaction_handler.py) | 编辑在途封口时清空原消息按钮，补偿失败保持可重试。 |
| 修改 | [app/handlers/plugin_handler.py](../../app/handlers/plugin_handler.py) | 旧 namespace 按钮反馈过期，仅清原按钮。 |
| 新增 | [docs/audits/2026-09-05-iteration-execution.md](../../docs/audits/2026-09-05-iteration-execution.md) | 批次执行、审查与方案裁定记录。 |
| 新增 | [docs/audits/2026-09-05-iteration-files.md](../../docs/audits/2026-09-05-iteration-files.md) | 本轮逐文件用途、增改删清单。 |
| 新增 | [docs/audits/2026-09-05-iteration-results.md](../../docs/audits/2026-09-05-iteration-results.md) | 成果、实际验证、性能取舍和有节奏的部署说明。 |
| 修改 | [docs/superpowers/plans/2026-09-05-business-flow-iterations.md](../../docs/superpowers/plans/2026-09-05-business-flow-iterations.md) | 原方案增加实际执行结果入口，保留原验收目标。 |
| 修改 | [examples/echo_feature/pyproject.toml](../../examples/echo_feature/pyproject.toml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/download/README.md](../../features/download/README.md) | 对齐当前版本、入口、完整性/性能/部署边界与示例。 |
| 修改 | [features/download/config.default.yaml](../../features/download/config.default.yaml) | download 分页默认关闭配置与校验。 |
| 修改 | [features/download/config.schema.json](../../features/download/config.schema.json) | download 分页默认关闭配置与校验。 |
| 修改 | [features/download/manifest.yaml](../../features/download/manifest.yaml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/download/pyproject.toml](../../features/download/pyproject.toml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/download/src/telepiplex_download/client.py](../../features/download/src/telepiplex_download/client.py) | 调用 SDK 严格完整扫描，不返回截断树。 |
| 修改 | [features/download/src/telepiplex_download/failure.py](../../features/download/src/telepiplex_download/failure.py) | 完整性和容量失败分类，准确保留已清理事实。 |
| 修改 | [features/download/src/telepiplex_download/service.py](../../features/download/src/telepiplex_download/service.py) | 清理前完整性/容量保护、复验与持久快照 capability。 |
| 新增 | [features/download/src/telepiplex_download/snapshot_store.py](../../features/download/src/telepiplex_download/snapshot_store.py) | download 提供方快照 sidecar 与大树扫描。 |
| 新增 | [features/download/src/telepiplex_download/transport_capacity.py](../../features/download/src/telepiplex_download/transport_capacity.py) | 清理前按完整 RPC/event 字节检查容量。 |
| 修改 | [features/download/tests/test_client_move_safety.py](../../features/download/tests/test_client_move_safety.py) | 严格树事实/真实移动安全边界。 |
| 新增 | [features/download/tests/test_completion_integrity.py](../../features/download/tests/test_completion_integrity.py) | 清理前验证、清理后变化及准确取消/失败记账。 |
| 修改 | [features/download/tests/test_feature_runtime.py](../../features/download/tests/test_feature_runtime.py) | Feature 完整性、取消/只读边界、按钮与版本合同。 |
| 新增 | [features/download/tests/test_snapshot_paging.py](../../features/download/tests/test_snapshot_paging.py) | 提供方分页容量、持久重放、游标与兼容边界。 |
| 新增 | [features/download/tests/test_transport_capacity.py](../../features/download/tests/test_transport_capacity.py) | 完整 RPC/event 编码容量与清理前拦截。 |
| 新增 | [features/download/tests/test_tree_integrity.py](../../features/download/tests/test_tree_integrity.py) | 严格扫描重复页、缺 ID、深度/节点与畸形事实。 |
| 修改 | [features/rename/README.md](../../features/rename/README.md) | 对齐当前版本、入口、完整性/性能/部署边界与示例。 |
| 修改 | [features/rename/manifest.yaml](../../features/rename/manifest.yaml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/rename/pyproject.toml](../../features/rename/pyproject.toml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/rename/src/telepiplex_rename/models.py](../../features/rename/src/telepiplex_rename/models.py) | 内部快照已验证状态禁止由网络载荷设置。 |
| 修改 | [features/rename/src/telepiplex_rename/processor.py](../../features/rename/src/telepiplex_rename/processor.py) | 整理前验证事件完整性与协议，旧事件严格补扫。 |
| 修改 | [features/rename/src/telepiplex_rename/service.py](../../features/rename/src/telepiplex_rename/service.py) | 修改前消费完整快照、旧事件补扫和终态幂等。 |
| 新增 | [features/rename/src/telepiplex_rename/snapshot_reader.py](../../features/rename/src/telepiplex_rename/snapshot_reader.py) | rename 拉齐、校验、持久化、确认及重启复用。 |
| 新增 | [features/rename/tests/test_event_integrity.py](../../features/rename/tests/test_event_integrity.py) | 旧/未知事件、虚假完整性与整理前拒绝。 |
| 修改 | [features/rename/tests/test_feature_processor.py](../../features/rename/tests/test_feature_processor.py) | 旧事件补扫与完整性要求。 |
| 新增 | [features/rename/tests/test_snapshot_reader.py](../../features/rename/tests/test_snapshot_reader.py) | 消费者缺页/错摘要/取消/持久化/双方重启。 |
| 新增 | [features/rename/tests/test_tree_integrity.py](../../features/rename/tests/test_tree_integrity.py) | 严格扫描重复页、缺 ID、深度/节点与畸形事实。 |
| 修改 | [features/search/README.md](../../features/search/README.md) | 对齐当前版本、入口、完整性/性能/部署边界与示例。 |
| 新增 | [features/search/docs/search-performance.md](../../features/search/docs/search-performance.md) | 搜索性能实验与 locale/底层 I/O 等待边界说明。 |
| 修改 | [features/search/manifest.yaml](../../features/search/manifest.yaml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/search/pyproject.toml](../../features/search/pyproject.toml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/search/src/telepiplex_search/anchored_candidate.py](../../features/search/src/telepiplex_search/anchored_candidate.py) | Wikipedia 季集证据绑定同 QID，拒绝跨作品借用。 |
| 新增 | [features/search/src/telepiplex_search/audit_transport.py](../../features/search/src/telepiplex_search/audit_transport.py) | 业务审计的外部 provider 替身与捕获型 Host。 |
| 修改 | [features/search/src/telepiplex_search/candidate_hydration.py](../../features/search/src/telepiplex_search/candidate_hydration.py) | 同作品 QID 的完整集表用于明确季集范围。 |
| 修改 | [features/search/src/telepiplex_search/candidate_locale.py](../../features/search/src/telepiplex_search/candidate_locale.py) | 本地化事务提交检查 revision 与候选 ID 顺序。 |
| 修改 | [features/search/src/telepiplex_search/live_pipeline_audit.py](../../features/search/src/telepiplex_search/live_pipeline_audit.py) | 实际 SearchFeature command/callback 审计与结果分层。 |
| 修改 | [features/search/src/telepiplex_search/media_metadata_v1.py](../../features/search/src/telepiplex_search/media_metadata_v1.py) | 从已核验同 QID 集表构造季集事实，最终下游仍使用 v2。 |
| 修改 | [features/search/src/telepiplex_search/service.py](../../features/search/src/telepiplex_search/service.py) | locale 2 秒整体事务预算及迟到数据隔离。 |
| 修改 | [features/search/src/telepiplex_search/work_discovery.py](../../features/search/src/telepiplex_search/work_discovery.py) | 独立标题发现与根发现并行，保持确定合并与错误顺序。 |
| 新增 | [features/search/tests/conftest.py](../../features/search/tests/conftest.py) | 默认 search 网络保护及显式 live 双重开关。 |
| 新增 | [features/search/tests/network_guard.py](../../features/search/tests/network_guard.py) | 记录意外外网尝试，异常被捕获也在 teardown 失败。 |
| 修改 | [features/search/tests/test_anchored_candidate.py](../../features/search/tests/test_anchored_candidate.py) | 同 QID 季集证据、跨 QID/related/missing 冲突拒绝。 |
| 新增 | [features/search/tests/test_business_pipeline_audit.py](../../features/search/tests/test_business_pipeline_audit.py) | 同名/年份/季集的实际搜索状态机与分层结果。 |
| 修改 | [features/search/tests/test_config_schema_contract.py](../../features/search/tests/test_config_schema_contract.py) | Search 当前版本与配置 schema 断言。 |
| 修改 | [features/search/tests/test_direct_link.py](../../features/search/tests/test_direct_link.py) | 修正遗漏 provider 网络替身。 |
| 修改 | [features/search/tests/test_feature_service.py](../../features/search/tests/test_feature_service.py) | 搜索真实行为、provider 替身与版本依赖断言。 |
| 修改 | [features/search/tests/test_live_pipeline_audit.py](../../features/search/tests/test_live_pipeline_audit.py) | 实际状态机/v2 与结果分层合同。 |
| 修改 | [features/search/tests/test_live_search_usability.py](../../features/search/tests/test_live_search_usability.py) | 显式 live 网络测试开关。 |
| 新增 | [features/search/tests/test_network_guard.py](../../features/search/tests/test_network_guard.py) | HTTP/socket 外网违规保留及真实 Unix connect。 |
| 新增 | [features/search/tests/test_search_performance.py](../../features/search/tests/test_search_performance.py) | 查询重叠、2 秒预算、确认/取消/重启中的迟到 locale。 |
| 修改 | [features/search/tests/test_work_discovery.py](../../features/search/tests/test_work_discovery.py) | 修正漏替换 provider 并保持根发现合同。 |
| 修改 | [features/search/tools/run_live_pipeline_audit.py](../../features/search/tools/run_live_pipeline_audit.py) | 离线/公共只读/Prowlarr 模式与正确配置门槛。 |
| 修改 | [features/sync/README.md](../../features/sync/README.md) | 对齐当前版本、入口、完整性/性能/部署边界与示例。 |
| 修改 | [features/sync/config.default.yaml](../../features/sync/config.default.yaml) | 说明定位退避、超时与取消等待行为。 |
| 修改 | [features/sync/manifest.yaml](../../features/sync/manifest.yaml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/sync/pyproject.toml](../../features/sync/pyproject.toml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 修改 | [features/sync/src/telepiplex_sync/adapters/plex.py](../../features/sync/src/telepiplex_sync/adapters/plex.py) | 媒体路径批量索引与兼容匹配。 |
| 修改 | [features/sync/src/telepiplex_sync/config_wizard.py](../../features/sync/src/telepiplex_sync/config_wizard.py) | 配置按钮统一 sync namespace。 |
| 修改 | [features/sync/src/telepiplex_sync/feature.py](../../features/sync/src/telepiplex_sync/feature.py) | sync 按钮/取消恢复和只读 Job capability，不连接 Plex 读取本地 Job。 |
| 修改 | [features/sync/src/telepiplex_sync/sync_service.py](../../features/sync/src/telepiplex_sync/sync_service.py) | 取消写入边界、Job 作品缓存和可取消退避。 |
| 修改 | [features/sync/tests/test_feature_runtime.py](../../features/sync/tests/test_feature_runtime.py) | Feature 完整性、取消/只读边界、按钮与版本合同。 |
| 修改 | [features/sync/tests/test_plex_adapters.py](../../features/sync/tests/test_plex_adapters.py) | 批量路径索引、重复路径与多 part 匹配。 |
| 修改 | [features/sync/tests/test_sync_service.py](../../features/sync/tests/test_sync_service.py) | 写入中取消、等待任务终结、缓存和退避计数。 |
| 修改 | [sdk/pyproject.toml](../../sdk/pyproject.toml) | 对齐组件版本及 SDK 2.1.0 依赖；不改变技术身份。 |
| 新增 | [sdk/src/telepiplex_plugin_sdk/storage_snapshot.py](../../sdk/src/telepiplex_plugin_sdk/storage_snapshot.py) | 共享分页/摘要/游标/持久不可变快照合同。 |
| 新增 | [sdk/src/telepiplex_plugin_sdk/storage_tree.py](../../sdk/src/telepiplex_plugin_sdk/storage_tree.py) | 共享严格扫描、节点事实/目录拓扑与完整性验证。 |
| 新增 | [tests/business_flow_storage.py](../../tests/business_flow_storage.py) | 实际 Feature 组合测试使用的稳定文件 ID 内存运输层。 |
| 修改 | [tests/test_bot_runtime_startup.py](../../tests/test_bot_runtime_startup.py) | Host 新版本启动断言。 |
| 修改 | [tests/test_deployment_contract.py](../../tests/test_deployment_contract.py) | SDK 2.1.0 的现役版本合同断言。 |
| 新增 | [tests/test_feature_action_contracts.py](../../tests/test_feature_action_contracts.py) | 实际 manifest 与 Host 按钮路由合同。 |
| 修改 | [tests/test_feature_builder.py](../../tests/test_feature_builder.py) | SDK 新依赖构建断言。 |
| 修改 | [tests/test_interaction_handler.py](../../tests/test_interaction_handler.py) | 文本/图片/媒体编辑在途封口、失败重试和空按钮。 |
| 新增 | [tests/test_large_snapshot_execution.py](../../tests/test_large_snapshot_execution.py) | 10,000 文件/500 目录分页接真实执行器及零追加移动重放。 |
| 新增 | [tests/test_manual_sync_runtime_boundary.py](../../tests/test_manual_sync_runtime_boundary.py) | 实际 Runtime 无自动事件订阅，仅本地只读管理方法。 |
| 修改 | [tests/test_plugin_handler.py](../../tests/test_plugin_handler.py) | 过期旧按钮、权限与当前任务键盘隔离。 |
| 修改 | [tests/test_pressure_telegram_pipeline.py](../../tests/test_pressure_telegram_pipeline.py) | 0/50ms、500ms busy、强制封口补偿、资源和调用预算。 |
| 新增 | [tests/test_real_feature_business_flow.py](../../tests/test_real_feature_business_flow.py) | 新增或更新相应业务边界回归。 |
| 修改 | [tests/test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 当前 Feature/SDK 版本表，保留技术身份和配置断言。 |
| 修改 | [tests/test_unraid_publish_script.py](../../tests/test_unraid_publish_script.py) | 发布脚本模拟标签与现役版本对齐，仍使用假命令记录器。 |
| 修改 | [tools/pressure_telegram_pipeline.py](../../tools/pressure_telegram_pipeline.py) | 记录正常调用与两类竞态补偿，支持延迟/强制封口压力。 |
