# Telepiplex 媒体命名与元数据交付记录

日期：2026-09-15。状态：Mac 本地代码、版本与验证完成，尚未发布。

## 1. 本次落地范围

| 场景 | 结果 |
| --- | --- |
| 单部电影目录 | `中文名 (年份) ⋯ Title` |
| 电影视频 | `Title.ext`，不主动增加年份或中文 |
| 剧集根目录 | `中文名 ⋯ Title`，不新增年份 |
| 剧集季目录与文件 | 继续现有 `Title Season xx` / `Title SxxExx.ext` 规则 |
| 日本动画 | 以来源提供的 Romaji 为优先外文标题；缺失时使用已验证英文 |
| 真人作品及非日本动画 | 使用已验证英文，日本真人作品也遵守此规则 |

双语分隔符精确为 `⋯`（U+22EF），两侧各一个半角空格。同一作品的根目录、季目录、视频和已支持的关联字幕统一使用同一外文主标题。目标名称继续经过现有跨平台清洗；中文重复尾缀清理行为保留。

### 电影年份

在已确认属于当前电影的来源中，依次考虑当前确认的发行条目/作品根、候选锚点，以及其余已验证来源绑定，选择首个有效的四位年份。高优先级来源缺值或年份无效时继续回退；低优先级来源给出不同年份不会推翻高优先级有效值。相关作品、未验证绑定以及非电影事实不能补入年份。

只有全部可用同作品来源都没有有效年份时，Search 暂缓形成完整合同；Rename 收到缺失年份的旧合同也保留文件原位。下载标题中的年份和文件时间不作为补值来源。

### 元数据传递

`media_metadata v2` 的 `identity` 增加可选但必须成对出现的字段：

```json
{
  "title_en": "Verified English Title",
  "title_original": "原语种标题",
  "naming_title": "Source Romaji",
  "naming_title_kind": "romaji"
}
```

上述仅为 identity 字段节选，非完整合同。`naming_title_kind` 为 `english` 或 `romaji`；英文命名值必须与 `title_en` 一致。可靠 Romaji 存在但英文缺失时，允许 `title_en` 为空，不把 Romaji 冒充英文。Romaji 只允许动画分类；Search 进一步根据分类、产地，以及产地缺失时的原语言证据确定日本动画。

Search 在确认投影时冻结命名字段，Download 使用现有深拷贝/持久化逻辑传递，Rename 只消费该值。确认名称的既有冻结机制保留，没有新增另一套用户名称覆盖机制。v2 的 schema 版本及稳定身份 ID 算法不变；没有新增 v3 或修改 Host API。

旧 v2 合同没有新字段时仍可被新版读取，Rename 回退到原有 `title_en`。旧消费者会拒绝新字段，不能用“旧合同可读”替代升级消费者的要求。

### 保持现有业务

合集、Absolute Order、Special 不新增支持。字幕 `.chi` 规则、扩展名与防重名策略、sidecar 范围、视频选择、分段/多版本处理、目标名称清洗、冲突处理、同哈希恢复、移动与空目录清理继续沿用现有实现。下载和整理操作范围、存储位置、Plex 匹配与扫描行为没有改变。

本次修改的是 Telepiplex 源码与本地测试，未操作 115、Unraid 媒体目录或 Plex，也未修改实际运行配置。

## 2. 版本

| 组件 | 改动前 | 改动后 | 原因 |
| --- | --- | --- | --- |
| Host | 3.6.14 | 3.7.0 | 随镜像携带新版共享 SDK |
| SDK | 2.1.1 | 2.2.0 | v2 命名字段扩展与兼容校验 |
| Search | 2.2.2 | 2.3.0 | 命名标题冻结、日本动画判定及年份回退 |
| Rename | 2.1.2 | 2.2.0 | 新目录格式与命名字段消费 |
| Download | 2.1.1 | 2.1.2 | 更新 SDK 固定依赖，生产业务代码保持 |
| Sync | 2.0.2 | 2.0.3 | 更新 SDK 固定依赖，生产业务代码保持 |
| Caption | 0.1.5 | 0.1.6 | 更新 SDK 固定依赖，继续占位实现 |

五个 Feature 的 manifest 与 pyproject 版本一致，SDK 固定依赖均为 2.2.0。Echo 示例仅更新 SDK 依赖，自身仍为 1.0.1。Host API 保持 1.7。构建总是从工作区打包 SDK，所以三个业务未改动的 Feature 也提升补丁版本，避免重用原有包身份。

## 3. 文件清单

与操作前 SHA-256 工作文件快照比对：60 个已有文件发生变化；新增 5 个 Caption 构建元信息文件和本记录；没有删除或重命名工作文件。下表逐项列出用途。历史归档及旧计划未改写。

### 修改文件

| 文件 | 用途 |
| --- | --- |
| [README.md](/Users/young/Documents/telepiplex/README.md) | 同步中文命名示例、当前版本、模块说明及升级顺序。 |
| [README_EN.md](/Users/young/Documents/telepiplex/README_EN.md) | 同步英文说明、命名示例、当前版本及升级顺序。 |
| [app/115bot.py](/Users/young/Documents/telepiplex/app/115bot.py) | Host 版本更新为 v3.7.0-host。 |
| [examples/echo_feature/pyproject.toml](/Users/young/Documents/telepiplex/examples/echo_feature/pyproject.toml) | 示例固定 SDK 依赖更新为 2.2.0，示例自身版本保持。 |
| [examples/echo_feature/src/telepiplex_echo.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/examples/echo_feature/src/telepiplex_echo.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [examples/echo_feature/src/telepiplex_echo.egg-info/requires.txt](/Users/young/Documents/telepiplex/examples/echo_feature/src/telepiplex_echo.egg-info/requires.txt) | 由本地构建重新生成 SDK 依赖声明。 |
| [features/caption/README.md](/Users/young/Documents/telepiplex/features/caption/README.md) | 同步当前版本、构建示例和本模块相关命名/兼容说明。 |
| [features/caption/manifest.yaml](/Users/young/Documents/telepiplex/features/caption/manifest.yaml) | 同步 Feature 发布版本；capability、Host API 范围和配置 schema 保持。 |
| [features/caption/pyproject.toml](/Users/young/Documents/telepiplex/features/caption/pyproject.toml) | 同步 Feature 包版本及 SDK 2.2.0 固定依赖。 |
| [features/download/README.md](/Users/young/Documents/telepiplex/features/download/README.md) | 同步当前版本、构建示例和本模块相关命名/兼容说明。 |
| [features/download/manifest.yaml](/Users/young/Documents/telepiplex/features/download/manifest.yaml) | 同步 Feature 发布版本；capability、Host API 范围和配置 schema 保持。 |
| [features/download/pyproject.toml](/Users/young/Documents/telepiplex/features/download/pyproject.toml) | 同步 Feature 包版本及 SDK 2.2.0 固定依赖。 |
| [features/download/src/telepiplex_download.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [features/download/src/telepiplex_download.egg-info/SOURCES.txt](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download.egg-info/SOURCES.txt) | 由本地构建重新生成源码/测试文件清单。 |
| [features/download/src/telepiplex_download.egg-info/requires.txt](/Users/young/Documents/telepiplex/features/download/src/telepiplex_download.egg-info/requires.txt) | 由本地构建重新生成 SDK 依赖声明。 |
| [features/download/tests/test_feature_runtime.py](/Users/young/Documents/telepiplex/features/download/tests/test_feature_runtime.py) | 验证 Romaji 字段在持久下载交接中原样深拷贝，并同步版本断言。 |
| [features/rename/README.md](/Users/young/Documents/telepiplex/features/rename/README.md) | 同步当前版本、构建示例和本模块相关命名/兼容说明。 |
| [features/rename/manifest.yaml](/Users/young/Documents/telepiplex/features/rename/manifest.yaml) | 同步 Feature 发布版本；capability、Host API 范围和配置 schema 保持。 |
| [features/rename/pyproject.toml](/Users/young/Documents/telepiplex/features/rename/pyproject.toml) | 同步 Feature 包版本及 SDK 2.2.0 固定依赖。 |
| [features/rename/src/telepiplex_rename.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [features/rename/src/telepiplex_rename.egg-info/SOURCES.txt](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename.egg-info/SOURCES.txt) | 由本地构建重新生成源码/测试文件清单。 |
| [features/rename/src/telepiplex_rename.egg-info/requires.txt](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename.egg-info/requires.txt) | 由本地构建重新生成 SDK 依赖声明。 |
| [features/rename/src/telepiplex_rename/media_metadata_v2.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/media_metadata_v2.py) | 消费冻结的命名标题；旧 v2 使用 title_en。 |
| [features/rename/src/telepiplex_rename/media_naming.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/media_naming.py) | 使用精确双语分隔符及电影目录年份；视频仅使用冻结外文标题。 |
| [features/rename/src/telepiplex_rename/tvdb_rename.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/tvdb_rename.py) | 剧集根目录、季目录与文件统一消费冻结外文标题。 |
| [features/rename/tests/test_feature_processor.py](/Users/young/Documents/telepiplex/features/rename/tests/test_feature_processor.py) | 更新目录期望；覆盖冻结 Romaji 的实际处理、字幕现行策略及缺少年份时无文件副作用。 |
| [features/rename/tests/test_file_first_processor.py](/Users/young/Documents/telepiplex/features/rename/tests/test_file_first_processor.py) | 同步逐文件流程的目标目录期望。 |
| [features/rename/tests/test_media_auto_rename.py](/Users/young/Documents/telepiplex/features/rename/tests/test_media_auto_rename.py) | 同步通用电影/剧集命名期望和电影元数据年份夹具。 |
| [features/rename/tests/test_media_metadata_v2.py](/Users/young/Documents/telepiplex/features/rename/tests/test_media_metadata_v2.py) | 覆盖 v2 电影年份、Romaji 剧集全层级一致、字幕后缀与旧合同兼容。 |
| [features/rename/tests/test_tvdb_rename.py](/Users/young/Documents/telepiplex/features/rename/tests/test_tvdb_rename.py) | 同步剧集根目录分隔符，保留现有季集编号行为。 |
| [features/search/README.md](/Users/young/Documents/telepiplex/features/search/README.md) | 同步当前版本、构建示例和本模块相关命名/兼容说明。 |
| [features/search/manifest.yaml](/Users/young/Documents/telepiplex/features/search/manifest.yaml) | 同步 Feature 发布版本；capability、Host API 范围和配置 schema 保持。 |
| [features/search/pyproject.toml](/Users/young/Documents/telepiplex/features/search/pyproject.toml) | 同步 Feature 包版本及 SDK 2.2.0 固定依赖。 |
| [features/search/src/telepiplex_search.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [features/search/src/telepiplex_search.egg-info/SOURCES.txt](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search.egg-info/SOURCES.txt) | 由本地构建重新生成源码/测试文件清单。 |
| [features/search/src/telepiplex_search.egg-info/requires.txt](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search.egg-info/requires.txt) | 由本地构建重新生成 SDK 依赖声明。 |
| [features/search/src/telepiplex_search/confirmed_enrichment.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/confirmed_enrichment.py) | AniList 补全准入复用日本动画判断。 |
| [features/search/src/telepiplex_search/media_metadata_v1.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/media_metadata_v1.py) | 在标题选择前提供分类和产地证据；电影年份按已验证同作品来源顺序补缺。 |
| [features/search/src/telepiplex_search/media_metadata_v2.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/media_metadata_v2.py) | 冻结独立 naming_title/naming_title_kind，保持英文与原文语义。 |
| [features/search/src/telepiplex_search/title_policy.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/title_policy.py) | 统一日本动画判断；真人与非日本动画选英文，日本动画优先来源 Romaji。 |
| [features/search/tests/test_config_schema_contract.py](/Users/young/Documents/telepiplex/features/search/tests/test_config_schema_contract.py) | 更新 Search 版本断言，配置 schema 不变。 |
| [features/search/tests/test_feature_service.py](/Users/young/Documents/telepiplex/features/search/tests/test_feature_service.py) | 更新 Search 服务当前版本断言。 |
| [features/search/tests/test_media_metadata_v1.py](/Users/young/Documents/telepiplex/features/search/tests/test_media_metadata_v1.py) | 覆盖电影年份首选、补缺、无效值、不同来源差异及排除无关来源。 |
| [features/search/tests/test_media_metadata_v2.py](/Users/young/Documents/telepiplex/features/search/tests/test_media_metadata_v2.py) | 覆盖独立命名字段、日漫分类与英文回退、候选冻结不被修改。 |
| [features/search/tests/test_title_policy.py](/Users/young/Documents/telepiplex/features/search/tests/test_title_policy.py) | 覆盖日本动画、日本真人及非日本动画的标题选择矩阵。 |
| [features/sync/README.md](/Users/young/Documents/telepiplex/features/sync/README.md) | 同步当前版本、构建示例和本模块相关命名/兼容说明。 |
| [features/sync/manifest.yaml](/Users/young/Documents/telepiplex/features/sync/manifest.yaml) | 同步 Feature 发布版本；capability、Host API 范围和配置 schema 保持。 |
| [features/sync/pyproject.toml](/Users/young/Documents/telepiplex/features/sync/pyproject.toml) | 同步 Feature 包版本及 SDK 2.2.0 固定依赖。 |
| [features/sync/src/telepiplex_sync.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/sync/src/telepiplex_sync.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [features/sync/src/telepiplex_sync.egg-info/requires.txt](/Users/young/Documents/telepiplex/features/sync/src/telepiplex_sync.egg-info/requires.txt) | 由本地构建重新生成 SDK 依赖声明。 |
| [features/sync/tests/test_feature_runtime.py](/Users/young/Documents/telepiplex/features/sync/tests/test_feature_runtime.py) | 同步 Sync 版本断言。 |
| [sdk/pyproject.toml](/Users/young/Documents/telepiplex/sdk/pyproject.toml) | SDK 版本更新为 2.2.0。 |
| [sdk/src/telepiplex_plugin_sdk.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/sdk/src/telepiplex_plugin_sdk.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py](/Users/young/Documents/telepiplex/sdk/src/telepiplex_plugin_sdk/media_metadata_v2.py) | 为 v2 增加成对的命名标题/类型校验，兼容旧合同，保持身份 ID 算法。 |
| [tests/test_bot_runtime_startup.py](/Users/young/Documents/telepiplex/tests/test_bot_runtime_startup.py) | 更新 Host 启动版本断言。 |
| [tests/test_deployment_contract.py](/Users/young/Documents/telepiplex/tests/test_deployment_contract.py) | 同步 Host/Feature 当前版本合同。 |
| [tests/test_feature_builder.py](/Users/young/Documents/telepiplex/tests/test_feature_builder.py) | 同步构建使用的 SDK 版本断言。 |
| [tests/test_media_metadata_v2.py](/Users/young/Documents/telepiplex/tests/test_media_metadata_v2.py) | 覆盖新字段成对校验、英文/Romaji 语义、深拷贝和稳定身份 ID。 |
| [tests/test_technical_identity_migration.py](/Users/young/Documents/telepiplex/tests/test_technical_identity_migration.py) | 同步当前技术身份版本断言。 |
| [tests/test_unraid_publish_script.py](/Users/young/Documents/telepiplex/tests/test_unraid_publish_script.py) | 同步发布脚本的本地模拟版本夹具；发布脚本本身未修改。 |

### 新增文件

| 文件 | 用途 |
| --- | --- |
| [features/caption/src/telepiplex_caption.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption.egg-info/PKG-INFO) | 由本地构建重新生成包版本与依赖元信息。 |
| [features/caption/src/telepiplex_caption.egg-info/SOURCES.txt](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption.egg-info/SOURCES.txt) | 由本地构建重新生成源码/测试文件清单。 |
| [features/caption/src/telepiplex_caption.egg-info/dependency_links.txt](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption.egg-info/dependency_links.txt) | 由本地构建生成包发现元信息。 |
| [features/caption/src/telepiplex_caption.egg-info/requires.txt](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption.egg-info/requires.txt) | 由本地构建重新生成 SDK 依赖声明。 |
| [features/caption/src/telepiplex_caption.egg-info/top_level.txt](/Users/young/Documents/telepiplex/features/caption/src/telepiplex_caption.egg-info/top_level.txt) | 由本地构建生成包发现元信息。 |
| [docs/audits/2026-09-15-media-naming-delivery.md](/Users/young/Documents/telepiplex/docs/audits/2026-09-15-media-naming-delivery.md) | 保存落地范围、版本、逐文件清单和实际验证结果。 |

### 构建副本

构建工具还从源码生成 SDK、五个 Feature 和 Echo 示例的 `build/lib/` 副本。这些是构建输出，未手工编辑；不纳入上述源码/文档快照的文件计数。包内全部 Python 源码已与各自 `src/` 文件逐一比对一致。`.egg-info` 的已有变更和新增文件已在上表单独列出。

## 4. 实际验证

### 完整测试

| 测试范围 | passed | skipped | subtests passed |
| --- | ---: | ---: | ---: |
| Host / SDK | 693 | 1 | 307 |
| Search | 666 | 2 | 136 |
| Download | 174 | 0 | 33 |
| Rename | 389 | 0 | 24 |
| Sync | 157 | 0 | 80 |
| Caption | 1 | 0 | 0 |
| 合计 | 2080 | 3 | 580 |

最后一轮上述套件全部通过。Rename 的缺少年份用例触发现有 `logger.warn` 的一条弃用提示，未为此修改文件执行流程。另在最终 README 修订后重跑部署合同测试：12 passed、24 subtests passed。

实际使用以下命令分别在对应目录执行；各 Feature 单独收集，避免同名 tests 包冲突：

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

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_deployment_contract.py
```

关键场景包含：日本真人不使用 Romaji、非日本动画不因日语字段而被误判、Romaji 穿过 Download 冻结交接到 Rename、目录及视频/字幕标题一致、元数据年份优先于 release 年份、无年份时零存储变更、旧合同兼容、新字段错误拒绝，以及现有冲突/幂等/清理回归。

这些是本地测试和模拟存储验证。真实提供方联网、115 文件操作、Unraid 文件系统及 Plex 自动匹配未执行，不能据此声称线上媒体已改名或 Plex 已重新匹配。

### 构建与包内校验

五个包使用真实本地构建工具生成，未连接当前项目 GitHub 仓库：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
for item in download:2.1.2 search:2.3.0 rename:2.2.0 sync:2.0.3 caption:0.1.6; do
  module=${item%%:*}
  version=${item#*:}
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
    "$PY" tools/build_feature.py "features/$module" \
    "/tmp/telepiplex-naming-packages/$module-$version.tpx" \
    --repository local/telepiplex --branch main \
    --commit 0000000000000000000000000000000000000000
done
```

每个包均通过 `verify_tpx()` 的完整性和内容校验；另外核对 manifest、wheel METADATA、SDK 固定依赖与 SDK wheel 版本。Feature 的全部 Python 文件及每份 SDK 的 12 个 Python 文件均与当前源码逐字节一致。包仅为本地验证输出，不作为已发布 Release。

| 包 | Feature Python 文件核对数 | SHA-256 |
| --- | ---: | --- |
| [download-2.1.2.tpx](/tmp/telepiplex-naming-packages/download-2.1.2.tpx) | 14 | `3f4d1c66d462019a41dcd78d1f5968c607070bdf3aa6fcc5c9721acf9297f187` |
| [search-2.3.0.tpx](/tmp/telepiplex-naming-packages/search-2.3.0.tpx) | 52 | `ae59673bad94451d12800c1d9f8da157ae7334a9b38540dc12a6247e3b9aeec7` |
| [rename-2.2.0.tpx](/tmp/telepiplex-naming-packages/rename-2.2.0.tpx) | 24 | `9402e14d3c96a337324775b8650c174c9c7c04b8e23d7487704584d5efebaa63` |
| [sync-2.0.3.tpx](/tmp/telepiplex-naming-packages/sync-2.0.3.tpx) | 13 | `4e334f4b24935a93d85ba2e6027a61bfcf7b43954f5ede6aac90a3d9fc33ee9e` |
| [caption-0.1.6.tpx](/tmp/telepiplex-naming-packages/caption-0.1.6.tpx) | 2 | `30669f0aefd0c1b522ab3eff64df0b39ce000869c963e832ce843bbe3273969b` |

### 其他检查

- 全部改动 Python 文件通过 AST 语法解析。
- 中英文 README 表格列数和本地链接通过检查。
- Rename 的 `file_executor.py`、`file_plan.py`、`file_facts.py`、`subtitles.py`、`operations.py`、`processor.py`、`service.py`，Download 的 `service.py`，Sync 的 `sync_service.py`，Caption 的 `runtime.py`，共 10 个业务文件与修改前 SHA-256 完全一致。
- 工作区 `.git`、`.worktrees` 均不存在；`.stfolder` 保留。

## 5. 交付与升级

1. 等待 Mac → Unraid 的 Syncthing 显示 **Up to Date / 最新**，目标为 `/mnt/user/archives/life hacker/telepiplex`。
2. 用户在 Unraid 检查和发布；本次没有执行 Git、创建 PR 或发布操作。发布脚本源文件未改动，不需要因此替换 User Scripts 脚本。
3. 正式发布后，既有部署先升级 Host，再升级已安装的 Download、Rename、Sync，最后升级 Search；Caption 同步依赖版本。SDK 随构建打包，无需单独安装。
4. 全部消费者升级完成后再提交新任务。旧 v2 任务保留原已确认英文标题；本次不会自动重新规划或批量迁移现有媒体。
