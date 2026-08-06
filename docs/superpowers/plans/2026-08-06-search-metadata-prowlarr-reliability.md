# Search 元数据、Rename 反查与 Prowlarr 交互集中修复实施计划

> **For Codex:** Use the test-driven-development, executing-plans, and verification-before-completion skills. Execute every step locally without Git.

**Goal:** 一次完成 Search 唯一结果确认消息、Prowlarr 运行中选择与极简文案、豆瓣富字段合并、直链补全、以及 `/m` 文件树反查的可确认恢复链路。

**Architecture:** 保留 `/s`、直接稳定链接和 `/m` 三种入口，但在作品身份锁定后统一进入 Search 元数据补全。Host 新增与 operation 状态消息分离的幂等 milestone；Search 提供结构化 `resolved / confirmation_required / unresolved` capability；Rename 持久化待确认状态并通过自己的回调恢复原任务。Prowlarr 只对当前 operation、当前消息、当前键盘和当前搜索阶段开放运行中选择。

**Tech Stack:** Python 3、asyncio、SQLite、pytest/unittest、python-telegram-bot、requests。

---

## Task 1: 豆瓣稀疏与富事实合并

**Files:**

- Modify: `features/search/src/telepiplex_search/adapters/douban.py`
- Test: `features/search/tests/test_douban_adapter.py`
- Test: `features/search/tests/test_direct_link.py`

- [ ] 增加同一 subject 的 `subject_abstract` 与 `rexxar` 均被读取的测试。
- [ ] 断言合并结果包含 `countries`、海报、原名和别名，列表字段去重且保序。
- [ ] 增加任一接口失败仍使用另一接口、稳定 ID 冲突拒绝、年份或媒体类型冲突禁止自动确认的测试。
- [ ] 运行新增测试，确认当前短路实现失败。
- [ ] 将 `_fetch_subject` 改为收集两个端点的有效事实后确定性合并，不在首个有效响应处返回。
- [ ] 将国家或地区纳入 `_normalize_payload` 的标准事实和缓存。
- [ ] 重跑豆瓣适配器与直链测试并确认通过。

验证命令：

```bash
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=features/search/src:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  features/search/tests/test_douban_adapter.py \
  features/search/tests/test_direct_link.py
```

## Task 2: 统一身份展示数据与独立 milestone

**Files:**

- Create: `features/search/src/telepiplex_search/identity_presentation.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `sdk/src/telepiplex_plugin_sdk/host_client.py`
- Modify: `app/runtime/runtime_broker.py`
- Modify: `app/runtime/interaction_coordinator.py`
- Modify: `app/handlers/interaction_handler.py`
- Modify: `app/115bot.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `tests/test_runtime_broker.py`
- Test: `tests/test_interaction_coordinator.py`
- Test: `tests/test_interaction_handler.py`
- Test: `tests/test_bot_runtime_startup.py`

- [ ] 为身份标题增加纯函数测试：中英文名并存显示 `中文名 (English Title)`，缺失时按既定顺序降级。
- [ ] 断言身份展示包含海报、年份、国家或地区、媒体类型、最终剧集范围和已验证来源。
- [ ] 断言 milestone ID 由稳定媒体身份和范围生成，相同输入稳定、不同范围不同。
- [ ] 增加 SDK `publish_operation_milestone` 请求格式测试。
- [ ] 增加 Broker 参数校验、插件 operation 所有权、幂等投递和图片失败降级文本测试。
- [ ] 运行新增测试，确认 Host 尚无 milestone 合同且 Search 唯一结果无独立消息。
- [ ] 在 coordinator 中持久化 `(operation_id, milestone_id)` 唯一记录，并提供投递领取、完成和失败释放方法。
- [ ] 在 Broker 和 `115bot` 装配 operation milestone sink；sink 只根据 operation 记录寻找聊天目标。
- [ ] 在 SDK 增加 milestone 调用，支持 `text` 与可选 `photo_url`。
- [ ] 在 Search 完成候选、范围和元数据冻结后发布独立 milestone，再进入 Prowlarr。
- [ ] 确认 milestone 不绑定或编辑 operation 的状态消息。
- [ ] 重跑 Host、SDK 和 Search 相关测试。

milestone 请求合同：

```python
{
    "method": "operation.milestone",
    "params": {
        "operation_id": "op-123",
        "milestone_id": "media-<stable-digest>",
        "text": "繁花 (Blossoms Shanghai)\n2023｜中国大陆｜剧集｜全剧",
        "photo_url": "https://example.invalid/poster.jpg",
    },
}
```

## Task 3: 首次候选与直接稳定链接

**Files:**

- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/candidate_presenter.py`
- Modify: `features/search/src/telepiplex_search/entities.py`
- Test: `features/search/tests/test_feature_service.py`
- Test: `features/search/tests/test_candidate_presenter.py`
- Test: `features/search/tests/test_direct_link.py`

- [ ] 增加首次豆瓣候选展示海报、国家或地区、年份、媒体类型的测试。
- [ ] 增加唯一硬匹配跳过候选按钮但仍发布独立身份 milestone 的测试。
- [ ] 增加直接豆瓣稳定链接不走普通候选发现、精确读取后进入相同补全和 milestone 的测试。
- [ ] 增加稳定链接精确读取失败不降级到模糊搜索的测试。
- [ ] 运行新增测试，确认当前展示和独立消息行为失败。
- [ ] 把 `countries` 与海报贯通事实、实体图、候选动作和最终 presentation。
- [ ] 将唯一 `/s` 与直接链接在 `_select_candidate` 后统一调用身份展示与 milestone。
- [ ] 重跑候选、直链和 Feature service 测试。

## Task 4: Search capability 的结构化解析与确认

**Files:**

- Modify: `features/search/src/telepiplex_search/service.py`
- Modify: `features/search/src/telepiplex_search/runtime.py`
- Test: `features/search/tests/test_metadata_capability.py`
- Test: `features/search/tests/test_feature_service.py`

- [ ] 增加严格唯一匹配返回 `status=resolved`、元数据、命名数据和 presentation 的测试。
- [ ] 增加同年同类型候选不唯一返回 `status=confirmation_required` 和至多五个稳定候选引用的测试。
- [ ] 增加年份缺失、类型不明、事实冲突时不自动选择的测试。
- [ ] 增加通过稳定候选引用调用确认方法、精确读取并返回 `resolved` 的测试。
- [ ] 增加伪造或不匹配候选引用返回 `unresolved` 的测试。
- [ ] 运行新增测试，确认当前 ambiguity 异常行为失败。
- [ ] 将 `resolve_metadata` 改为结构化结果，保留严格媒体元数据校验。
- [ ] 增加 `confirm_metadata` capability method，候选引用只包含可持久化稳定来源与 ID。
- [ ] 确认 capability 不依赖 Search 进程内候选下标。
- [ ] 重跑元数据 capability 和 Feature service 测试。

结构化响应：

```python
{
    "status": "confirmation_required",
    "query": "The Office",
    "probe": {"year_hint": 2005, "content_shape": "series"},
    "candidates": [
        {
            "ref": "douban:1478064",
            "title": "办公室",
            "original_title": "The Office",
            "year": 2005,
            "countries": ["美国"],
            "media_type": "series",
            "poster_url": "https://example.invalid/poster.jpg",
        }
    ],
}
```

## Task 5: `/m` 探针、待确认持久化与原任务恢复

**Files:**

- Modify: `features/rename/src/telepiplex_rename/content_probe.py`
- Modify: `features/rename/src/telepiplex_rename/jobs.py`
- Modify: `features/rename/src/telepiplex_rename/service.py`
- Modify: `features/rename/src/telepiplex_rename/runtime.py`
- Test: `features/rename/tests/test_content_probe.py`
- Test: `features/rename/tests/test_feature_service.py`
- Test: `features/rename/tests/test_jobs.py`

- [ ] 增加资源名中年份位于 `S01` 后仍能提取的测试。
- [ ] 增加 `resolved` 直接持久化元数据、发布 milestone、继续同一 job 的测试。
- [ ] 增加 `confirmation_required` 保存 query、probe、候选引用并进入 `awaiting_input` 的测试。
- [ ] 增加 Rename 候选键盘使用自身 callback namespace、选择后调用 `confirm_metadata` 并恢复同一 job 的测试。
- [ ] 增加重复回调、进程重启和已确认未组织恢复不重复下载、不重复搜索、不重复 milestone 的测试。
- [ ] 增加退出确认后保留下载内容并执行既有未整理目录策略的测试。
- [ ] 运行新增测试，确认当前异常终止行为失败。
- [ ] 从完整顶层资源名先提取 `year_hint`，再清理季集、质量和来源标记生成 `identity_query`。
- [ ] 扩展 durable job 状态和结果字段以保存待确认与已确认元数据。
- [ ] 在 Rename callback 路由中处理候选选择、退出和恢复。
- [ ] 让 `_run_organization` 消费结构化 Search capability 结果，并从持久化检查点继续。
- [ ] 重跑 Rename 全量测试。

## Task 6: Prowlarr 运行中序号选择门禁

**Files:**

- Modify: `app/handlers/interaction_handler.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `tests/test_interaction_handler.py`
- Test: `features/search/tests/test_feature_service.py`

- [ ] 增加 `running + prowlarr_search + opt-in + 当前消息 + 当前键盘 + 当前 namespace` 允许 callback 的 Host 测试。
- [ ] 增加任一条件缺失时继续拦截的参数化测试。
- [ ] 增加增量结果出现后选择序号会冻结选择、取消剩余搜索任务并立即开始提交的 Search 测试。
- [ ] 增加迟到搜索器结果不会改写冻结选择或重复提交的测试。
- [ ] 运行新增测试，确认当前 operation gate 拦截。
- [ ] Search 在 Prowlarr running action details 中显式声明阶段与运行中交互许可。
- [ ] Host 只对当前渲染键盘内的 callback 开放门禁，不全局开放 running callback。
- [ ] 重跑 Host 交互和 Search 增量搜索测试。

## Task 7: Prowlarr 极简文案与种子状态

**Files:**

- Modify: `features/search/src/telepiplex_search/release_identity.py`
- Modify: `features/search/src/telepiplex_search/release_report.py`
- Modify: `features/search/src/telepiplex_search/service.py`
- Test: `features/search/tests/test_release_identity.py`
- Test: `features/search/tests/test_release_report.py`
- Test: `features/search/tests/test_feature_service.py`

- [ ] 增加标题测试：运行中 `🔍 中文名 (English Title)`，结束时仅 emoji 改为 `✅`。
- [ ] 增加搜索器统计测试：仅显示 `搜索器 n/(m-x)，离线 x`。
- [ ] 增加规格归一测试：`4K/UHD/2160p -> 2160p`、`HEVC/H265/x265 -> x265`、`AVC/H264/x264 -> x264`，去重并保留稳定次序。
- [ ] 增加同 infohash 聚合的种子测试：最大显式 seeders `>=3` 为活种，`1-2` 为疑似死种，全显式 `0` 为死种，无可解析值为疑似死种。
- [ ] 断言结果不显示原始 seeders 数、发布组、索引器名、可用数、过滤数、异常数或音轨。
- [ ] 运行新增测试，确认当前文案和阈值失败。
- [ ] 在 release identity 层规范规格并按 infohash 合并结果。
- [ ] 在 report 层只生成以下层级：

```text
✅ 繁花 (Blossoms Shanghai)
搜索器 5/(6-1)，离线 1

① 2160p · WEB-DL · x265 · DV
   58.4 GB｜活种
```

- [ ] 重跑 release 与 Feature service 测试。

## Task 8: 版本、构建副本与合同同步

**Files:**

- Modify: `app/115bot.py`
- Modify: `app/runtime/plugin_contract.py`
- Modify: `sdk/pyproject.toml`
- Modify: `sdk/src/telepiplex_plugin_sdk.egg-info/PKG-INFO`
- Modify: `features/search/manifest.yaml`
- Modify: `features/search/pyproject.toml`
- Modify: `features/search/src/telepiplex_search.egg-info/PKG-INFO`
- Modify: `features/search/README.md`
- Modify: `features/rename/manifest.yaml`
- Modify: `features/rename/pyproject.toml`
- Modify: `features/rename/src/telepiplex_rename.egg-info/PKG-INFO`
- Modify: `features/rename/README.md`
- Modify: `tests/test_technical_identity_migration.py`
- Modify: `tests/test_bot_runtime_startup.py`
- Modify: matching files under `features/search/build/lib/`, `features/rename/build/lib/`, and `sdk/build/lib/`

- [ ] 在功能测试稳定后更新 Host API、Host、SDK、Search 和 Rename 的源码版本合同。
- [ ] 更新 manifest 的 Host API 与 SDK 依赖范围，保持技术身份不变。
- [ ] 更新 README 示例和源合同测试中的版本。
- [ ] 使用项目现有构建方式刷新 build/lib 与 egg-info，随后用 `cmp` 或内容测试确认副本一致。
- [ ] 运行技术身份、启动合同和 Feature source contract 测试。

计划版本：

```text
Host API  1.4
Host      v3.4.14-host
SDK       1.2.0
Search    1.6.0
Rename    1.1.0
```

## Task 9: 完整本地验证

- [ ] 运行 Search Feature 全量测试。
- [ ] 运行 Rename Feature 全量测试。
- [ ] 运行 Host 全量测试。
- [ ] 运行 Download、Sync 和 Caption Feature 全量回归。
- [ ] 运行 Python 语法检查与关键源码/build 副本一致性检查。
- [ ] 确认 `.git` 和 `.worktrees` 不存在、`.stfolder` 存在。
- [ ] 记录实际测试数量、跳过项和任何外部服务未覆盖边界。
- [ ] 汇总所有新增、修改、删除或重命名文件与目的。
- [ ] 提醒用户等待 Syncthing 显示 `Up to Date / 最新`，不执行发布。

完整验证命令：

```bash
cd /Users/young/Documents/telepiplex

PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

## 交付边界

- 只在 `/Users/young/Documents/telepiplex` 修改与本地测试。
- 不运行任何 Git 命令，不创建 `.git` 或 `.worktrees`。
- 不连接 GitHub、不提交、不推送、不打 tag、不发布。
- 音轨展示只保留为下一批需求，本批测试明确保证不会误显示。
