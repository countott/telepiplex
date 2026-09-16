# Telepiplex 剧集首播年份交付记录

日期：2026-09-16。状态：本地实现与验证完成，未发布。

## 本次结果

真人与动画剧集的作品根目录采用 `中文名 (首播年份) ⋯ Title`。同一作品的所有季共用作品首播年；季目录、视频、字幕不重复加年。英文/Romaji 选择、季集编号、现有季目录结构和字幕命名规则保持。

```text
中文名 (2014) ⋯ Title/
├── Title Season 01/
│   └── Title S01E01.mkv
└── Title Season 02/
    ├── Title S02E01.mkv
    └── Title S02E01.chi.srt
```

Search 将已验证的作品根年份冻结到公开 v2 `identity.year`。首选作品根缺年时，按既有来源绑定优先级寻找有值的同作品根资料；分季页面、未验证来源与其他 AniList 发行条目不能提供首播年。来源链接选择了某季或单集，但实际资料为已验证的整剧时，仍可使用整剧年份。

Search 私有合同保留发行条目年和范围年，片源检索与季集排序逻辑未更改。电影继续使用自身影片年份。可靠首播年缺失或不是有效四位年份时，新任务不生成完整公开合同；Rename 收到无有效年份的剧集合同则保留原位，不从 release 名称或文件时间推断。

v2 字段结构、稳定 metadata_id 算法、Host API 和 SDK 版本保持。已有冻结任务的元数据不会被本次升级改写；旧合同已保存的年份仍按冻结值消费，不进行历史任务或存量媒体批量迁移。

## 版本

| 组件 | 改动前 | 改动后 |
| --- | --- | --- |
| Search | 2.3.0 | 2.3.1 |
| Rename | 2.2.0 | 2.2.1 |

Host 3.7.0、SDK 2.2.0、Download 2.1.2、Sync 2.0.3、Caption 0.1.6 均未提升。两个 Feature 的 manifest 与 pyproject 一致。本次未新增 TVDB Aired Order 强制规则、Absolute 迁移、Special 或合集业务。

## 文件清单

与本轮修改前 SHA-256 快照对比，共修改以下 25 个工作文件；新增本交付记录，没有删除或重命名文件。构建工具从源码刷新两个 Feature 的 `build/lib/` 副本，构建副本不计入下表。

| 修改文件 | 目的 |
| --- | --- |
| [README.md](/Users/young/Documents/telepiplex/README.md) | 同步剧集根目录示例、版本及升级顺序。 |
| [README_EN.md](/Users/young/Documents/telepiplex/README_EN.md) | 同步英文说明、根目录示例、版本及升级顺序。 |
| [features/rename/README.md](/Users/young/Documents/telepiplex/features/rename/README.md) | 说明根目录加首播年、内部名称保持和缺年时保留原位。 |
| [features/rename/manifest.yaml](/Users/young/Documents/telepiplex/features/rename/manifest.yaml) | 提升 Feature 发布版本。 |
| [features/rename/pyproject.toml](/Users/young/Documents/telepiplex/features/rename/pyproject.toml) | 同步包版本；SDK 固定依赖仍为 2.2.0。 |
| [features/rename/src/telepiplex_rename.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename.egg-info/PKG-INFO) | 通过真实本地构建重新生成包版本元信息，未手工编辑。 |
| [features/rename/src/telepiplex_rename/media_naming.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/media_naming.py) | 统一年份校验，通用剧集根目录加入年份，电影命名行为保持。 |
| [features/rename/src/telepiplex_rename/tvdb_rename.py](/Users/young/Documents/telepiplex/features/rename/src/telepiplex_rename/tvdb_rename.py) | 已确认剧集与既有 TVDB 规划入口的根目录加入首播年；季集文件命名保持。 |
| [features/rename/tests/test_feature_processor.py](/Users/young/Documents/telepiplex/features/rename/tests/test_feature_processor.py) | 更新根目录期望和版本断言；验证缺少年份时视频、字幕均无存储变更。 |
| [features/rename/tests/test_file_first_processor.py](/Users/young/Documents/telepiplex/features/rename/tests/test_file_first_processor.py) | 更新规范路径、冲突和逐文件失败恢复的目录期望。 |
| [features/rename/tests/test_media_auto_rename.py](/Users/young/Documents/telepiplex/features/rename/tests/test_media_auto_rename.py) | 补充测试元数据首播年并更新根目录期望，内部文件断言保持。 |
| [features/rename/tests/test_media_metadata_v2.py](/Users/young/Documents/telepiplex/features/rename/tests/test_media_metadata_v2.py) | 覆盖跨季根目录年一致、根目录之外不加年、年份无效时拒绝及 Romaji。 |
| [features/rename/tests/test_tvdb_rename.py](/Users/young/Documents/telepiplex/features/rename/tests/test_tvdb_rename.py) | 补充年份夹具并更新目录期望；原编号、源路径及未匹配文件测试继续通过。 |
| [features/search/README.md](/Users/young/Documents/telepiplex/features/search/README.md) | 说明作品首播年份的选择、公开合同语义及当前版本。 |
| [features/search/manifest.yaml](/Users/young/Documents/telepiplex/features/search/manifest.yaml) | 提升 Feature 发布版本。 |
| [features/search/pyproject.toml](/Users/young/Documents/telepiplex/features/search/pyproject.toml) | 同步包版本；SDK 固定依赖仍为 2.2.0。 |
| [features/search/src/telepiplex_search.egg-info/PKG-INFO](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search.egg-info/PKG-INFO) | 通过真实本地构建重新生成包版本元信息，未手工编辑。 |
| [features/search/src/telepiplex_search/media_metadata_v1.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/media_metadata_v1.py) | 按已验证的整剧来源补齐 root_year，排除分季与后续发行条目年份；保留私有条目年。 |
| [features/search/src/telepiplex_search/media_metadata_v2.py](/Users/young/Documents/telepiplex/features/search/src/telepiplex_search/media_metadata_v2.py) | 将剧集 root_year 冻结为 v2 identity.year；缺失有效首播年时拒绝投影。 |
| [features/search/tests/test_config_schema_contract.py](/Users/young/Documents/telepiplex/features/search/tests/test_config_schema_contract.py) | 同步 Search 版本断言；配置 schema 保持。 |
| [features/search/tests/test_feature_service.py](/Users/young/Documents/telepiplex/features/search/tests/test_feature_service.py) | 同步 Search 版本与构建示例断言。 |
| [features/search/tests/test_media_metadata_v1.py](/Users/young/Documents/telepiplex/features/search/tests/test_media_metadata_v1.py) | 覆盖作品根年份优先级、缺值补齐、排除分季/未验证来源和 AniList 发行年分离。 |
| [features/search/tests/test_media_metadata_v2.py](/Users/young/Documents/telepiplex/features/search/tests/test_media_metadata_v2.py) | 覆盖全剧、整季、单集共用首播年，无效年拒绝、冻结数据不变和电影年份不变。 |
| [tests/test_technical_identity_migration.py](/Users/young/Documents/telepiplex/tests/test_technical_identity_migration.py) | 同步两个 Feature 的包版本断言，SDK 版本保持。 |
| [tests/test_unraid_publish_script.py](/Users/young/Documents/telepiplex/tests/test_unraid_publish_script.py) | 更新发布脚本测试中的模拟版本标签；发布脚本源文件未修改。 |

新增：[本交付记录](/Users/young/Documents/telepiplex/docs/audits/2026-09-16-series-premiere-year.md)，保存本轮范围、版本、改动清单与实际验证。

## 实际验证

| 范围 | passed | skipped | subtests passed |
| --- | ---: | ---: | ---: |
| Search 全量 | 670 | 2 | 152 |
| Rename 全量 | 392 | 0 | 29 |
| Host 相关合同、SDK 和版本测试 | 37 | 0 | 48 |
| 合计 | 1099 | 2 | 229 |

最终文档修订后，额外重跑部署合同：12 passed / 24 subtests passed（与表中已有用例重复，不重复计入总数）。Rename 保留一条既有 `logger.warn` 弃用提示。本轮未重跑 Host 全量和其余三个未改动 Feature 的测试。

实际运行命令：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3

for module in search rename; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_deployment_contract.py tests/test_technical_identity_migration.py \
  tests/test_unraid_publish_script.py tests/test_media_metadata_v2.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_deployment_contract.py
```

本地构建也已执行：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
for item in search:2.3.1 rename:2.2.1; do
  module=${item%%:*}
  version=${item#*:}
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
    "$PY" tools/build_feature.py "features/$module" \
    "/tmp/telepiplex-series-year-packages/$module-$version.tpx" \
    --repository local/telepiplex --branch main \
    --commit 0000000000000000000000000000000000000000
done
```

两包通过 `verify_tpx()` 完整性校验。Search 包内 52 个、Rename 包内 24 个 Python 文件分别与当前源码逐字节一致，两包均携带未变更的 SDK 2.2.0。

| 本地验证包 | SHA-256 |
| --- | --- |
| [search-2.3.1.tpx](/tmp/telepiplex-series-year-packages/search-2.3.1.tpx) | `a25e5931f8cada59391b3c8bc5c2d21908fff6a5d6a22bcfb382873582b58cbb` |
| [rename-2.2.1.tpx](/tmp/telepiplex-series-year-packages/rename-2.2.1.tpx) | `66f9fe2ea84776da20779628853f2702b348adc79fd0807c07cd3ffbb0929502` |

其他检查：改动 Python 文件通过 AST 语法解析；四份现行 README 的表格列数和本地链接有效。Host 版本文件、SDK 合同/版本、Rename 的处理器、服务、文件执行器、文件计划、字幕及操作模块，以及其他三个 Feature 的版本声明均与修改前 SHA-256 一致。`.git` 与 `.worktrees` 不存在，`.stfolder` 保留。

本轮仅验证代码和模拟存储流程，未操作真实媒体，未执行 Plex 扫描或自动匹配验证。

## 交付

等待 Syncthing 显示 **Up to Date / 最新**，同步目标为 `/mnt/user/archives/life hacker/telepiplex`，再由用户在 Unraid 检查和发布。本次没有执行 Git 或发布操作，Unraid 发布脚本源文件也未修改。

已使用 SDK 2.2.0 的部署，本轮先更新 Search 2.3.1，再更新 Rename 2.2.1，两者完成后再发起新任务。更早版本升级 SDK 的顺序见现行 README；本次本地验证包不代表已经发布的 Release。
