# Caption 来源交集评估与简化交付

日期：2026-10-05。Caption **1.2.0 → 1.3.0**；Host 3.9.2、其他 Feature、SDK 2.2.1、API 1.9 保持。

## 结论与证据

18 个适配器代表检索能力，不代表 18 份独立库存。此次保留适配器，将常规检索收敛为按作品选择的 4 个来源，再按授权和输入加入补充来源；不按片名或文件名删除候选，不降低正文质量检查。

| 来源组 | 交集与独立价值 | 本轮处理 |
| --- | --- | --- |
| ASSRT、SubHD、迅雷 | 同一作品可出现多个站点版本，但未完成跨库正文指纹比对，无法给出重复率。历史实际赢家集中在这三者。 | 常规核心，仍由正文决定简繁、单双语、格式与排名。 |
| SubDL、SubSource、Subf2m | SubDL 官方说明保存 Subscene 历史库；SubSource 官方说明起初保存其波斯语／英语库，再扩展其他语言。SubDL／Subf2m 有相同发布名与上传者的条目，但尚未比较两边正文。 | 不当作三份独立增量库存；Subf2m 移到详情入口。有有效 Key 的 SubDL、SubSource 仍同时选优。 |
| ASSRT 与 Shooter Hash API | 品牌存在历史关联，但协议和输入不同：ASSRT 支持片名；Shooter 需要四段视频哈希。 | Shooter 仅有哈希时参与，不计入无视频 query 的常规来源。 |
| R3SUB、Addic7ed | 电影单语／繁中版本和剧集版本有补充价值；历史样本不能证明全库中文覆盖或稳定下载。 | R3SUB 用于电影，Addic7ed 用于剧集。 |
| 字幕库、Subf2m | 搜索入口故障与库存重复是两件事。2026-10-05 各一次普通公开请求仍为 404／500。 | 精简方案保留指定详情链接；全部方案仍可尝试关键词搜索。 |
| LWLTV、YYSub | 站点检索结果不等于自动可下载；既有样本分别遇到浏览器验证、登录限制。 | LWLTV 移到全部方案；YYSub 精简方案需配置登录 Cookie。Cookie 不视作验证已通过。 |
| Nekomoe、MingY、Kitauji、Haruhana | 不同制作组的成品渠道，不能因“都做动漫”认定库存重复。Kitauji 当前只接入《鹿乃子》，Haruhana 的一个双语样本不证明全库都不合规则。 | 动画与未知类别保留四组；明确真人类别跳过。Kitauji 保留自身的作品守卫。 |
| 本地归档 | 可能包含网站或字幕组的历史文件；价值取决于用户已有资料。 | 只有显式 roots 才运行，默认不扫描目录。 |

主要公开依据：[SubDL 历史库说明](https://subdl.com/subscene-alternative)、[SubSource About](https://subsource.net/about)。相同候选元数据示例：[SubDL 条目](https://subdl.com/subtitle/sd213724/interstellar/chinese-bg-code)、[Subf2m 条目](https://subf2m.co/subtitles/interstellar/chinese-bg-code/1081833)，发布者均为 klseklse、发布名相同。候选元数据相同不等于正文完全相同，也不证明中文全库的交集比例。

2026-09-28 的 [16 部作品实测](../../features/caption/docs/provider-integration-validation-2026-09-28.json)命中 15 部，赢家为 ASSRT 6、迅雷 6、SubHD 3。该轮只比较五个来源；R3SUB 在 6 次查询出现候选但未赢，Addic7ed 16 次均无候选。这支持核心组合，却不能证明其他来源没有独有内容。旧证据偏向已选字幕，不能用于估算全库重复率。既有《沙丘》样本中 SubDL 提供双语 ASS、SubSource 提供 iTunes 单语 SRT，因此不做未经完整质量兜底的二选一。

## 实际默认行为

| 条件 | `balanced` 精简常用 | `all` 全部来源 |
| --- | --- | --- |
| 已确认真人电影、无额外配置 | ASSRT、SubHD、迅雷、R3SUB | 所有已启用且有必需输入的适配器 |
| 已确认真人剧集、无额外配置 | ASSRT、SubHD、迅雷、Addic7ed | 同上 |
| 动画或未知类别 | 对应常规组合 + 四个成品目录 | 同上 |
| Key、Shooter 哈希、归档路径 | 满足对应条件才加入 | 同样先检查必需输入 |
| YYSub | 配置 Cookie 后尝试 | 可查询，下载授权另行检查 |
| 字幕库、Subf2m、LWLTV | 不参加常规关键词搜索 | 可尝试，站点故障／挑战如实报告 |
| 指定详情 URL | 只查支持该链接的来源 | 同样只查对应来源 |
| `enabled: false` | 始终禁用，包括详情入口 | 同样禁用 |

未配置授权或不适用的来源在创建线程和申请网络槽位之前跳过，结果仍保存来源名、状态与原因；真实检索失败继续报告不可用。精简方案不以第一个命中提前停止，也不改变匹配、解包、正文语言判断与质量排名。

没有全库等价证据，因此保留“全部来源”和详情入口用于补充查找。精简方案可能少查到只能通过备用站点关键词找到的条目；无结果时任务面板提示切换方案或提供详情链接。未进行全库覆盖率或性能基准测试，不能将调度数量下降等同于同比耗时下降。

## 配置与兼容

默认增加 `source_profile: balanced`，Schema 继续为 1，字段可选；旧配置缺失时也采用 balanced。Host 现有补默认流程保留既有 `enabled`、Key、Cookie、映射和归档路径，不重置用户配置。`enabled: true` 表示允许使用，实际是否检索由方案和输入决定；需要广泛检索时选择 all。

`/caption_config` →“字幕来源”→“精简常用／全部来源”，沿用现有配置补丁和重载流程。其余来源设置仍在 `/config/plugins/caption/config.yaml`。已有 Host 3.9.2、Download 2.2.0、Search 2.6.1、Rename 2.4.0 时只需更新 Caption 1.3.0。

媒体类别来自已验证的 `media_metadata_v2.placement.category_kind`，不从中文片名或原始语言猜测。MediaQuery 仅追加有默认值的字段，不改 Search／SDK 合同。详情检索仍遵守 `enabled: false` 与来源 `rate_limited` 冷却；排队后再次检查额度，避免重复详情请求耗费网络槽位。

## 当前实测与限制

全部记录见 [source-profile-validation-2026-10-05.json](../../features/caption/docs/source-profile-validation-2026-10-05.json)。

- 12:28 上海时间各一次普通公开搜索：字幕库 HTTP 404，Subf2m HTTP 500；没有凭据或挑战绕过。这仅说明本次入口状态，不表示永久故障。
- 当前低速检索《星际穿越》：实际调度 ASSRT、SubHD、迅雷、R3SUB，前三者各返回 3 个候选，最终检查 7 个候选，21.531 秒取得 ASSRT 简英 ASS，2,387 个有效 cue，SHA-256 `7b4d75902816e879b2ec06872d05a7fd67646cc4f686be8cef83716097a9ee6b`。
- 本次 R3SUB 返回 `unsafe_url`（该次域名解析到非公网网络），保留限制状态并继续其他来源。没有放宽 URL 校验，也不声称 R3SUB 当前正常下载；不能把本地网络观察推断为永久站点故障。
- 真实查询用显式已知作品身份，未实连 Search 身份解析；未提供个人 Key 或 Cookie，未读取视频或写入媒体库。无视频时仍报告 `video_timing_unverified`、`timing_verified=false`；没有验证逐句同步或翻译准确性。
- 当前样本预算为 8 个总候选、每来源 3 个，不代表全部版本已遍历；模块默认预算保持 30／15。四动漫源及授权 API 未做本轮全量联网复测。

## 文件清单

新增 4 个、修改 17 个文件，无删除或重命名；历史验证与交付记录保持原文。

| 状态 | 文件 | 目的 |
| --- | --- | --- |
| 新增 | [source_policy.py](../../features/caption/src/telepiplex_caption/source_policy.py) | 来源方案、适用性与必需配置检查。 |
| 新增 | [test_source_policy.py](../../features/caption/tests/test_source_policy.py) | 精简／全部、授权、类别、显式禁用等策略合同。 |
| 新增 | [source-profile-validation-2026-10-05.json](../../features/caption/docs/source-profile-validation-2026-10-05.json) | 官网证据、搜索故障与当前低速真实检索记录。 |
| 新增 | 本文 | 评估、限制、文件及验证交付清单。 |
| 修改 | [engine.py](../../features/caption/src/telepiplex_caption/engine.py) | 调度前筛选、指定源隔离、额度冷却与可审计检索结果。 |
| 修改 | [models.py](../../features/caption/src/telepiplex_caption/models.py) | MediaQuery 追加可选 category_kind。 |
| 修改 | [metadata.py](../../features/caption/src/telepiplex_caption/metadata.py) | 传递 Search 已确认的类别。 |
| 修改 | [service.py](../../features/caption/src/telepiplex_caption/service.py) | 配置方案选择、状态展示、无适用来源与后续查找提示。 |
| 修改 | [config.default.yaml](../../features/caption/config.default.yaml) | 默认 balanced 与 enabled 语义说明。 |
| 修改 | [config.schema.json](../../features/caption/config.schema.json) | 接受可选 balanced／all。 |
| 修改 | [manifest.yaml](../../features/caption/manifest.yaml) | 插件版本提升 1.3.0。 |
| 修改 | [pyproject.toml](../../features/caption/pyproject.toml) | Python 包版本同步 1.3.0。 |
| 修改 | [test_engine.py](../../features/caption/tests/test_engine.py) | 验证 18→4 调度、全部来源、正文选优、详情隔离与限额。 |
| 修改 | [test_metadata.py](../../features/caption/tests/test_metadata.py) | 已确认类别传递，不从原始语言猜类型。 |
| 修改 | [test_service.py](../../features/caption/tests/test_service.py) | 来源配置补丁、验证和结果文案。 |
| 修改 | [test_caption_host_contract.py](../../tests/test_caption_host_contract.py) | 默认／旧配置与 Schema 兼容验证。 |
| 修改 | [test_technical_identity_migration.py](../../tests/test_technical_identity_migration.py) | 当前版本合同更新 1.3.0。 |
| 修改 | [Caption README](../../features/caption/README.md) | 当前组合、配置升级、来源实证边界。 |
| 修改 | [Caption CHANGELOG](../../features/caption/CHANGELOG.md) | 新增 1.3.0 更新说明。 |
| 修改 | [根 README](../../README.md) | 当前版本、简化行为与升级顺序。 |
| 修改 | [README_EN](../../README_EN.md) | 英文版本表与能力说明同步。 |

## 实际验证

Python：`/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`；字幕测试依赖使用既有 `/tmp/telepiplex-caption-test-deps`。

```bash
cd /Users/young/Documents/telepiplex/features/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:src:../../sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider tests

cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-caption-test-deps:.:sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider tests
```

- Caption：**473 passed**。
- Host 全量最终：**730 passed、1 skipped、333 subtests passed**。首次发现 README_EN 版本表未同步，修正后重新执行全量通过。
- JSON 证据解析通过；实际确认 `.git`、`.worktrees` 不存在，`.stfolder` 保留。
- 新增检索合同证明：旧 18 个 enabled=true 配置在明确真人电影、无 Key／Cookie／hash／roots 时只有 4 个来源进入工作槽位；两份有效 Key 仍允许更优双语 ASS 战胜另一来源单语 SRT。实际正文检查始终保留。
- Download、Search、Rename、Sync 本轮源码未改，未重复运行其独立全量测试；本次 Host 全量包含 Caption 业务链路与安装依赖合同。未运行 Docker／GitHub Actions 正式构建，未生成发布包。

等待 Syncthing 显示 **Up to Date / 最新** 并同步到 Unraid `/mnt/user/archives/life hacker/telepiplex`，由用户检查和发布。Mac 本轮没有执行 Git、创建标签或发布。
