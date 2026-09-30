# Caption 1.1.0 免费来源扩展交付审计

状态：本轮本地开发与验收完成，待用户 Syncthing 交付及发布。日期：2026-09-28。

## 范围与版本

Caption **1.1.0**，Search **2.6.0**。配套继续使用 Host **3.9.0**（API 1.9）、Download **2.2.0**、Rename **2.4.0**、Sync **2.1.1**、SDK **2.2.1**。版本均来自当前 manifest/pyproject；没有执行发布、Git 或连接本项目的 GitHub 仓库。

三条外挂字幕链路保留：下载后在 Rename 整理前补字幕；复用 Rename 扫描媒体库；Search 定位元数据后无视频查询。文件命名交给 Rename，仍只用 `chi` / `cht`，既有 Plex 接续保持。只处理外挂字幕，不检查或提取内嵌字幕，不做转写/翻译。

## 来源不是简单按数量相加

现有 ASSRT、Shooter、OpenSubtitles 保留，扩展成 **18 个适配器**。详细逐源说明见[模块说明](../../features/caption/README.md)及[目录配置](../../features/caption/docs/catalog-usage.md)。

- SubHD 已通过正常匿名 session 搜索→准备下载→文件→正文检查；《星际穿越》简英 ASS 2013 cue。字幕库详情→公开签名下载→整季 RAR→指定 E01 简英 ASS 398 cue 已通过。字幕库搜索当前多个正常入口持续 404，因此保留明确故障与 `--source` 详情入口。
- 迅雷、R3SUB、Addic7ed、Subf2m 有真实中文字幕正文证据；Subf2m 搜索当前 HTTP 500，详情下载可用。R3 使用公开预览/导出，不宣称有验证码的整包下载可用。
- SubDL、SubSource 免费个人 Key 接口已实现且契约测试无跳过；尚未取得个人 Key 实连 API。真实公开文件已检查（包括 UTF-16 简英 SRT、简英 ASS、简中单语 SRT）。OpenSubtitles 沿用个人授权/免费额度路径，没有购买、调用付费翻译或声称授权链已实测。
- LWLTV 可搜索，当前下载遇 Turnstile；YYSub 可搜索，当前下载要求登录。均明确报告授权/人工操作状态，不当作“没有字幕”。用户自行取得的文件可放入本地归档入口。
- 喵萌、MingY、北宇治和拨雪寻春读取官方成品目录；北宇治当前只覆盖已核验的 `subs-shikanoko`。真实成品包经当前代码检查：MingY 普通版 6 集、北宇治 4 集、喵萌 8 集被选中；拨雪寻春样本是中日双语，按用户单语规则拒绝。未通过成员仍保留拒绝记录，不能宣称整季完整。
- 本地归档采用用户显式目录、可选 TSV 和明确季集映射；默认不扫描目录。人人影视历史库只提供导入能力，本次没有下载或导入约 18 GB 全库。没有把字幕组源工程文件、RSS 页面或在线播放视频当作可用外挂字幕。

## 真实媒体库样本

只读使用 Flip the server 项目 2026-09-17 历史登记，抽取 16 个真实片名，覆盖真人电影/剧、动画电影/剧和英语、中文/粤语、日语、韩语、意大利语。样本[登记证据](../../features/caption/docs/library-samples-2026-09-28.json)不包含服务器路径；这些是明确输入的测试身份，不是本轮实时 Search 解析或当前媒体盘的复查。

首轮顺序低速联网 **15/16 命中**。双城之战、奇巧计程车、瑞克和莫蒂、葬送的芙莉莲、你的名字、千与千寻、寻梦环游记、想见你、摩登家庭、绝命毒师、功夫、天堂电影院、寄生虫、我不是药神、星际穿越取得通过当前检查的字幕。

《请回答1988》首轮未命中，查出数字片名被误作发行年及单集查询漏整季包，已补修复与回归。后续原作标题检索真实页面提供了候选，但再次搜索/详情烟测遇 HTTP 526；保留失败证据，没有声称取得正文或将首轮改写成 16/16。没有为了结果继续轰炸来源。

所有选中字幕仍为 `timing_verified=false`。正文结构、语言、时间轴、版本和参考时长检查不能替代逐句音画同步或译文准确性审校。真实正文只保留 `/tmp`，不放入工作区。完整统计见[验证 JSON](../../features/caption/docs/provider-integration-validation-2026-09-28.json)。

## 质量、容量与边界

- 按原始语言执行用户指定的简繁/单双语/ASS-SRT优先级。中日双语、明显分段、制作残留、错误季集和不同剪辑版本继续拒绝。Search 参考时长与视频实测时长分开；剧集平均时长不能填给每集。
- 对 ASS 只兼容极少量渲染器不显示的 OP/ED 单字 `fx` 事件：有效时间语法、end<=start、最多64且不超过Dialogue的2%。这些事件不参与统计，原文保留并附warning。普通对白坏时码仍拒绝。依据[libass渲染条件](https://github.com/libass/libass/blob/master/libass/ass_render.c)与[时长解析](https://github.com/libass/libass/blob/master/libass/ass.c)，并用真实包及正反例验证。
- 7z 使用独立解释器、30秒子进程时限、字典/成员/总量约束；仅允许有真实ASS结构的小成员集通过高压缩比特例。路径穿越、链接、损坏包和压缩炸弹继续拒绝。
- 网络并发3、内容检查2，缓存有量/时限；相同请求共享，免费配额失败保留状态并退避。超时后的实际工作继续占用槽位，防止重试叠加线程。
- 单媒体默认180秒；搜索和内容检查分配预算，逐个保存已检查成员，超时返回已验收结果并报告未完成范围。自动流程240秒涵盖元数据恢复，并预留写入时间；用户主动取消继续传播。
- HTTP响应和错误体按块检查协作式deadline，重定向不排空无界响应体。DNS、响应头和正在执行的一次socket读取不能强制中断；不宣称Python线程可被硬终止。凭据仅在已知来源域名发送，CDN和跨来源重定向不带Key/Cookie。

## 本地测试、压测与打包

实际执行结果（子测试另计，重复补测不累加）：

| 范围 | 通过 | 跳过 | 子测试通过 |
| --- | ---: | ---: | ---: |
| host | 725 | 1 | 318 |
| download | 180 | 0 | 33 |
| search | 694 | 2 | 152 |
| rename | 413 | 0 | 28 |
| sync | 160 | 0 | 80 |
| caption | 376 | 0 | 0 |

合计 **2548 passed、3 skipped、611 subtests passed**。最终源码冻结后补跑跨模块业务、Host合同、版本/品牌合同：**19 passed、42 subtests passed**。Caption 本身没有跳过。

三个既有跳过分别为 Host 未提供历史发布矩阵包、Search 两个需主动启用及配置的联网检查；它们不当作通过。另有 Rename 既有 `logger.warn` 弃用警告。首轮 Host 因缺 OpenCC 临时依赖及版本文档尚未对齐而失败，补入受控依赖/对齐版本后全量及最终针对性回归均通过，没有为通过而修改跨模块字幕 fixture。

实际命令结构如下（各套独立或按展示循环执行，日志保存在 `/tmp/caption-free-final-<module>.log`）：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
EXTRA=/tmp/telepiplex-caption-test-deps
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$EXTRA:.:sdk/src" \
  "$PY" -m pytest -q -p no:cacheprovider tests
(cd features/search && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider tests)
for module in download rename sync; do
  (cd "features/$module" && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider tests)
done
(cd features/caption && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$EXTRA:src:../../sdk/src" \
  "$PY" -m pytest -q -p no:cacheprovider tests)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$EXTRA:.:sdk/src" "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_caption_business_flow.py tests/test_caption_host_contract.py \
  tests/test_technical_identity_migration.py tests/test_product_name_casing.py
```

跨模块业务通过真实 Host Unix RPC 验证三条链路、chi/cht 命名、取消/重复执行及传输；115 存储使用替身，不是云端媒体写入测试。

离线压力结果：**1,000 请求、32 路调用并发、全部成功**，耗时 **84.138 秒**，约 **11.89 请求/秒**，P50 **2.637 秒**、P95 **2.869 秒**。Python 峰值分配 **22,286,019 字节**，进程峰值 RSS **87,982,080 字节**；正常来源只有16次搜索/16次下载，实际并发峰值3。注入限额故障仅触发3次来源调用，其余保持可见退避状态；结束时工作线程、共享任务及活跃工作均为0。正文是明确的离线合成 fixture，不是用外部站点做高压，也不是生产SLA。

```bash
cd /Users/young/Documents/telepiplex/features/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:src:../../sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  tools/pressure_test.py --requests 1000 --concurrency 32 --output /tmp/caption-free-pressure-final.json
```

真实来源验证先执行 `tools/live_provider_check.py --providers assrt,subhd,xunlei,r3sub,addic7ed --limit 16 --candidates 15 --output /tmp/caption-free-library-live.json`。最终传输代码冻结后另以同工具执行 `--providers xunlei --titles 星际穿越 --limit 1 --candidates 2 --output /tmp/caption-free-final-live-smoke.json`，真实搜索/下载/正文检查通过，选出简英 ASS。其余公开来源与真实成品包验证详见结构化证据，正文不入工作区。

本地 `.tpx` 构建与安装：

| 模块 | 版本 | 字节 | 结果 |
| --- | --- | ---: | --- |
| search | 2.6.0 | 1,151,072 | 构建、包验证、干净venv离线安装、pip check、空配置启动、Unix握手与health均通过 |
| caption | 1.1.0 | 5,122,750 | 构建、包验证、干净venv离线安装、pip check、空配置启动、Unix握手与health均通过 |

实际分别执行 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" /tmp/caption-free-final-package-check.py search` 与 `... caption`。该本地脚本调用项目 `tools/build_feature.py`、`verify_tpx`，使用既有受控 wheelhouse 禁止网络构建，安装测试启动期间只允许 Unix socket 并禁止 DNS/远程连接。最终包内源码与工作区 `.py` 逐文件一致。包 SHA-256、具体平台和启动结果记录在验证 JSON。

这证明 Mac Python 3.12.14 本地构建/安装及协议启动可用；正式 Linux/Unraid 发布产物仍由用户发布后的现有流水线生成。本地测试包只在 `/tmp`，没有放入源码目录或替代正式发布包。

以下边界检查也实际通过：

```bash
test ! -e .git
test ! -e .worktrees
test -d .stfolder
```


## 逐文件交付清单

下列为本轮 46 个新增/修改的源码、测试与文档文件；无删除或重命名。测试生成的 egg-info 是构建元数据，没有当作产品源码改动或已发布包交付。

| 类型 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | [README.md](../../README.md) | 同步当前版本、免费字幕来源扩展和升级组合。 |
| 修改 | [README_EN.md](../../README_EN.md) | 同步英文版本表与本轮能力说明。 |
| 新增 | [docs/superpowers/plans/2026-09-28-caption-free-providers.md](../../docs/superpowers/plans/2026-09-28-caption-free-providers.md) | 保留验收目标、工作边界和完成情况。 |
| 修改 | [features/caption/README.md](../../features/caption/README.md) | 说明18适配器的真实能力、授权限制、详情入口及质量边界。 |
| 新增 | [features/caption/CHANGELOG.md](../../features/caption/CHANGELOG.md) | 记录Caption 1.1.0变化和配套Search版本。 |
| 修改 | [features/caption/manifest.yaml](../../features/caption/manifest.yaml) | Caption版本迭代至1.1.0，保持Host/API合同。 |
| 修改 | [features/caption/pyproject.toml](../../features/caption/pyproject.toml) | Caption源码包版本迭代至1.1.0。 |
| 修改 | [features/caption/config.default.yaml](../../features/caption/config.default.yaml) | 加入免费来源、可选凭据、目录映射与检索预算默认值。 |
| 修改 | [features/caption/config.schema.json](../../features/caption/config.schema.json) | 校验来源配置、凭据长度、映射、索引和时间预算边界。 |
| 修改 | [features/caption/src/telepiplex_caption/models.py](../../features/caption/src/telepiplex_caption/models.py) | 在媒体查询末尾增加参考时长，保持旧位置参数兼容。 |
| 修改 | [features/caption/src/telepiplex_caption/metadata.py](../../features/caption/src/telepiplex_caption/metadata.py) | 区分视频实测与元数据参考时长，单集只接受明确映射。 |
| 修改 | [features/caption/src/telepiplex_caption/providers.py](../../features/caption/src/telepiplex_caption/providers.py) | 完善中文来源普通请求流程、详情解析、传输时限、凭据隔离与18源注册。 |
| 新增 | [features/caption/src/telepiplex_caption/chinese_providers.py](../../features/caption/src/telepiplex_caption/chinese_providers.py) | 新增LWLTV/YYSub公开搜索和下载权限检查。 |
| 新增 | [features/caption/src/telepiplex_caption/extra_providers.py](../../features/caption/src/telepiplex_caption/extra_providers.py) | 新增迅雷、SubDL、SubSource、Subf2m、R3SUB、Addic7ed。 |
| 新增 | [features/caption/src/telepiplex_caption/catalog_providers.py](../../features/caption/src/telepiplex_caption/catalog_providers.py) | 新增四个官方成品目录和受限本地归档索引/季集映射。 |
| 修改 | [features/caption/src/telepiplex_caption/engine.py](../../features/caption/src/telepiplex_caption/engine.py) | 网络与CPU容量、跨超时工作共享、缓存退避、总预算和逐成员保留已验收结果。 |
| 修改 | [features/caption/src/telepiplex_caption/service.py](../../features/caption/src/telepiplex_caption/service.py) | 免费Key设置、详情链接参数、自动剩余预算和可操作的来源状态。 |
| 修改 | [features/caption/src/telepiplex_caption/matching.py](../../features/caption/src/telepiplex_caption/matching.py) | 加固制作素材/分段/版本与动漫映射核对，修复数字片名年份冲突。 |
| 修改 | [features/caption/src/telepiplex_caption/quality.py](../../features/caption/src/telepiplex_caption/quality.py) | 改进中日双语、参考时长及ASS内容质量判定。 |
| 修改 | [features/caption/src/telepiplex_caption/archive.py](../../features/caption/src/telepiplex_caption/archive.py) | 隔离7z解包并限定时限、字典、总量和高压缩成品结构。 |
| 修改 | [features/caption/tests/test_providers.py](../../features/caption/tests/test_providers.py) | 验证请求协议、凭据隔离、详情URL、来源注册及数字片名。 |
| 新增 | [features/caption/tests/test_chinese_providers.py](../../features/caption/tests/test_chinese_providers.py) | 验证两个新增中文网页源的搜索、授权和挑战状态。 |
| 新增 | [features/caption/tests/test_extra_providers.py](../../features/caption/tests/test_extra_providers.py) | 验证六源协议、真实格式契约、免费Key、机翻排除和故障。 |
| 新增 | [features/caption/tests/test_catalog_providers.py](../../features/caption/tests/test_catalog_providers.py) | 验证成品索引、缓存、映射及本地目录安全/边界。 |
| 修改 | [features/caption/tests/test_engine.py](../../features/caption/tests/test_engine.py) | 验证18源排队、取消/超时单航班、并发上限、时间预算及整季部分成果。 |
| 修改 | [features/caption/tests/test_service.py](../../features/caption/tests/test_service.py) | 验证免费授权、详情查询、配置清除和自动全程预算。 |
| 修改 | [features/caption/tests/test_metadata.py](../../features/caption/tests/test_metadata.py) | 验证实测/参考时长分离与精确季集时长。 |
| 修改 | [features/caption/tests/test_matching.py](../../features/caption/tests/test_matching.py) | 验证数字片名、特殊集/错误映射、片源版本与分段拒绝。 |
| 修改 | [features/caption/tests/test_quality.py](../../features/caption/tests/test_quality.py) | 验证中日双语、分段、制作残留和ASS特殊事件边界。 |
| 修改 | [features/caption/tests/test_archives.py](../../features/caption/tests/test_archives.py) | 验证高压缩成品及7z安全边界。 |
| 新增 | [features/caption/tools/live_provider_check.py](../../features/caption/tools/live_provider_check.py) | 可重复执行低速真实媒体库标题验证，只保存统计、不写媒体。 |
| 新增 | [features/caption/tools/pressure_test.py](../../features/caption/tools/pressure_test.py) | 用真实引擎、离线正文与故障注入执行并发压测。 |
| 新增 | [features/caption/docs/library-samples-2026-09-28.json](../../features/caption/docs/library-samples-2026-09-28.json) | 保存16个历史媒体库标题与登记证据，不包含服务器路径。 |
| 新增 | [features/caption/docs/catalog-usage.md](../../features/caption/docs/catalog-usage.md) | 说明官方目录范围、动漫映射、容器只读路径与本地TSV。 |
| 修改 | [features/search/manifest.yaml](../../features/search/manifest.yaml) | Search版本迭代至2.6.0。 |
| 修改 | [features/search/pyproject.toml](../../features/search/pyproject.toml) | Search源码包版本迭代至2.6.0。 |
| 修改 | [features/search/README.md](../../features/search/README.md) | 说明参考时长上下文并同步构建示例/版本。 |
| 新增 | [features/search/src/telepiplex_search/subtitle_context.py](../../features/search/src/telepiplex_search/subtitle_context.py) | 构造不影响v2命名合同的字幕专用上下文。 |
| 修改 | [features/search/src/telepiplex_search/media_metadata_v1.py](../../features/search/src/telepiplex_search/media_metadata_v1.py) | 保留已明确对应单集的runtime_minutes。 |
| 修改 | [features/search/src/telepiplex_search/service.py](../../features/search/src/telepiplex_search/service.py) | 元数据查询结果调用字幕上下文构造器。 |
| 新增 | [features/search/tests/test_subtitle_context.py](../../features/search/tests/test_subtitle_context.py) | 验证电影时长和精确单集时长，拒用剧集平均时长。 |
| 修改 | [features/search/tests/test_feature_service.py](../../features/search/tests/test_feature_service.py) | 对齐Search 2.6.0版本与构建合同断言。 |
| 修改 | [features/search/tests/test_config_schema_contract.py](../../features/search/tests/test_config_schema_contract.py) | 对齐Search版本与配置合同断言。 |
| 修改 | [tests/test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 对齐Caption/Search独立版本断言。 |
| 新增 | [features/caption/docs/provider-integration-validation-2026-09-28.json](../../features/caption/docs/provider-integration-validation-2026-09-28.json) | 保存本轮真实来源、媒体库命中、质量与压测结构化证据（无字幕正文）。 |
| 新增 | [docs/audits/2026-09-28-caption-free-providers-delivery.md](../../docs/audits/2026-09-28-caption-free-providers-delivery.md) | 记录完整验收结果、限制、逐文件变化和Syncthing交付。 |

## 交付边界

本次只在 Mac 修改和验证。等待 Syncthing 显示 **Up to Date / 最新**，同步到 Unraid `/mnt/user/archives/life hacker/telepiplex` 后，由用户检查并自行发布。没有代执行 Git、PR、tag 或发布，也没有读写 Unraid 媒体。
