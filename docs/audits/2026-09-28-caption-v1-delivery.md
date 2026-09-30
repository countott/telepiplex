# Caption 1.0.0 本地开发交付

日期：2026-09-28。仅完成 Mac 本地源码和验证；没有执行 Git，没有连接当前项目的 GitHub 仓库，没有发布。Syncthing 完成状态需由用户确认。

## 已实现范围

- 下载链路：Download 完成 → Rename 确认身份和文件树 → Caption 查找与检查外挂字幕 → Rename 刷新文件树并统一整理；既有 Plex 下一步保持。
- 媒体库：`/caption scan` 复用 Rename 目录扫描与分页，按已有视频名称补齐旁挂字幕。
- 独立查询：`/caption 片名 年份` 或剧集季集 query 调用 Search 元数据能力；无需视频文件，交给 Rename 生成目录和字幕名。
- 只处理外挂字幕，不检测内置字幕。文件只用 `chi` / `cht` 语言后缀。
- 英语片严格执行「简中双语 ASS → 简中双语 SRT → 简中单语 SRT → 繁中」；其他原始语言执行「简中单语 → 繁中单语」。简繁、双语和基本质量由正文判断，不只相信文件名。
- 保护已有不同内容文件；支持分块续传、重复执行、取消和重启后的中断提示。遇到来源不可用、未知身份或不合格字幕时报告原因。

## 源码版本与升级顺序

Host **3.9.0**（Host API **1.9**）→ Download **2.2.0** → Search **2.5.0** → Rename **2.4.0** → Caption **1.0.0**。已安装 Sync 可更新到 **2.1.1**，仅同步 SDK。SDK **2.2.1** 随 Feature 包携带，无需单独安装。

Caption 依赖 Rename；Rename 通过可选 capability 调用 Caption，安装关系不成环。Caption 未安装或停用时原有整理流程继续可用。

## 真实来源与质量证据

无需 API key 已实际取得并检查公开 ASSRT 字幕：英语《星际穿越》简中双语 ASS、中文《流浪地球》简中单语 SRT、日语《你的名字》简中单语 ASS。另使用真实《千与千寻》RAR 验证解包、简繁识别与英文字幕排除。详细编号、字幕量和检索上限见 [来源实测记录](../../features/caption/docs/provider-evidence-2026-09-28.md) 与 [结构化记录](../../features/caption/docs/provider-validation-2026-09-28.json)。没有把第三方字幕正文放入仓库。

ASSRT 公共通路可用；SubHD 搜索可用但当前部分下载需网站动态操作；字幕库当前访问策略阻止自动采集；OpenSubtitles 官方中文分类适配完成，待个人 API key／账号及配额。Shooter 哈希接口需要可信视频四段哈希，普通 115 文件树没有该信息，不能假称已执行。缺少授权时继续可用来源，质量门槛保持。

没有与实际视频逐句对时，`timing_verified` 保持 false；没有时长、FPS 或发布信息的检查项会标为未验证。真实 115 账号上传未执行；已验证官方上传协议测试与真实 Host RPC 的完整业务链，云端 I/O 使用测试替身。网站可用性、实际字幕覆盖和片源同步仍需运行时确认。

## 本地验证

本轮各套测试实际结果如下；subtests 单独列出，不混入 passed 数。

| 范围 | passed | skipped | subtests |
| --- | ---: | ---: | ---: |
| Host（含 SDK、权限、三入口真实 RPC） | 725 | 1 | 318 |
| Download | 180 | 0 | 33 |
| Search | 691 | 2 | 152 |
| Rename | 413 | 0 | 28 |
| Sync | 160 | 0 | 80 |
| Caption | 153 | 0 | 0 |
| 合计 | **2,322** | **3** | **611** |

Host 跳过的是需显式提供旧协调发行包的 release-matrix 测试；Search 两项跳过需真实 Search 配置和显式联网开关的既有 live 测试。没有将跳过记为通过。Rename 仍有 1 条既有 `logger.warn` 弃用警告。Rename 最后修订后额外重跑真实 Host RPC 三入口测试，**2 passed**，不重复加入上述总数。另验证五模块 manifest/pyproject 版本、SDK 固定依赖与两份运行依赖清单一致。

各模块分别执行了以下命令结构（部分命令额外带 `-rs` 列出跳过原因）。Caption 解析依赖安装在临时目录，未要求修改系统 Python：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
EXTRA=/tmp/telepiplex-caption-test-deps

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$EXTRA:.:sdk/src" \
  "$PY" -m pytest -q -rs -p no:cacheprovider tests

for module in download search rename sync caption; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$EXTRA:src:../../sdk/src" \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src:"$EXTRA" \
  "$PY" -m pytest -q -p no:cacheprovider tests/test_caption_business_flow.py

test ! -e .git
test ! -e .worktrees
test -d .stfolder
```

三个本地边界检查实际通过。Host、Search、Sync、Caption 的最终测试日志保存在 `/tmp/telepiplex-caption-final-{host,search,sync,caption}.log`。完整测试未代替公开来源实测，二者分别记录。

### 独立打包与启动

最终五个 Feature 均从临时复制的冻结源码构建，逐字节核对构建输入与最终源码一致。每个包通过 `verify_tpx`，并在各自干净虚拟环境中从包内 wheelhouse **离线安装**、`pip check`、空配置入口启动及真实 Unix RPC `handshake` / `health`；SDK 均为 2.2.1。启动期间阻断 DNS/网络连接，只允许本地 Unix socket，Host 替身调用为 0。

依赖准备曾从 PyPI 获取公开依赖；离线安装和启动阶段未联网。验证包为 **macOS arm64** 本地测试产物，不是 Linux 部署发行包，没有发布。生产容器新增的 `unar` 后端未在此次 Mac 环境运行；真实 RAR 实测使用 Mac `bsdtar` 后端。

| Feature | 版本 | 包大小（字节） | 包校验、离线安装、入口/RPC |
| --- | --- | ---: | --- |
| caption | 1.0.0 | 5,089,515 | 通过 |
| download | 2.2.0 | 8,428,443 | 通过 |
| search | 2.5.0 | 1,150,289 | 通过 |
| rename | 2.4.0 | 1,044,222 | 通过 |
| sync | 2.1.1 | 9,347,674 | 通过 |

最终打包脚本：`/tmp/telepiplex-caption-final-package-check.py`。各包实际以模块名为参数单独或分批执行，等价命令为：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  "$PY" /tmp/telepiplex-caption-final-package-check.py download caption rename search sync
```

最终结构化结果（含包 SHA-256、构建输入校验和与启动结果）：`/tmp/telepiplex-caption-final-package-results.json`；详细日志：`/tmp/telepiplex-caption-final-package-<module>.log`。这些临时验证产物没有复制为已发布包。


## 完整变更清单

以下由任务开始时的 SHA-256 基线与最终文件内容比较生成，未使用 Git。旧构建产物及历史归档未直接改写。

### 新增（25 个文件）

| 文件 | 目的 |
| --- | --- |
| [docs/audits/2026-09-28-caption-v1-delivery.md](../../docs/audits/2026-09-28-caption-v1-delivery.md) | 汇总本次范围、版本、测试、实际限制和完整逐文件交付清单。 |
| [features/caption/docs/provider-evidence-2026-09-28.md](../../features/caption/docs/provider-evidence-2026-09-28.md) | 记录公开来源实测、质量验证、RAR 后端和可用性边界。 |
| [features/caption/docs/provider-validation-2026-09-28.json](../../features/caption/docs/provider-validation-2026-09-28.json) | 保存英、中、日语真实字幕检索与检查的结构化证据，不包含字幕正文或密钥。 |
| [features/caption/src/telepiplex_caption/archive.py](../../features/caption/src/telepiplex_caption/archive.py) | 有界读取 ZIP/gzip/7z/RAR，校验成员路径、链接、大小、重复与完整性。 |
| [features/caption/src/telepiplex_caption/engine.py](../../features/caption/src/telepiplex_caption/engine.py) | 多来源检索、限流、下载与质量选优；记录失败原因、检查范围与覆盖限制。 |
| [features/caption/src/telepiplex_caption/matching.py](../../features/caption/src/telepiplex_caption/matching.py) | 验证作品、年份、季集、片源版本和可用 FPS，拒绝冲突候选。 |
| [features/caption/src/telepiplex_caption/metadata.py](../../features/caption/src/telepiplex_caption/metadata.py) | 适配 Search 确认身份与文件树，不猜测未知原始语言和季集。 |
| [features/caption/src/telepiplex_caption/models.py](../../features/caption/src/telepiplex_caption/models.py) | 定义媒体、候选、字幕文件、质量和匹配结果契约。 |
| [features/caption/src/telepiplex_caption/providers.py](../../features/caption/src/telepiplex_caption/providers.py) | 接入中文字幕来源、官方 API 与公共下载，约束网络访问并区分授权和服务故障。 |
| [features/caption/src/telepiplex_caption/quality.py](../../features/caption/src/telepiplex_caption/quality.py) | 解析真实字幕正文、编码、时码、中文简繁和单双语，执行用户指定优先级。 |
| [features/caption/src/telepiplex_caption/service.py](../../features/caption/src/telepiplex_caption/service.py) | 实现三入口、交互、取消恢复、目录配置与有校验的分块续传。 |
| [features/caption/src/telepiplex_caption/store.py](../../features/caption/src/telepiplex_caption/store.py) | 持久化有界任务记录，重启后标记中断任务。 |
| [features/caption/tests/test_archives.py](../../features/caption/tests/test_archives.py) | 验证压缩包格式与路径、大小、链接、损坏和 RAR 安全边界。 |
| [features/caption/tests/test_engine.py](../../features/caption/tests/test_engine.py) | 验证多来源选优、分集挑选、预算、缓存和失败状态。 |
| [features/caption/tests/test_matching.py](../../features/caption/tests/test_matching.py) | 验证错片、错集、年份和片源版本拒绝规则。 |
| [features/caption/tests/test_metadata.py](../../features/caption/tests/test_metadata.py) | 验证身份、季集和原始语言适配，防止错误推断。 |
| [features/caption/tests/test_providers.py](../../features/caption/tests/test_providers.py) | 验证各来源协议、公共下载、授权状态与网络边界。 |
| [features/caption/tests/test_quality.py](../../features/caption/tests/test_quality.py) | 验证正文质量、编码、简繁、同步双语证据和用户指定优先级。 |
| [features/caption/tests/test_service.py](../../features/caption/tests/test_service.py) | 验证三入口、配置、取消、恢复、分块续传与目标变更。 |
| [features/download/src/telepiplex_download/subtitle_upload.py](../../features/download/src/telepiplex_download/subtitle_upload.py) | 实现持久化分块接收、SHA-1 校验、115 官方上传及同名冲突保护。 |
| [features/download/tests/test_subtitle_upload.py](../../features/download/tests/test_subtitle_upload.py) | 验证 115 官方协议、持久化分块、重试、冲突保护与写入屏障。 |
| [features/rename/src/telepiplex_rename/caption_bridge.py](../../features/rename/src/telepiplex_rename/caption_bridge.py) | 提供共享扫描、统一字幕命名与字幕落盘 capability。 |
| [features/rename/tests/test_caption_bridge.py](../../features/rename/tests/test_caption_bridge.py) | 验证共享扫描、chi/cht 命名、下载前接续和来源归因。 |
| [tests/test_caption_business_flow.py](../../tests/test_caption_business_flow.py) | 通过真实 Host Unix RPC 验证三入口、分块传输、统一命名和重复执行，云端使用测试替身。 |
| [tests/test_caption_host_contract.py](../../tests/test_caption_host_contract.py) | 验证安装依赖无环、Caption 消息段、默认配置和字幕/授权脱敏。 |

### 修改（66 个文件）

| 文件 | 目的 |
| --- | --- |
| [Dockerfile](../../Dockerfile) | 增加 unar，支持生产容器读取真实 RAR 字幕包。 |
| [README.md](../../README.md) | 更新三入口能力、模块版本与升级顺序。 |
| [README_EN.md](../../README_EN.md) | 同步英文模块说明、版本及升级顺序。 |
| [app/115bot.py](../../app/115bot.py) | Host 源码版本提升至 3.9.0。 |
| [app/handlers/interaction_handler.py](../../app/handlers/interaction_handler.py) | Telegram 入站诊断隐藏自由输入，避免裸密钥进入日志。 |
| [app/runtime/operation_segments.py](../../app/runtime/operation_segments.py) | 加入 Caption 任务消息段类型。 |
| [app/runtime/plugin_contract.py](../../app/runtime/plugin_contract.py) | Host API 提升至 1.9。 |
| [app/runtime/plugin_manifest.py](../../app/runtime/plugin_manifest.py) | 支持可选 capability 依赖声明及冲突校验。 |
| [app/runtime/plugin_rpc.py](../../app/runtime/plugin_rpc.py) | 日志快照脱敏自由消息正文，保留实际 RPC 载荷。 |
| [app/runtime/runtime_broker.py](../../app/runtime/runtime_broker.py) | 授权可选 capability 调用，支持 Caption 任务消息段。 |
| [app/utils/log_sanitizer.py](../../app/utils/log_sanitizer.py) | 对字幕分块正文与规范化正文脱敏。 |
| [examples/echo_feature/pyproject.toml](../../examples/echo_feature/pyproject.toml) | 示例固定依赖同步到 SDK 2.2.1。 |
| [features/caption/README.md](../../features/caption/README.md) | 说明当前模块版本、Caption 相关能力或 SDK 联动及升级要求。 |
| [features/caption/config.default.yaml](../../features/caption/config.default.yaml) | 提供自动补字幕、来源、超时、下载预算和输出目录默认值。 |
| [features/caption/config.schema.json](../../features/caption/config.schema.json) | 校验 Caption 配置并兼容原有空配置。 |
| [features/caption/manifest.yaml](../../features/caption/manifest.yaml) | 同步模块版本与 Host/capability 契约；Sync 仅随 SDK 升级。 |
| [features/caption/pyproject.toml](../../features/caption/pyproject.toml) | 同步源码包版本、SDK 2.2.1 及所需依赖。 |
| [features/caption/requirements-feature.txt](../../features/caption/requirements-feature.txt) | 打包 OpenCC、编码检测与压缩包解析依赖。 |
| [features/caption/src/telepiplex_caption/runtime.py](../../features/caption/src/telepiplex_caption/runtime.py) | 用完整 Feature 服务替换占位实现，注册 capability、命令、回调与任务接口。 |
| [features/download/README.md](../../features/download/README.md) | 说明当前模块版本、Caption 相关能力或 SDK 联动及升级要求。 |
| [features/download/manifest.yaml](../../features/download/manifest.yaml) | 同步模块版本与 Host/capability 契约；Sync 仅随 SDK 升级。 |
| [features/download/pyproject.toml](../../features/download/pyproject.toml) | 同步源码包版本、SDK 2.2.1 及所需依赖。 |
| [features/download/requirements-feature.txt](../../features/download/requirements-feature.txt) | 打包 OSS 官方客户端依赖。 |
| [features/download/src/telepiplex_download/client.py](../../features/download/src/telepiplex_download/client.py) | 在既有 115 客户端暴露字幕上传能力。 |
| [features/download/src/telepiplex_download/service.py](../../features/download/src/telepiplex_download/service.py) | 注册分块上传与待完成写入屏障，取消时仍跟踪已接受写入。 |
| [features/download/tests/test_feature_runtime.py](../../features/download/tests/test_feature_runtime.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/rename/README.md](../../features/rename/README.md) | 说明当前模块版本、Caption 相关能力或 SDK 联动及升级要求。 |
| [features/rename/config.default.yaml](../../features/rename/config.default.yaml) | 默认开启可选 Caption 接续并设置总超时。 |
| [features/rename/config.schema.json](../../features/rename/config.schema.json) | 校验 Caption 接续开关与超时。 |
| [features/rename/manifest.yaml](../../features/rename/manifest.yaml) | 同步模块版本与 Host/capability 契约；Sync 仅随 SDK 升级。 |
| [features/rename/pyproject.toml](../../features/rename/pyproject.toml) | 同步源码包版本、SDK 2.2.1 及所需依赖。 |
| [features/rename/src/telepiplex_rename/ai.py](../../features/rename/src/telepiplex_rename/ai.py) | 在现有整理链路中识别和保留 SSA 等外挂字幕格式。 |
| [features/rename/src/telepiplex_rename/content_probe.py](../../features/rename/src/telepiplex_rename/content_probe.py) | 在现有整理链路中识别和保留 SSA 等外挂字幕格式。 |
| [features/rename/src/telepiplex_rename/file_facts.py](../../features/rename/src/telepiplex_rename/file_facts.py) | 在现有整理链路中识别和保留 SSA 等外挂字幕格式。 |
| [features/rename/src/telepiplex_rename/inventory.py](../../features/rename/src/telepiplex_rename/inventory.py) | 在现有整理链路中识别和保留 SSA 等外挂字幕格式。 |
| [features/rename/src/telepiplex_rename/models.py](../../features/rename/src/telepiplex_rename/models.py) | 在现有整理链路中识别和保留 SSA 等外挂字幕格式。 |
| [features/rename/src/telepiplex_rename/processor.py](../../features/rename/src/telepiplex_rename/processor.py) | 在现有整理链路中识别和保留 SSA 等外挂字幕格式。 |
| [features/rename/src/telepiplex_rename/runtime.py](../../features/rename/src/telepiplex_rename/runtime.py) | 注册 media.rename capability。 |
| [features/rename/src/telepiplex_rename/service.py](../../features/rename/src/telepiplex_rename/service.py) | 整理前调用可选 Caption，等待写入并刷新文件树，汇总结果与来源归因。 |
| [features/rename/src/telepiplex_rename/subtitles.py](../../features/rename/src/telepiplex_rename/subtitles.py) | 保留 chi/cht 简繁后缀，避免一律重写为 chi。 |
| [features/rename/tests/test_feature_processor.py](../../features/rename/tests/test_feature_processor.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/rename/tests/test_inventory.py](../../features/rename/tests/test_inventory.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/rename/tests/test_subtitle_preservation.py](../../features/rename/tests/test_subtitle_preservation.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/rename/tests/test_subtitles.py](../../features/rename/tests/test_subtitles.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/search/README.md](../../features/search/README.md) | 说明当前模块版本、Caption 相关能力或 SDK 联动及升级要求。 |
| [features/search/manifest.yaml](../../features/search/manifest.yaml) | 同步模块版本与 Host/capability 契约；Sync 仅随 SDK 升级。 |
| [features/search/pyproject.toml](../../features/search/pyproject.toml) | 同步源码包版本、SDK 2.2.1 及所需依赖。 |
| [features/search/src/telepiplex_search/service.py](../../features/search/src/telepiplex_search/service.py) | 向调用方提供原始语言等字幕上下文，保持 media_metadata v2 不变。 |
| [features/search/tests/test_config_schema_contract.py](../../features/search/tests/test_config_schema_contract.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/search/tests/test_feature_service.py](../../features/search/tests/test_feature_service.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [features/sync/README.md](../../features/sync/README.md) | 说明当前模块版本、Caption 相关能力或 SDK 联动及升级要求。 |
| [features/sync/manifest.yaml](../../features/sync/manifest.yaml) | 同步模块版本与 Host/capability 契约；Sync 仅随 SDK 升级。 |
| [features/sync/pyproject.toml](../../features/sync/pyproject.toml) | 同步源码包版本、SDK 2.2.1 及所需依赖。 |
| [features/sync/tests/test_feature_runtime.py](../../features/sync/tests/test_feature_runtime.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [sdk/pyproject.toml](../../sdk/pyproject.toml) | SDK 版本提升至 2.2.1。 |
| [sdk/src/telepiplex_plugin_sdk/diagnostics.py](../../sdk/src/telepiplex_plugin_sdk/diagnostics.py) | 统一字幕正文与自由输入诊断脱敏。 |
| [sdk/src/telepiplex_plugin_sdk/logging_utils.py](../../sdk/src/telepiplex_plugin_sdk/logging_utils.py) | Feature dispatch 日志使用脱敏后的输入快照。 |
| [tests/test_bot_runtime_startup.py](../../tests/test_bot_runtime_startup.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_deployment_contract.py](../../tests/test_deployment_contract.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_feature_builder.py](../../tests/test_feature_builder.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_interaction_handler.py](../../tests/test_interaction_handler.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_plugin_manager.py](../../tests/test_plugin_manager.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_plugin_manifest.py](../../tests/test_plugin_manifest.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_plugin_rpc.py](../../tests/test_plugin_rpc.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_runtime_broker.py](../../tests/test_runtime_broker.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |
| [tests/test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 更新或补充模块契约、版本、字幕处理或诊断脱敏的回归断言。 |

### 重命名

- `features/caption/tests/test_placeholder_runtime.py` → `features/caption/tests/test_runtime.py`：占位模块测试改为正式入口注册及启动测试。

### 删除

无额外删除。

## 交付边界

等待 Syncthing 显示 **Up to Date / 最新**，将 Mac 源码同步至 Unraid `/mnt/user/archives/life hacker/telepiplex`。后续检查、版本标签及发布由用户在 Unraid 执行。没有改动 Unraid User Scripts 发布脚本。
