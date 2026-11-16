# Telepiplex 配置语义整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本文是待审阅的整合建议；本轮只交付计划，不执行实现、不调整版本。

**Goal:** 在已有单一主 YAML 的基础上，使同一个配置事实只维护一次，目录、开关、凭据和超时的说明与实际行为一致。

**Architecture:** 保留 Host 负责主配置事务、Feature 独立安装及消费隔离快照的架构。先修复显式值与实际生效不一致，再建立 115 目录定义与用途绑定，由 Host 向旧插件投影原有字段；最后收敛 Telegram 编辑入口与模板生成来源。

**Tech Stack:** Python、PyYAML、JSON Schema、pytest、现有 Host RPC 与 Telegram 配置会话。

**Spec:** 本文“建议方案与语义契约”是本轮新增设计建议；基础设计见 [集中配置设计](../specs/2026-11-16-centralized-config-design.md)。基础设计已获授权，本轮目录引用、开关整合及 v3 迁移尚未实施。与基础设计不同之处在本文明确列出，不回写历史设计。

## Global Constraints

- 唯一用户配置仍为 `/config/config.yaml`；插件包的 default/schema 是契约，进程快照是生成结果，都不是第二份用户配置。
- 保留 Telegram `/config` 编辑；基础配置应可在安装插件前填写，安装后直接使用。
- 默认保持现有实际目录、菜单内容及顺序、媒体整理和字幕交付行为；迁移不移动、重命名或创建用户文件。
- 不新增“暂存目录”业务，不让手动下载菜单自动继承分类目录，不因为配置某项就自动安装或启用插件。
- 保留独立安装、更新、回滚和最新授权；不得要求五个插件同时升级才能恢复运行。
- 主 YAML、Feature `config_schema_version`、Host API、包版本是四种不同版本，分别管理。
- Mac 只读写源码、文档并做本地验证；禁止 Git、`.git`、`.worktrees`、连接本项目 GitHub 或发布。保留 `.stfolder` 和其他 chat 的修改。
- 产品文案用 Telepiplex，技术标识保持既有小写。交付等待 Syncthing `Up to Date / 最新`，由用户在 Unraid `/mnt/user/archives/Life Hacker/Telepiplex` 发布。

## Review Focus

1. 旧字段被新默认值遮蔽：从 raw YAML 到 adapter 的测试必须保住显式 `0`、`false`、空值与优先级（Task 1）。
2. 看起来相同的目录实际跨 115、容器文件系统、HTTP 路由：不能跨域去重，也不能偷偷改写 Unicode、大小写或路径段空格（Task 2、3）。
3. 不同旧开关组合及未安装/停用插件：整合后不得意外打开自动字幕，业务总开关仍有效（Task 4）。
4. 新旧包混装、更新失败后重试：旧快照字段合法，已存在 release 可复用，配置迁移不依赖五插件升级顺序（Task 3、7）。
5. 迁移、Telegram 草稿与 Token 刷新交错：仅提交事务拥有的字段，冲突时不覆盖新授权，不回显秘密（Task 3、5）。

## 1. 审计结论：文件集中不等于语义整合

当前已把用户值集中到主 YAML，但只对四分类和 TMDB 凭据做了共享映射。其他配置保留原插件形状，向导也保留原入口，因此出现三类重复：同一事实重复填写、同一事实有多个编辑入口、同一参数的默认值/范围在代码中重复定义。

### 1.1 路径的实际用途

| 当前字段 | 实际语义与消费者 | 整合决定 |
| --- | --- | --- |
| `shared.category_folder[].path` | 115 四分类；Search 下载目标、Rename 分类盘点、Sync 的 Plex 库映射 | 目录实体引用；`kind`、显示名称、Plex ID 属于分类绑定 |
| `features.download.save_directories[].path` | `/magnet` 手动下载菜单；不是 Search 分类菜单 | 引用目录实体，但保留原菜单名称和顺序，空列表仍为空 |
| `features.rename.unorganized_path` | `/rename` 及字幕盘点使用的额外根目录 | 名称说明改为“额外盘点目录”；保留用途，不改成下载暂存/兜底移动目标 |
| `features.caption.output_path` | 没有关联视频时的独立字幕导出目录 | 引用独立导出用途；有关联视频时仍跟随视频所在目录及文件名 |
| `features.caption.providers.local_archive.roots/mapping_file` | 容器本地字幕档案及映射文件 | 本地路径配置，独立于 115 目录；保留只读与防符号链接保护 |
| `plugins.root`、运行时目录 | Host 的持久安装目录、临时 socket/快照目录 | Host 运维配置，明确容器路径及重启要求，不纳入网盘目录表 |
| `features.sync.mcp.path` | HTTP 服务路由，例如 `/mcp` | 文案标为“MCP 路由”，不能套文件路径校验 |

证据入口：Download `directories.py:13` 要求无前导 `/`，提交处 `service.py:1495` 再补 `/`；公共编辑器 `shared_config_handler.py:182` 和 Caption `service.py:45` 要求绝对路径。Rename `service.py:621` 组合盘点根目录，`:2339` 对身份未确认的下载保留 `event.final_path`；`caption_bridge.py:33` 区分视频旁字幕与独立导出。Local archive 见 `catalog_providers.py:371`，Feature 工作目录见 `plugin_supervisor.py:243`。行号为 2026-11-16 审计快照，实施时按符号核对。

### 1.2 重复与生效差异

| 优先级 | 已确认问题 | 处理原则 |
| --- | --- | --- |
| P0 | Download 仍接受 `request_interval`/`poll_interval`，但中央默认合并先补新键，遮住旧 fallback | raw 显式别名先迁移，再补默认；不能只测裸 adapter |
| P0 | Search TMDB/TVDB/AniList timeout schema/UI 为 `1..300`，实际 clamp 为 `5..60` | 迁移展示原配置与原有效值，之后 UI/schema/HTTP 参数统一 |
| P0 | Rename 对 `ai.base_url` 的兼容不一致；实际 AI 请求只用 `api_url` | raw 别名规范化；保留协议含义，不全局替换所有 URL 键 |
| P0 | Search 的 `search.enable` 存在于模板/向导，未发现业务读取 | 建议退役这个无效公开字段，不凭本次整合新增搜索 gate |
| P1 | TMDB 一份存储，公共、Search 快速向导、Provider 页、Sync 向导均可编辑 | 一个编辑所有者；旧入口转到同一公共编辑会话 |
| P1 | Rename `caption_enabled` 与 Caption `automatic` 同时控制下载后字幕 | 一个自动字幕开关；保留 Caption 总开关和 Host 运行状态的区别 |
| P1 | 四个 GitHub 字幕源重复 token/缓存 TTL | Caption 内共享默认，可按源显式覆盖；不合并来源特有策略 |
| P2 | `features.search.search` 重复层级 | 主文件扁平化，旧进程通过投影保留旧结构 |
| P2 | default/schema、两个主模板、向导字段表、runtime fallback 重复参数事实 | default 管默认，schema 管类型/范围，UI 只管分组/顺序；模板同源生成 |

其他证据：`central_config.py:_defaults/effective/_mapped`；Download `client.py:96`、`service.py:1797`；Search `config_wizard.py:18`、`adapters/tmdb.py:52`、`tvdb.py:66`、`anilist.py:34`；Rename `processor.py:606`、`ai.py:238/289`；Caption `engine.py:57` 与 `config.default.yaml:8`（45/30 默认漂移）。

## 2. 建议方案与语义契约

### 2.1 方案取舍

| 方案 | 优点 | 留下的问题 |
| --- | --- | --- |
| 只统一说明、校验和菜单入口 | 迁移小，可先缓解困惑 | 同一物理目录仍在多区重复填写 |
| **目录实体 + 用途绑定 + 唯一编辑入口（建议）** | 路径改一次，所有用途同步；保留各业务角色 | 需要一次可诊断、可预演的 v2 → v3 迁移 |
| 所有近似字段直接合并成全局开关、超时、路径 | 表面字段少 | 混淆不同预算、存储域和业务行为，不采用 |

建议分阶段落地第二项。先完成 P0 修正及回归，再启用结构迁移；不把这份建议当作已批准的实现结果。

### 2.2 115 目录：定义一次，用途引用

建议主结构如下。这是局部示例，不是可直接替换用户文件的模板；ID/路径仅演示，迁移必须来自用户旧值。

```yaml
config_version: 3                    # 建议的新主文件版本；当前仍为 2
shared:
  directories:
    movies_live: {name: 真人电影, path: /真人电影}
    movies_animation: {name: 动画电影, path: /动画电影}
    series_live: {name: 真人剧集, path: /真人剧集}
    series_animation: {name: 动画剧集, path: /动画剧集}
    extra_inventory: {name: 额外盘点, path: /未整理}
    standalone_subtitles: {name: 独立字幕导出, path: /字幕}
  directory_roles:
    media_categories:
      live_action_movie: {directory_id: movies_live, label: 真人电影, plex_library_id: ''}
      animated_movie: {directory_id: movies_animation, label: 动画电影, plex_library_id: ''}
      live_action_series: {directory_id: series_live, label: 真人剧集, plex_library_id: ''}
      animated_series: {directory_id: series_animation, label: 动画剧集, plex_library_id: ''}
    manual_downloads: []             # 保留旧菜单；不会自动加入上面四分类
    unorganized_inventory: {directory_id: extra_inventory}
    standalone_subtitles: {directory_id: standalone_subtitles}
  tmdb:
    api_key: ''                       # UI 统一称 API Read Access Token
features:
  search:
    prowlarr: {}                      # 去掉原来多余的 search 层
    scoring: {}
    metadata: {}
  caption:
    enabled: true                    # 字幕业务总开关，仍保留
    automatic: true                  # 下载后自动字幕的唯一用户开关
    provider_defaults:
      github: {github_token: '', cache_ttl_seconds: 3600}
    providers: {}                    # provider 可显式覆盖，省略时继承
```

- 目录实体只定义 115 目录，不允许任意引用表达式、跨文件 include 或引用链。ID 稳定，与显示名称和路径解耦。
- 分类固定按四个 `kind` 识别。迁移保留每个旧 `name` 为绑定的 `label`；手动菜单也保留自己的 label 和顺序。同一个路径可以被多个用途引用。
- 修改目录路径时展示所有受影响用途；删除被引用目录时报字段路径。禁止悬空 ID，保存前检查全部角色。
- 公共“修改目录”编辑实体，影响全部引用；旧用途向导的 path 补丁只重绑该用途：复用相同路径实体或创建新实体，不修改其他用途的路径。新 UI 把“选择用途目录”与“修改目录本身”分开，旧 callback 不获得更大的修改范围。
- 旧 `unorganized_path: /` 实际不加入额外盘点根；v3 用 `unorganized_inventory: null` 表示这一状态，旧快照投影回 `/`。不能把它解释为全盘扫描。缺键与显式 null 区分，只有缺键采用既有默认。
- 旧包仍收到 `category_folder`、`save_directories`、`unorganized_path`、`output_path`。旧 Download 快照继续使用它能接受的无前导 `/` 形式，主 YAML 与 UI 显示规范绝对路径。
- 新旧包都经同一用途解析器取值；不让各插件再去理解整份主 YAML。旧向导返回的目录补丁转换为目录实体/绑定事务，不能重新保存第二份原始路径。

### 2.3 路径规则与本地路径迁移

- 115 规范显示为 `/一级/二级`。新输入可接受无前导 `/` 的便捷写法；Telegram 输入状态必须能接收这两种写法，不能把绝对路径误当 bot 命令。
- 拒绝 `..`、反斜杠及控制字符；新输入拒绝重复 `/` 和 `.` 段，允许去掉一个末尾 `/`。保留大小写、Unicode 原始码点与目录段空格，不做 NFC/NFKC/casefold，不改用户文件名。
- 根目录权限按用途判断：分类及手动下载不接受 `/`；保留现有独立字幕导出允许 `/` 的行为。额外盘点目录绑定不接受 `/`，用上一节的 null 表示旧有“没有额外根”状态。
- 迁移先计算每个旧消费者的实际路径。只有可证明与其旧有效目标一致的值才能规范化和去重；重复斜杠、点段或不同消费者解释不一致时报告原字段及安全的差异说明，停止自动提交，不猜测 115 服务端如何解释。
- 仅规范路径完全相等才自动复用目录实体；相同名字不同路径不合并。不同用途的 label、Plex ID 等不参与实体去重，也不能丢弃。
- Local archive 的相对 roots/mapping_file 不能直接改成相对 `/config`：当前相对 Feature release 的进程 CWD，`~` 使用运行进程 HOME。预检能确定旧 release CWD/HOME 时固定为原有效绝对路径；不能确定时保留主文件并报字段，要求补绝对路径。保留 roots 顺序，因为 TSV 的 `root_index` 依赖它。
- 保留 local archive 的 no-follow/符号链接检查，不在迁移期间扫描或读取字幕内容。Host 安装目录、运行时目录仍按原权限和重启规则处理；MCP 路由单独校验。

### 2.4 开关、超时、凭据的所有者

**自动字幕：**建议收归 `features.caption.automatic`，Rename 旧 `caption_enabled` 仅作为迁移输入及兼容快照字段。迁移值为旧有效 `Rename.caption_enabled AND Caption.automatic`；任一个为 false 就保留关闭效果，缺失值按旧 release 默认解析。`Caption.enabled` 继续控制包括手动字幕在内的字幕业务，Host 插件运行状态继续存原运行状态文件，三者在 UI 中分别说明。自动开启不强制安装/启动 Caption；状态页显示未生效原因。

**时间参数：**先统一 UI 单位和层级，避免为了命名重做所有包契约。存在歧义的公开键可在 v3 改名并建立明确双向映射；旧进程仍使用旧键。

| 当前参数 | 预算层级 | 决定 |
| --- | --- | --- |
| Caption `request_timeout_seconds=15` | 单次 HTTP 请求 | 保留 |
| Caption `provider_timeout_seconds=45` | 单来源工作预算 | 保留；修复 runtime 缺省 30 的漂移 |
| Caption `retrieval_timeout_seconds=180` | 单媒体检索 | 保留 |
| Caption `automatic_timeout_seconds=240` | 一批自动字幕 | 保留 |
| Rename `caption_timeout=300` | 整个字幕阶段，跨多个最多 50 视频的批次 | 公开说明完整层级；如改名用 `caption_stage_timeout_seconds`，旧快照仍为 `caption_timeout` |
| Search Prowlarr timeout/indexer_timeout/status_timeout | 总搜索、单 indexer、状态请求 | 分别保留，不能合成一个全局 timeout |

Search 三个 metadata adapter 建议以后接受其 schema 已承诺的 `1..300` 秒，不再暗中 clamp。对于旧配置 300、旧实际 60 的情况，迁移建议先写入 60 并显示“原配置 300，原有效 60，已保持 60”。此项属于明确的参数语义修复，必须单独测试与发布说明，不能声称完全没有行为调整。

新 Search 在包 schema 增加机器扩展 `x-telepiplex-metadata-timeout-contract: seconds_1_300_v1`，仅声明 TMDB/TVDB/AniList 的 timeout 实际按秒接受 `1..300`。没有声明的旧包按实际 `5..60` 约束编辑、激活和 reload，超界变更明确要求升级，不能接受后悄悄截断。具备该声明的新包才允许用户明确设 300 并实际请求 300。保持现有 timeout 字段形状，不为识别新行为盲目增加 config_schema_version；Host 从已验证包 schema 读取声明，不能用包版本猜测。未安装时可先填基础字段，但仍以待安装包实际契约决定激活是否可行。

兼容例外：v3 因其他 blocker 不能提交时，v2 原 timeout=300 仍按旧算法投影为有效 60，主文件原值不变，并报告“配置值/有效值/旧契约”差异；不能因此阻止冷启动或无修改 reload。v2 兼容期间所有包仍使用该旧语义，扩展范围须在 v3 成功且新包声明可用后开放。Host 新编辑拒绝超界值；手改 reload 有旧 raw 基线时检查变更，没有基线的首次启动按旧格式兼容并明确诊断，不宣称 300 已生效。

**凭据与服务连接：**TMDB token 由公共编辑器独占，Search/Sync 只显示引用状态和同一个编辑链接；保留机器键 `api_key`，标签改为正确 Token 类型。Prowlarr `X-Api-Key`、TVDB key/PIN、115 API/passport、AI URL 与 AniList GraphQL endpoint 各自保留。TMDB 地址当前存在 Search 可配、Sync 固定的差别，本轮不强制让 Search 代理地址改变 Sync 流量；如后续共享地址，必须同时支持 consumer override 并迁移保留原目标。

**Caption GitHub 默认：**仅对四个 GitHub catalog 源提供 token/TTL 默认；缺键才继承，显式 `github_token: ''` 仍表示匿名访问，其 override 必须保留，即使当前公共 token 也为空。四源旧有效值全部相同时可提为公共默认；不同则保留公共包默认，并按源保留不同有效值，不擅自选多数 token。`title_aliases`、`episode_mappings`、`max_release_pages` 保持来源独有；local archive 的 300 秒 TTL 不并入 GitHub 的 3600 秒默认。

### 2.5 配置元数据与编辑入口

- 包 `config.default.yaml` 是插件默认值来源，`config.schema.json` 是类型/范围来源。schema 可增加标签/说明；UI 表只保留分组、顺序及敏感处理，不再复制默认与范围。
- Host 新增有限的共享/基础设置描述与生成脚本，将 Host 默认、目录绑定、五个 Feature 的 default/schema 组合成两份一致的主模板；不新建跨项目通用表单框架。
- 为安装前编辑，构建时输出受版本约束的基础设置描述；Host 不要求启动插件才知道基础字段。高级业务向导、115 授权及连接测试仍由可用插件提供，且都写同一 Host 事务。
- `/config` 以“目录与用途、服务连接、字幕策略、插件高级参数、运行环境”分组。插件列表主要呈现安装、更新、启停状态；旧配置按钮作为兼容导航，不保留第二套共享输入流程。
- 每项显示来源（用户设置/包默认/共享继承）、用途及当前是否可生效；未安装项允许保存，成功反馈区分保存与实际重载。敏感值不回显。
- 既有未知插件和未知用户字段继续保留。已映射共享字段的 feature 私有影子键须诊断并清理，不能继续留在文件中但被静默覆盖。

## 3. 迁移和兼容契约

建议独立实现纯函数预检与旧包投影，复用现有存储/激活事务，不另造写文件后端。

```python
normalize_115_directory(value: str, *, allow_relative: bool, allow_root: bool) -> str
normalize_explicit_aliases(plugin_id: str, raw: dict) -> dict
plan_v3_migration(document: dict, *, context: MigrationContext) -> MigrationPlan
project_feature_config(plugin_id: str, defaults: dict, *, document: dict,
                       contract: ProjectionContract) -> dict
```

`MigrationContext` 只提供已安装 release 的旧默认/schema、旧 CWD/HOME；不含网络客户端。`MigrationPlan` 包含候选文档、字段级补丁、源/目标修订、无秘密的 notices/blockers。`ProjectionContract` 按明确的包 schema/声明选择快照形状，并包含上述 Search timeout 声明；不靠猜版本号或是否存在某个业务字段。稳定目录解析和 legacy 字段投影可先覆盖当前五个包，不要求新包协议。

处理顺序固定为：**读 raw → 判断显式键与别名 → 解析旧有效语义 → 生成目录/策略迁移 → 合成目标默认 → 按各已安装包投影并校验 → 统一事务提交**。

| 情况 | 必须行为 |
| --- | --- |
| 新旧别名同时存在 | 显式 canonical 优先，包括 `''`、false、0、`[]`、`{}`；报告被遮蔽旧键，删除旧别名不能改变选择结果 |
| 旧 Download request_interval/poll_interval | 在默认合并之前转换；request_interval 按旧 client 规则映射到 offline_poll、offline_mutation、storage_read、storage_mutation、token_refresh 五类（有显式新键则新键优先）；poll_interval 仅映射 initial，保留原 backoff/max 规则 |
| Rename AI base_url | 规范为 api_url；canonical 已显式存在则优先，地址内容不因命名统一而拼接成另一类协议 |
| 无效 search.enable | v3 停止公开展示/写入；迁移说明该旧字段未控制搜索，不凭其 false 给原业务增加关闭行为 |
| 自动字幕两个 gate 不同 | 使用旧有效 AND 结果；总开关及 Host 停用状态各自保留 |
| 同路径多处配置 | 一个实体，多用途绑定；保留每处 label、顺序、kind、Plex ID |
| 公共值与私有影子值冲突 | 展示安全字段路径并阻止自动清理；不输出 token；显式的新公共来源不能被旧导入覆盖 |
| 未安装插件 | 已知内置契约允许配置和预检；不启动/安装插件；未知插件原样保留 |
| 无法确定旧本地相对路径基准 | 不提交；保留原配置，列出需改为绝对路径的字段 |
| 迁移失败或并发更新 | 不提交部分 v3；旧运行实例继续服务，可修正并重试；保留新 Token 和无关编辑 |
| 重复预检、重启或更新重试 | 同一输入产生稳定 ID；已是 v3 不再次导入旧值，存在候选 release 可验证复用 |

提交前保留权限受控的原文备份及迁移摘要，使用当前字段级 CAS、原子替换和相关消费者协调重载。预检不创建目录、不移动文件、不改授权、不发业务请求；有 blockers 时不标记 `config_version: 3`。保留可在 Telegram 取得摘要与重试的入口，错误不能把 Host 和插件更新入口一起卡住。

冷启动也必须进入这一恢复流程：当前 `app/115bot.py:build_plugin_manager` 在创建 manager 前调用 `migrate_legacy`，实施时须把可恢复的语义迁移阻塞转换为明确的诊断状态。合法 v2 可继续旧投影；某插件配置不能安全投影时仅阻止该插件激活，Host 的更新、诊断、迁移修正与重试仍可用。若 YAML 本身不可解析或 Bot 凭据也不可读取，则只能保留文件并输出安全日志，不能承诺 Telegram 入口可用。

代码回滚保持现行“最新设置和授权优先”的约定。旧 Host 不认识 v3，不能直接配旧镜像启动；需要回滚 Host 时先生成经旧 reader/schema 验证的反向兼容配置候选，再从**当前值**投影恢复旧布局。无法无损表达则拒绝该回滚并报告字段；禁止恢复整份升级前备份覆盖新 Token。

## 4. 实施任务与验收

任务顺序为 1 → 2 → 3 → 4 → 5 → 6 → 7。Task 1 是独立修复，Task 2 是路径规则基础；Task 3 完成后才可启用 v3 写入。每个任务先增加会失败的行为回归，再实现，最后运行该任务的测试；不把下列计划命令写成已通过的结果。

### Task 1：先修复生效差异，冻结语义清单

**Files:** 新建 `app/runtime/config_aliases.py`、`tests/test_config_aliases.py`；修改 `app/runtime/central_config.py`；对应 Download `client.py`/`service.py`、Rename `ai.py`/`processor.py`、Caption `engine.py`；对应各包 schema/default。核对 Search `adapters/{tmdb,tvdb,anilist}.py` 并固定旧行为测试，新超时契约在 Task 4 实现。运行时消费文件均位于 `features/<id>/src/telepiplex_<id>/`。

**Interfaces:** 产出 `normalize_explicit_aliases(plugin_id: str, raw: dict) -> dict`，对副本操作且不补默认；超时迁移通知由后续 `MigrationPlan` 记录，P0 修复期间不暗中启用新超时行为。

- [ ] 增加端到端回归：raw `{request_interval: 0, poll_interval: 10}` 经中央投影到实际 client/轮询参数，0 和 10 不被默认遮蔽；canonical 显式存在时优先。
- [ ] 增加 AI 别名、空 canonical、各 metadata timeout 的 1/5/60/300 边界、Caption 缺省预算的回归。将当前无效 `search.enable` 记录为待退役，不增加业务 gate。
- [ ] 先运行新增测试确认失败，再在默认合成前规范别名；统一必要防御 fallback 与包 default。超时行为变更与 v3 迁移一起启用，不能早于旧有效值保存。
- [ ] 验证 `tests/test_config_aliases.py tests/test_central_config.py`，并运行 Download client pacing、Search provider、Rename AI、Caption engine 对应测试；检查实际 HTTP `timeout` 参数而非仅检查配置字典。

### Task 2：建立按存储域区分的路径协议

**Files:** 新建 `sdk/src/telepiplex_plugin_sdk/storage_paths.py`、`tests/test_storage_paths.py`；修改 Download `directories.py`、Caption `service.py`、`app/handlers/shared_config_handler.py` 的重复输入校验；补充 Feature 对应配置测试。

**Interfaces:** 产出上文 `normalize_115_directory(...) -> str`；保留旧 Download 的 root-relative 适配函数，明确其只用于旧快照。容器 `Path` 和 MCP HTTP 路由不调用该函数。

- [ ] 增加路径矩阵：绝对/便捷相对输入、根目录按角色放行、重复 slash、点段、遍历、反斜杠、控制字符、大小写、Unicode 组合字符、目录段空格；断言没有文件系统副作用。
- [ ] 实现规范解析及旧格式转换；不要改动业务路径拼接或媒体文件命名规则。
- [ ] 加 Telegram 输入路由回归：输入 `电影/动作` 和 `/电影/动作` 均进入同一草稿；其他正常 bot 命令仍按既有退出/导航规则处理。
- [ ] 验证 `tests/test_storage_paths.py tests/test_shared_config_handler.py` 和 Download/Caption 的配置输入测试，所有接受值的 UI、主 YAML 校验与快照目标一致。

### Task 3：目录实体、兼容投影及 v3 迁移

**Files:** 新建 `app/runtime/config_projection.py`、`app/runtime/config_semantics_migration.py`、`tests/test_config_semantics_migration.py`、`tests/test_config_projection.py`、`tests/test_config_migration_bootstrap.py`；修改 `app/115bot.py:build_plugin_manager`、`app/runtime/central_config.py`、`plugin_store.py`、`plugin_manager.py`；必要时修改 `features/caption/src/telepiplex_caption/catalog_providers.py` 的本地路径基准处理。

**Interfaces:** 定义 `MigrationContext`、`MigrationPlan`、`ProjectionContract` 及上文两个投影/迁移函数。目录引用解析放在 `config_projection.py`，迁移模块只做计划，不写文件；`CentralConfigStore` 仍为唯一写入者。

- [ ] 编写 v2 完整 fixture，包含分类/手动菜单同路径不同 label、空菜单、独立字幕根 `/`、额外盘点根与旧 `/` 禁用状态、local archive 相对路径和不同 Unicode 路径。
- [ ] 验证 v3 → 旧五包 schema 全部合法，实际 Search/手动下载目标、Rename 根目录、独立导出和视频旁字幕位置与旧 fixture 一致；未知身份下载仍保留 final_path。
- [ ] 实现稳定 ID 分配：固定四 kind 次序，再按旧手动菜单次序，再额外盘点和独立字幕；规范路径相同时复用首次 ID，角色各保留旧 label。既有 v3 ID 不重新生成。
- [ ] 实现引用校验、raw 补丁双向映射、受影响消费者计算、迁移预演及受控备份；公共目录改动协调 Search/Rename/Sync/Download/Caption 中实际相关的运行消费者。测试公共实体编辑影响全部引用，旧用途 patch 仅重绑本用途；按旧实例配置基线识别真正变化，其他用途的旧快照值不能被一起写回。
- [ ] 测试歧义路径、本地基准不明、悬空引用、影子键冲突、写失败、幂等和 Token 并发；逐项比较无关字段与最新授权未变。
- [ ] 增加真实 `build_plugin_manager` 冷启动测试：迁移冲突仍能诊断、更新包、修正及重试，失败前主文件未变；有效 v2 的其他插件不受影响。覆盖“其他目录 blocker + v2 Search timeout=300”：投影为旧有效 60，不写主文件，冷启动及无修改 reload 成功，新超界编辑明确拒绝。
- [ ] 验证 `tests/test_config_semantics_migration.py tests/test_config_projection.py tests/test_config_migration_bootstrap.py tests/test_central_config.py tests/test_central_config_lifecycle.py`，及 Caption local archive 的 no-follow/root_index 回归。

### Task 4：整合开关、凭据、来源默认及超时契约

**Files:** 修改 `app/runtime/config_projection.py`、`config_semantics_migration.py`；修改 Rename `config_wizard.py`/`service.py`、Caption `service.py`/`catalog_providers.py`、Search/Sync `config_wizard.py`、Search `adapters/{tmdb,tvdb,anilist}.py`；对应包 default/schema 和 `tests/test_caption_business_flow.py`、`features/caption/tests/test_catalog_providers.py`、`features/search/tests/test_provider_config.py`、`test_tmdb_adapter.py`。

**Interfaces:** Canonical `features.caption.automatic` 投影为旧 Rename.caption_enabled 与 Caption.automatic；`features.caption.provider_defaults.github` 展开为旧 provider 字段。所有继承按“键不存在”判断。

- [ ] 穷举两个旧自动开关的四种组合、缺省、Caption.enabled=false、插件未安装/停用；迁移前后整体调用/拒绝结果一致。
- [ ] 为 GitHub 相同 token、不同 token、显式匿名 token、不同 TTL 编写迁移与实际请求头回归；秘密不出现在 notices、异常、状态卡片。
- [ ] 实现单自动开关和 GitHub 默认展开；保留手动字幕总 gate、source 特有策略及外层/内层预算区别。
- [ ] 为新 Search 声明并实现 `seconds_1_300_v1`；测试旧 raw timeout 300 迁为有效 60，新包明确设置 300 时 HTTP timeout 为 300，旧包同样操作明确拒绝，1/5/60/300 边界和手改 YAML 的 reload 一并覆盖。
- [ ] 从旧编辑器移除共享凭据的第二套输入，转向 Task 5 的公共会话；旧 callback 仍可安全导航，不能跨插件越权写配置。
- [ ] 验证 Host 字幕业务测试，以及 Rename Caption bridge、Caption service/catalog、Search/Sync wizard 测试。

### Task 5：一个配置事实只提供一个编辑流程

**Files:** 修改 `app/handlers/config_handler.py`、`shared_config_handler.py`、`app/runtime/plugin_manager.py`；按需新增 `app/runtime/config_settings.py` 的有限基础字段分组描述；修改五包已有配置入口；测试 `tests/test_config_handler.py`、`test_shared_config_handler.py` 及 Feature wizard 测试。

**Interfaces:** 复用当前 `configure_shared`/插件 config_patch 事务。Host 公共会话负责目录与 TMDB，Caption 负责字幕策略；旧包配置入口的 shared patch 统一映射，不通过可伪造 callback 扩大写权限。

- [ ] 为四类路径用途、共享 token、自动字幕各编写“多个导航入口只到同一草稿/字段”的交互测试。
- [ ] 增加未安装插件可保存基础参数、未启用插件不被自动启动、secret 不回显、默认/继承/显式值标签及实际重载反馈测试。
- [ ] 实现分组、影响用途预览与兼容导航，删除向导中重复的类型/范围/默认字面量，读取包契约或 Task 6 的构建描述。
- [ ] 验证并发草稿、同字段冲突、取消、重复确认及 Token 刷新；不把旧整包快照展开写回主文件。
- [ ] 运行 Host 配置交互与五包相关 wizard 测试，确认实际落盘只修改一个 canonical 字段及必需的迁移路径。

### Task 6：模板同源与 Search 层级收敛

**Files:** 新建 `scripts/generate_config_templates.py`、`app/runtime/config_defaults.py`、生成文件 `app/runtime/config_settings.generated.json`；修改 `app/115bot.py`、`app/runtime/central_config.py`、`config_projection.py`、`config_semantics_migration.py`、`config_settings.py`、`tests/test_config_template_contract.py`、Search `config_wizard.py` 中重复元数据；重新生成 `app/config.yaml.example`、`config/config.yaml.example`。只改源与新生成物，不手改 build/lib 等旧产物。

**Interfaces:** `config_defaults.py` 集中声明当前 Host 与共享区默认，原运行时消费点改为引用它，不能保留另一组同值字面量。生成器提供 `--check`，从该模块、Feature default/schema 及显式共享映射生成两份模板和 `app/runtime/config_settings.generated.json`；Host 的 `config_settings.py` 从同目录读取这个随镜像携带的基础字段描述（含其来源包/配置 schema 版本），不新增另一份手工默认表。

- [ ] 为字段默认/范围漂移、双模板不一致、缺少公开字段、共享键重复出现、新 Search 形状与旧投影编写回归。
- [ ] 将主文件 `features.search.search.{prowlarr,scoring}` 迁为 `features.search.{prowlarr,scoring}`；保留旧进程结构，并覆盖 raw patch、错误字段路径、legacy import 和空评分表语义。
- [ ] 默认由包 YAML 读取，schema 类型/范围由包 JSON 读取；生成器只按显式投影映射排除旧共享字段，不能靠相似名称猜测去重。
- [ ] 运行生成器、`--check`、`tests/test_config_template_contract.py tests/test_config_projection.py`；断言五包投影过 schema，且生成默认与源默认一致。

### Task 7：混合版本验证、版本计划与交付

**Files:** 修改 `app/115bot.py:get_version`、`sdk/pyproject.toml`、受影响 Feature 的 `manifest.yaml`/`pyproject.toml`、现行 `README.md`/`README_EN.md` 与对应 Feature README；扩展 `tests/test_central_config_lifecycle.py`、`tests/test_config_migration_bootstrap.py`、`tests/test_release_catalog_generator.py`。保持发布脚本和版本校验的现有单源规则，不为了此任务新建另一套版本表。

- [ ] 先完成 Host 兼容桥：能读取 v2/v3，给当前发布包生成旧字段，迁移预检失败也保留更新/重试入口；再开放 v3 提交及相关 UI。原有不支持 Host 授权写协议的旧 Download 门槛继续生效。
- [ ] 测试 Host 先升级、Feature 单个先升级但 Host 不兼容时明确拒绝、旧包混装、失败后重试复用 release、回滚保持新 token、旧 Host 降级候选不可无损时拒绝。不能把“必须记住顺序”作为用户的恢复手段。
- [ ] 临近实现结束重新核对版本，不沿用审计快照盲目递增。本轮基线为 Host 3.10.1、SDK 2.3.0、Download 2.3.0、Search 2.11.2、Rename 2.4.6、Sync 2.1.3、Caption 1.3.5；计划文档不会提升它们。
- [ ] Host 的结构/UI/迁移作为下一个 minor；新增 SDK 路径契约提升 SDK minor；有代码或包契约改动的 Feature 分别递增，有公开行为/配置能力变化用 minor，仅兼容导航/修正用 patch。只有配置形状需要声明式迁移才递增包 config_schema_version，不能拿它代替行为能力声明。
- [ ] 新配置导航及 timeout 声明的消费也属于 Host 能力，即使没有新 RPC 也需建立兼容门槛。以当前 API 1.10 为基线，建议新能力放入下一个 API minor（1.11），修改 `app/runtime/plugin_contract.py` 和实际依赖新能力的包 `host_api` 下限；实施时核对其他 chat 是否已推进版本。旧五包仍由新 Host 兼容，不强制捆绑升级。
- [ ] 执行 Host 与五包完整测试，验证所有旧发布门槛、集中写授权、路径级 CAS 及更新重试回归；人工检查 Telegram 配置保存/迁移摘要，不能用仅 schema 测试代替。
- [ ] 记录实际改变的文件与目的、实际测试结果、兼容范围和恢复步骤。等待 Syncthing `Up to Date / 最新`；用户在 Unraid 发布。不要在 Mac 执行 Git 或发布。

## 5. 计划验证命令与完成标准

实施时在 `/Users/young/Documents/Telepiplex` 使用以下解释器；本轮计划交付不运行或宣称这些测试已通过：

```bash
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

实现完成必须同时满足：

1. 同一 115 路径只定义一次，多个用途保留自己的角色；本地目录和 HTTP 路由不被混入。
2. 用户不必分别在 Search/Sync 填同一 TMDB token，也不必同时配置两个自动字幕 gate。
3. 主配置与 Telegram 对同一参数采用相同约束，实际请求/目标与说明一致；显式空值不被默认覆盖。
4. v2 自动迁移可预演、可幂等重试；无法安全确定时报告字段并保持旧运行，不要求用户手工整体重填。
5. 当前五包混装可工作，授权刷新、失败恢复和新设置保留有测试证据。
6. 两份主模板由同一来源生成；所有实际受影响包提升版本，发布边界保持不变。

## 6. 本轮交付范围

只新增本文。审计采用源码、default/schema、配置投影链及只读内存探针交叉核对；未执行应用测试套件，未改运行代码、用户配置或版本。实施前需审阅新增的目录引用结构、自动字幕单一所有者、旧有效 timeout 保持策略和 v3 迁移契约。

独立可实施性审阅提出的旧向导作用域、额外盘点根禁用、迁移冷启动恢复、新旧 Search 超时声明及 v2 降级交叉场景均已纳入契约与对应测试任务；这不等于实现已获批准或验证完成。
