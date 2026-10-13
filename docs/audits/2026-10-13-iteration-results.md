# Telepiplex 修复与性能优化迭代交付

日期：2026-10-13。已完成第0–7轮本地实施与验收：F1–F7修复、移动缺省批量100、字幕正文复用与检索重叠、Host历史失败索引。六套完整测试共 **2873 passed、3 skipped、626 subtests passed**。尚未发布，没有操作真实媒体。

[原始自检](2026-10-13-real-case-self-check.md)保持原文。[交付数据](2026-10-13-iteration-results.json)保存原始测试输出、测量样本、压力结果、复现脚本和文件清单，不依赖临时目录长期保留。

## 逐轮结果

| 轮次 | 完成行为与验收 | 本地版本 |
| --- | --- | --- |
| 0 | 7项复现转正式回归，保存旧实现失败输出后再修复；补齐隔离字幕声明依赖。参考基线2650 passed/3 skipped/626 subtests。 | 基线 |
| 1 | 目录状态、嵌套字段、count/offset/has_more、空尾页、重复和身份统一验证；115外层证据贯穿真实RPC。清理绑定观测ID；兼容copy目标SHA1/大小确认后重验源、删除原源ID、复验结果。矛盾目录与假copy成功均零删除。 | Download2.2.1；Rename2.4.1 |
| 2 | 字段、发布名、成员名独立核对明确季集，作品ID不解除冲突；保留整季单集成员与编号映射。电影发行年和剧集合法季度年份规则保持；分辨率/CRC不当作年份。 | Caption1.3.1 |
| 3 | 下划线与点分隔等价；特别内容检查排除确认的Special/Extras/Special Ops标题；根作品季度显示全季/单集菜单。仅点击实际渲染按钮完成业务回放，Season0/OVA/OAD仍拒绝。 | Search2.6.2 |
| 4 | config/runtime/processor/executor缺省100，显式32保持；范围1–100，信息查询32上限保持。批次边界、多目录、部分成功、丢回执、取消及恢复通过。 | Rename2.4.2 |
| 5 | 成功解包与接受的质量结果共享不可变数据；摘要/文件名/格式/限制/完整MediaQuery入key。统一32MiB/128项/600s预算；160相同请求只解包/检查一次。失败不缓存，线程实际结束前保留槽位。 | Caption1.3.2 |
| 6 | 全部来源先排队，仅提前检查确定进入最终round-robin集合的候选；候选预算、排序和下载计数保持。同分按原候选/成员顺序稳定，来源速度不决定胜出。受控30次与公网2例完成。 | Caption1.3.3 |
| 7 | projected_at迁移后幂等创建partial index；事件、状态与恢复语义保持。1k/10k/100k历史结果和排序一致，空查询成本有界。 | Host3.9.3-host |

独立复核补齐真实transport丢外层count、嵌套失败码/别名矛盾、成功标志与失败码冲突、删除路径替换及同分完成顺序问题，均纳入回归。中间patch为迭代验收记录，没有创建或发布中间制品。

目标前验证最多3次、总预算10秒；调用内超时覆盖槽位、节流与冷却等待，不改共享客户端超时。copy不盲目重发，删源前身份复核失败则保留源；删除后有界复验不能确认则报告失败。

目录和已知文件删除绑定provider对象ID，旧provider不支持绑定时保留并失败关闭。服务端没有原子判空删除，无法锁住其他客户端在校验后的远端内容变动；ID绑定防止误删同路径替代对象。115 wire读取形状参考[OpenList作者维护的115 SDK](https://github.com/OpenListTeam/115-sdk-go/blob/main/fs.go)，本地HTTP替身验证；没有实际115账户写入验收。

## 完整本地验证

| 套件 | Passed | Skipped | Subtests | 耗时 |
| --- | ---: | ---: | ---: | ---: |
| Host | 754 | 1 | 333 | 124.49s |
| Download | 232 | 0 | 33 | 5.35s |
| Search | 725 | 2 | 152 | 43.95s |
| Rename | 496 | 0 | 28 | 9.45s |
| Sync | 160 | 0 | 80 | 8.69s |
| Caption | 506 | 0 | 0 | 10.84s |

3项跳过与基线一致，未算通过。Rename既有logger.warn弃用提示不影响通过。Host完整套件包含真实Feature进程、Unix RPC、丢回执、重复提交、取消和重启；storage专项RPC验证复制假成功、矛盾目录与替换对象零删除。

实际分别在对应目录执行以下命令，避免各Feature同名tests包混用：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider --tb=short tests
for module in download search rename sync; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider --tb=short tests
  )
done
(
  cd features/caption
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider --tb=short tests
)
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

隔离字幕依赖位于/tmp/telepiplex-audit-caption-deps，未更改生产依赖声明。五个Feature源码import/requirements校验、manifest与pyproject版本一致性通过。未构建或发布镜像/.tpx。SDK2.2.1、Sync2.1.1和HostAPI1.9保持；SDK空尾页证明、下载清理后第二次整树复验保留。

## 性能对照

每组同机至少30次。Rename为真实executor、不可变预检、内存provider，每次list/move模拟1ms；Caption为真实engine、合成正文、受控来源延迟。测量包含完整性检查，不能换算线上固定提速比例。

| 项目 | 旧/上一轮 | 新实现 | 结果 |
| --- | --- | --- | --- |
| 1000文件移动，30次/批量 | batch32：32move、254list；均值1.2761s | batch100：10move、78list；均值0.9836s | 平均减少22.9%；60次均1000成功，ID/SHA1/计数不变，0失败/重复 |
| 字幕CPU复用，30组×8请求 | 解包/检查各240次，中位86.926ms | 各30次，中位17.606ms | 减少79.7%重复处理等待，选择指纹一致 |
| 字幕搜索/下载重叠，30次/阶段 | cache-only中位92.120ms | pipeline中位51.037ms | 中位减少44.6%、P95减少37.8%；候选集合、选择、网络次数一致 |
| Host空待办，1k/10k/100k，各30次 | VMsteps7017/70017/700017；100k中位4.510ms | 三档均25steps；约0.0075ms | partial index生效，不随已处理历史线性扫描 |

count-only目录仍读取空尾页，所以list次数高于旧审计未修复测量；没有省略完整性检查。最终公网顺序只读双城之战S01E01、奇巧计程车S01E01，各最多6候选，两例matched且正文SHA-256与自检一致。公网波动不用于计算提速比例；未验证视频同步和翻译正确性。

## 压力与覆盖范围

- Host正常200条/并发32：200完成、800milestone投递、800重复去重、200事件、0失败。另200条恢复组注入10故障，恢复10、0失败。
- Telegram正常busy50ms与慢busy500ms：各queue10/并发1/重复点击2，两组均10正确终态、30消息段封闭、重复拒绝10。无残留键盘/错文本/任务/FD/render lock，drain全通过。慢busy增加前台等待，未进行投影重构。
- Caption500请求/并发24：500matched、0失败、3.401s；网络峰3；检索/下载各16次；CPU缓存17项/212125bytes；worker/共享任务残留0，槽位恢复3/2/2。
- 文件计数专项覆盖1/31/32/33/99/100/101/1000，缺省100与显式32逐对象核对；部分成功41完成/60留源；取消后恢复批次100/1，没有重复提交。

Host/Telegram压力实际运行Host、Unix RPC、PTB队列与投影，完整业务Feature handlers为合成实现，Telegram HTTP为替身；文件数量另由真实Rename executor配合存储fixture验证。Caption压力使用真实engine和合成字幕provider。未验收真实Telegram/Prowlarr/115/Plex部署端到端、线上限流和大文件传输速度。Search离线70条corpus实际执行8条、8成功，62条缺fixture跳过，不能算70条完成。

实际压力与公网命令如下，完整参数及输出随交付JSON保存：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_operation_pipeline.py --pipelines 200 --concurrency 32 --milestone-faults 0
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_operation_pipeline.py --pipelines 200 --concurrency 32 --milestone-faults 10
# 两组分别传 --busy-latency-ms 50 和500。
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_telegram_pipeline.py --pipelines 10 --concurrency 1 \
  --frontend-mode queue --telegram-latency-ms 50 --duplicate-clicks 2 --timeout-seconds 60
# 在features/caption目录：
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:src:../../sdk/src "$PY" \
  tools/pressure_test.py --requests 500 --concurrency 24 \
  --output /tmp/telepiplex-implementation-20261013/caption-pressure-final.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:src:../../sdk/src "$PY" \
  tools/live_provider_check.py --providers assrt,subhd,xunlei --limit 2 --candidates 6 \
  --output /tmp/telepiplex-implementation-20261013/caption-live-final.json
```

## 文件交付

共63个文件，按实施前本地SHA-256快照比较，未使用Git。无删除/重命名，原始自检、归档及旧构建产物保持。

| 操作 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | [README.md](/Users/young/Documents/telepiplex/README.md) | 当前版本、行为、默认批量与交付入口。 |
| 修改 | [README_EN.md](/Users/young/Documents/telepiplex/README_EN.md) | 英文当前版本、行为与升级说明。 |
| 修改 | [app/115bot.py](/Users/young/Documents/telepiplex/app/115bot.py) | Host patch版本。 |
| 修改 | [app/runtime/event_journal.py](/Users/young/Documents/telepiplex/app/runtime/event_journal.py) | 迁移后幂等创建待投影partial index。 |
| 新增 | [docs/audits/2026-10-13-iteration-results.json](/Users/young/Documents/telepiplex/docs/audits/2026-10-13-iteration-results.json) | 保存实际测试输出、测量/压力样本、复现脚本及清单。 |
| 新增 | [docs/audits/2026-10-13-iteration-results.md](/Users/young/Documents/telepiplex/docs/audits/2026-10-13-iteration-results.md) | 交付结果、文件用途、测量与上线观察。 |
| 新增 | [docs/superpowers/plans/2026-10-13-reliability-performance-iterations.md](/Users/young/Documents/telepiplex/docs/superpowers/plans/2026-10-13-reliability-performance-iterations.md) | 保存已授权计划、边界和逐轮验收。 |
| 修改 | [features/caption/CHANGELOG.md](/Users/young/Documents/telepiplex/features/caption/CHANGELOG.md) | 追加匹配、复用和流水迭代记录。 |
| 修改 | [features/caption/README.md](/Users/young/Documents/telepiplex/features/caption/README.md) | 同步该Feature当前版本、修复行为及兼容说明。 |
| 修改 | [features/caption/manifest.yaml](/Users/young/Documents/telepiplex/features/caption/manifest.yaml) | 同步Feature patch，capability与schema保持。 |
| 修改 | [features/caption/pyproject.toml](/Users/young/Documents/telepiplex/features/caption/pyproject.toml) | 同步包patch，保留SDK2.2.1依赖。 |
| 新增 | [features/caption/src/telepiplex_caption/cache.py](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption/cache.py) | 不可变成功CPU结果的统一内存/LRU/TTL预算。 |
| 修改 | [features/caption/src/telepiplex_caption/engine.py](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption/engine.py) | CPU共享复用、保守提前检查与确定性同分排序。 |
| 修改 | [features/caption/src/telepiplex_caption/matching.py](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption/matching.py) | 独立核验季集冲突，过滤技术年份，保留合法映射/年份。 |
| 修改 | [features/caption/tests/test_engine.py](/Users/young/Documents/telepiplex/features/caption/tests/test_engine.py) | 独立内容身份fixture与真实CPU并发回归。 |
| 新增 | [features/caption/tests/test_engine_cache.py](/Users/young/Documents/telepiplex/features/caption/tests/test_engine_cache.py) | 160请求复用、完整key、淘汰/TTL/失败/取消/槽位回归。 |
| 新增 | [features/caption/tests/test_engine_pipeline.py](/Users/young/Documents/telepiplex/features/caption/tests/test_engine_pipeline.py) | FIFO排队、重叠、候选集合及同分稳定性回归。 |
| 修改 | [features/caption/tests/test_matching.py](/Users/young/Documents/telepiplex/features/caption/tests/test_matching.py) | 季集/年份冲突、数字片名、合法整包及映射回归。 |
| 修改 | [features/caption/tools/pressure_test.py](/Users/young/Documents/telepiplex/features/caption/tools/pressure_test.py) | CPU共享残留、内存和槽位恢复压力门槛。 |
| 修改 | [features/download/README.md](/Users/young/Documents/telepiplex/features/download/README.md) | 同步该Feature当前版本、修复行为及兼容说明。 |
| 修改 | [features/download/manifest.yaml](/Users/young/Documents/telepiplex/features/download/manifest.yaml) | 同步Feature patch，capability与schema保持。 |
| 修改 | [features/download/pyproject.toml](/Users/young/Documents/telepiplex/features/download/pyproject.toml) | 同步包patch，保留SDK2.2.1依赖。 |
| 修改 | [features/download/src/telepiplex_download/client.py](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download/client.py) | 保留并校验完整wire证据，严格身份读取、ID绑定删除与copy前后验证。 |
| 修改 | [features/download/src/telepiplex_download/pacing.py](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download/pacing.py) | 调用内截止时间覆盖节流、槽位与冷却。 |
| 修改 | [features/download/src/telepiplex_download/service.py](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download/service.py) | 清理删除绑定快照ID，完整树快照读取保持私有逐扫描校验。 |
| 修改 | [features/download/src/telepiplex_download/snapshot_store.py](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download/snapshot_store.py) | 大树快照扫描也验证逐扫描续页承诺，保留二次完整复验。 |
| 新增 | [features/download/src/telepiplex_download/storage_listing.py](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download/storage_listing.py) | 私有wire解析与逐扫描continuation facade；不改SDK接口。 |
| 修改 | [features/download/tests/test_client_move_safety.py](/Users/young/Documents/telepiplex/features/download/tests/test_client_move_safety.py) | 原移动合同及绑定删除安全回归。 |
| 修改 | [features/download/tests/test_client_pacing.py](/Users/young/Documents/telepiplex/features/download/tests/test_client_pacing.py) | 局部预算及共享客户端隔离回归。 |
| 修改 | [features/download/tests/test_completion_integrity.py](/Users/young/Documents/telepiplex/features/download/tests/test_completion_integrity.py) | 下载清理绑定快照对象ID、替换保留和清理后整树复验回归。 |
| 修改 | [features/download/tests/test_feature_runtime.py](/Users/young/Documents/telepiplex/features/download/tests/test_feature_runtime.py) | 包版本、runtime与RPC合同回归。 |
| 新增 | [features/download/tests/test_legacy_move_verification.py](/Users/young/Documents/telepiplex/features/download/tests/test_legacy_move_verification.py) | 目标指纹、错误状态、预算、原ID删源、wire目录与恢复回归。 |
| 修改 | [features/rename/README.md](/Users/young/Documents/telepiplex/features/rename/README.md) | 同步该Feature当前版本、修复行为及兼容说明。 |
| 修改 | [features/rename/config.default.yaml](/Users/young/Documents/telepiplex/features/rename/config.default.yaml) | 缺省移动批量100，保留显式配置。 |
| 修改 | [features/rename/manifest.yaml](/Users/young/Documents/telepiplex/features/rename/manifest.yaml) | 同步Feature patch，capability与schema保持。 |
| 修改 | [features/rename/pyproject.toml](/Users/young/Documents/telepiplex/features/rename/pyproject.toml) | 同步包patch，保留SDK2.2.1依赖。 |
| 新增 | [features/rename/src/telepiplex_rename/directory_listing.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/directory_listing.py) | 统一严格分页、状态、身份和空尾页证据。 |
| 修改 | [features/rename/src/telepiplex_rename/file_executor.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/file_executor.py) | 写前/写后/清理统一读取，删除绑定身份，缺省批量100。 |
| 修改 | [features/rename/src/telepiplex_rename/processor.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/processor.py) | 处理器缺省批量100。 |
| 修改 | [features/rename/src/telepiplex_rename/runtime.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/runtime.py) | runtime缺省批量100。 |
| 修改 | [features/rename/src/telepiplex_rename/service.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/service.py) | 异步库存分页复用统一校验，保留取消和容量边界。 |
| 修改 | [features/rename/tests/test_config_schema_contract.py](/Users/young/Documents/telepiplex/features/rename/tests/test_config_schema_contract.py) | 配置范围/缺省值、版本和schema合同回归。 |
| 新增 | [features/rename/tests/test_directory_listing_safety.py](/Users/young/Documents/telepiplex/features/rename/tests/test_directory_listing_safety.py) | 目录矛盾零删除、真实客户端、绑定身份、批次边界和取消/恢复回归。 |
| 修改 | [features/rename/tests/test_feature_processor.py](/Users/young/Documents/telepiplex/features/rename/tests/test_feature_processor.py) | 完整分页和删除替身；处理器、缺省批量及版本回归。 |
| 修改 | [features/rename/tests/test_file_executor.py](/Users/young/Documents/telepiplex/features/rename/tests/test_file_executor.py) | 偏移分页和ID删除替身；执行、冲突、恢复及计数回归。 |
| 修改 | [features/rename/tests/test_file_first_inventory.py](/Users/young/Documents/telepiplex/features/rename/tests/test_file_first_inventory.py) | 库存fixture真实偏移分页，保留作品分组与附加内容回归。 |
| 新增 | [features/rename/tests/test_inventory_directory_integrity.py](/Users/young/Documents/telepiplex/features/rename/tests/test_inventory_directory_integrity.py) | 真实异步库存分页矛盾、空尾页、连续性和身份回归。 |
| 修改 | [features/rename/tests/test_regression_pressure.py](/Users/young/Documents/telepiplex/features/rename/tests/test_regression_pressure.py) | 完整分页与保留空尾页后的压力预算。 |
| 修改 | [features/search/README.md](/Users/young/Documents/telepiplex/features/search/README.md) | 同步该Feature当前版本、修复行为及兼容说明。 |
| 修改 | [features/search/manifest.yaml](/Users/young/Documents/telepiplex/features/search/manifest.yaml) | 同步Feature patch，capability与schema保持。 |
| 修改 | [features/search/pyproject.toml](/Users/young/Documents/telepiplex/features/search/pyproject.toml) | 同步包patch，保留SDK2.2.1依赖。 |
| 修改 | [features/search/src/telepiplex_search/live_pipeline_audit.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/live_pipeline_audit.py) | 回放仅点击实际渲染按钮。 |
| 修改 | [features/search/src/telepiplex_search/release_gate.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/release_gate.py) | 下划线范围识别，特别内容排除确认标题。 |
| 修改 | [features/search/src/telepiplex_search/service.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/service.py) | 根作品季度全季/单集菜单及旧action兼容。 |
| 新增 | [features/search/tests/test_audit_search_regressions.py](/Users/young/Documents/telepiplex/features/search/tests/test_audit_search_regressions.py) | F5–F7正反案例、实际菜单、投递元数据一致性回归。 |
| 修改 | [features/search/tests/test_config_schema_contract.py](/Users/young/Documents/telepiplex/features/search/tests/test_config_schema_contract.py) | 配置范围/缺省值、版本和schema合同回归。 |
| 修改 | [features/search/tests/test_feature_service.py](/Users/young/Documents/telepiplex/features/search/tests/test_feature_service.py) | 版本和制品名预期，保持业务交互回归。 |
| 修改 | [tests/business_flow_storage.py](/Users/young/Documents/telepiplex/tests/business_flow_storage.py) | RPC内存替身校验绑定删除对象ID。 |
| 修改 | [tests/test_bot_runtime_startup.py](/Users/young/Documents/telepiplex/tests/test_bot_runtime_startup.py) | Host新版本展示回归。 |
| 新增 | [tests/test_event_journal_cost.py](/Users/young/Documents/telepiplex/tests/test_event_journal_cost.py) | 历史规模、过滤排序、索引幂等和旧库迁移回归。 |
| 新增 | [tests/test_storage_safety_rpc.py](/Users/young/Documents/telepiplex/tests/test_storage_safety_rpc.py) | 真实Feature/Host/RPC复制假成功、矛盾目录、身份替换零删除回归。 |
| 修改 | [tests/test_technical_identity_migration.py](/Users/young/Documents/telepiplex/tests/test_technical_identity_migration.py) | 版本同步与技术身份保持回归。 |
| 修改 | [update.md](/Users/young/Documents/telepiplex/update.md) | 追加本轮更新记录，保留历史。 |

## 同步与上线观察

等待Syncthing **Up to Date / 最新**，确认同步至Unraid /mnt/user/archives/life hacker/telepiplex，由用户检查和发布。源码版本：Host3.9.3-host、Download2.2.1、Search2.6.2、Rename2.4.2、Caption1.3.3，SDK/Sync保持。

先更新Host与Download安全实现，再Search、Rename、Caption，逐轮观察代表性任务的对象数量、身份、正确终态和封存按钮。批量100重点观察限流、超时、部分成功；显式32不自动覆盖。出现新增丢失、重复写入或错误终态，停止该组件后续更新；移动批量可恢复32，保留同会话日志定位。没有自行执行Git、发布或Unraid操作。

