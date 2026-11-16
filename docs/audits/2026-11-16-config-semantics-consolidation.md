# Telepiplex 配置语义整合交付审计

> 本地实现、七项任务独立审阅与终审修正已完成。当前 Host 与五个 Feature 完整测试退出码均为 0。未发布，未连接线上 Telegram 或 Unraid。

## 依据与范围

- 授权计划：`docs/superpowers/plans/2026-11-16-config-semantics-consolidation.md`。执行裁决以 `docs/superpowers/plans/2026-11-16-config-semantics-consolidation-progress.md` 为准；原计划、旧设计、旧审计和 `.superpowers` 快照不回写。
- 工作边界：`AGENTS.md`；仅在 Mac `/Users/young/Documents/Telepiplex` 读写源码和本地验证。没有在 Mac 使用 Git、创建 `.git`/`.worktrees`、连接本项目 GitHub 或发布。
- 实现及复审证据：`/tmp/telepiplex-config-semantics-20261116/task-{1..7}-report.md`，及各任务最终独立审阅。本报告汇总实际运行与独立复审，不把本地模拟交付描述为线上验证。
- 文件清单按原始本地快照与当前源逐文件 SHA-256 对账，共 **111 个路径：新增 29、修改 82，无删除或重命名**，含本审计。快照排除历史 build、缓存、`.superpowers`、符号链接及超过 2 MB 文件；收尾对快照中45个历史 egg-info 文件另行核对，与初始快照字节一致，包含实施/复审已专门核对的34项。

## 已形成的配置行为

### 一个配置源，明确的所有者

唯一用户配置仍为 `/config/config.yaml`。Feature 包的 `config.default.yaml`、`config.schema.json` 是包契约；Host 生成的隔离进程快照、随镜像携带的基础字段描述和迁移备份不是第二份用户配置。

主文件 v3 使用 `shared.directories` 定义 115 目录，`shared.directory_roles` 绑定四分类、手动下载菜单、额外盘点和独立字幕导出。相同规范路径复用实体，显示名称、分类 kind、Plex 库 ID 和菜单顺序仍属于各用途。手动菜单保持原内容及顺序，空菜单仍为空，不自动继承四分类。

公共“修改目录”会影响其全部引用；旧用途入口的 path 补丁只重新绑定该用途，通过复用或新建实体完成，不能借旧 callback 修改其他用途。目录删除检查全部引用；修改和重绑以字段级比较及只读依赖保护并发编辑。实体 ID 分配按固定 kind/旧菜单/额外根/字幕目录顺序稳定生成；菜单显示顺序单独保存为可选 `media_category_order`，既有 v3 ID 不在重启时重新分配。

115 路径、容器本地档案路径和 MCP HTTP 路由保持不同存储域。115 新输入支持绝对路径及便捷相对写法，规范为绝对显示，允许去掉一个结尾 `/`；拒绝重复斜杠、`.`、`..`、反斜杠和 Unicode Cc 控制字符。保留大小写、Unicode 原始码点和路径段空格，不做 Unicode 归一化或段内 trim。分类、手动下载不接受 `/`；独立字幕导出可用 `/`；额外盘点禁用使用 `null`，不能把它当成全盘扫描。

兼容快照继续提供旧 `category_folder`、Download 的无前导 `/` `save_directories`、Rename `unorganized_path` 和 Caption `output_path`。有关联视频的字幕继续跟随视频位置/命名；独立字幕使用独立导出目录。未确认身份的下载继续保留 `final_path`。迁移不移动、重命名或创建用户媒体文件，也不新增暂存目录业务。

分类及额外盘点的末尾空白因为现有消费链解释不一致而保守阻塞，错误指出字段；不会 trim 成另一个目录。开头段空格及内部空格保持；独立字幕 `/after ` 的末尾空格按实际交付链保留。Download 手动目录外围空格只有声明对应新行为的包才能按新语义激活。

### 显式值、开关、凭据和时间预算

- raw 别名先于默认合成。显式 canonical 值包括 `0`、`false`、`''`、`[]`、`{}` 都优先，合法性仍由契约校验。Download `endpoint_intervals` 整体存在时不从旧 `request_interval` 补子项；仅缺失时按旧五类别规则转换。`poll_interval` 只补缺失的 `poll_initial_interval`，不放宽既有合法范围或修改安全 pacing 下限。
- Rename `ai.base_url` 仅兼容映射到缺失的 `ai.api_url`；显式空 `api_url` 仍禁用推断。就绪判断与实际 HTTP 地址采用同一优先级，不拼接成另一类协议地址。
- 下载后自动字幕唯一公开策略为 `features.caption.automatic`。迁移取旧有效 `Rename.caption_enabled AND Caption.automatic`，任一 false 都保持关闭。`Caption.enabled` 仍是含手动操作的业务总 gate；Host 安装/运行状态独立，保存策略不会安装或启用 Caption。
- Rename 整个字幕阶段预算公开为 `caption_stage_timeout_seconds`，旧快照仍为 `caption_timeout`。Caption 的单 HTTP、单来源、单媒体、一批自动字幕预算分别保持 15/45/180/240 秒；缺省单来源运行预算修正为 45 秒。Prowlarr 总搜索、单 indexer、状态请求仍是独立预算。
- Search 的 TMDB/TVDB/AniList 新契约按秒接受 `1..300`，值直接到实际 HTTP 请求；非法 null/bool/非有限数/容器/超范围值在请求前拒绝，不用默认或 clamp 掩盖。迁移把旧 raw 300/旧实际 60 保存为 60，并给出差异说明；旧 raw 1 同理保存旧实际 5。新包且 v3 成功后，用户再显式设置 300 才实际使用 300。
- v2 因其他 blocker 保留时继续按旧有效 `5..60` 投影，即使 Feature 已升级，原 raw 300 仍不改写且实际为 60。无修改 reload 和可读插件冷启动不因此失败；有旧 raw 基线时，新手改超界值在 reload 拒绝。首次 v2 启动按旧格式兼容并提供诊断。
- TMDB 的唯一编辑者是公共 Host 会话，机器键 `api_key` 保留，UI 按 API Read Access Token 说明。Search/Sync 入口转向同一会话。Search 的可配 TMDB endpoint 不改变 Sync 原目标；Prowlarr、TVDB、115、AI、AniList 的各自连接仍保留。
- 四个 GitHub 字幕源只在键缺失时继承公共 token/TTL；显式 `github_token: ''` 保持匿名，即使当下公共 token 也为空。四源相同旧有效值可提为公共默认，不同则保留各源有效 override，不选“多数 token”。来源特有策略和 local archive 的独立 TTL 不合并。
- `features.search.search.{prowlarr,scoring}` 在主文件扁平为 `features.search.{prowlarr,scoring}`；进程仍收到旧层级。显式空 scoring map 按整体替换，不把删掉的条目重新合并回来。无业务读取的旧 `search.enable` 在 v3 退役，不新增关闭搜索的 gate。

### 同一个事实的编辑、默认与模板

`/config` 提供有限的“目录与用途、服务连接、字幕策略、插件高级参数、运行环境”入口。已知未安装插件可先保存基础设置，停用插件不会因此启动；成功反馈区分保存与实际切换消费者。高级业务交互、115 授权/刷新和连接测试继续由各插件负责。

Feature 只可返回 Host 校验的有限 `config_navigation` 目标，必须关闭原会话且不携带 `config_patch`。Host 校验当前路由、实际 chat/user、operation、代际和退役状态后新开卡片；普通业务命令不能使退役配置草稿重新取得写权。Host 草稿按 `(chat_id, user_id)` 隔离，确认绑定 nonce/期限。旧 callback 保持原作用域，不开放任意 Host callback。秘密不回显，来源显示为用户设置、包默认或共享继承。

多个旧字段同时无效时，编辑器收集有限允许字段的批量修复草稿，完整候选通过包校验后才原子提交。校验失败保留草稿以继续编辑；同字段冲突拒绝整批。目录选择、当前值、全部影响用途均分页，不省略长路径或引用。未纳入有限修复字段的扩展策略仍需编辑主 YAML 或更新相应包。

`config_defaults.py` 是当前 Host/共享默认来源，包默认/schema 是 Feature 默认及类型/范围来源。生成器生成两个字节相同的 v3 主模板和同目录基础字段 JSON 描述；运行时不读取源码 `features/`。描述保留包原始契约及版本来源，已安装 release 优先。共享目录默认由 Host 所有，包内对应旧字段保留为进程契约；生成器发现两边名称/路径/顺序不一致必须拒绝或无损映射，不能静默丢弃。

合法 `restart_limit: 0` 与 `stabilize_seconds: 0` 冷启动现与热重载一致。模板中的 allowed_user 仅是示例，不能成为授权 fallback。独立 Dispatcher 的 30 秒与 Host 配置的 1800 秒是不同默认预算。

## 自动迁移、阻塞修复、升级与回滚

1. Host 冷启动建立 manager 后进行语义 bootstrap，把可恢复的阻塞保留为诊断而不使管理面整体退出；恢复子进程时按各自投影决定是否激活。顺序是读 raw → 显式别名 → 按旧契约解析有效语义 → 生成完整 v3 候选 → 合成默认 → 按已安装及内置包契约投影/校验 → 统一事务提交。未安装的已知包依赖随镜像生成描述，不安装或启动它们；缺少契约不猜测，保留 v2 并列 blocker。未知插件/未知未映射字段保留。
2. 预演是纯计算，不扫描用户媒体、不读字幕内容、不创建目录、不改授权、不发业务请求。local archive 相对路径与 `~` 只有确定旧 release CWD/HOME 时才固定为原有效绝对路径，roots 顺序及 TSV `root_index` 保留，no-follow/符号链接保护保留；基准未知就报告字段并阻止自动提交。
3. 成功提交前写入权限 `0600` 的原始字节 `config.yaml.pre-v3.<uuid>.bak` 和不含秘密的 `.summary.json`，主文件原子替换。字段 CAS 和受影响消费者的候选启动/协调切换在同一事务中；失败恢复旧实例及事务自己仍拥有的字段，不覆盖新授权或无关编辑。
4. blocker 不标记 v3，不提交部分 v3。能安全投影的旧 v2 消费者继续服务；只隔离不能安全投影的插件，更新、诊断、有限修复及重试入口保留。共享值改动仍协调相关消费者，不因别的插件阻塞而留下旧共享值。合法旧 v2 raw 300/有效 60 可冷启动和无修改 reload。
5. 操作者从 `/config` 查看预演/字段诊断，批量修复允许字段或修改主 YAML；需新能力的只更新相应 Feature，再重试语义迁移。相同输入产生相同 ID；候选 release 已存在时按契约验证后复用。不会要求五包同步升级才能恢复管理面。
6. 三项定向行为声明分别是 Search `seconds_1_300_v1`、Rename `null_disables_extra_root_v1`、Download `preserve_segment_spaces_v1`。Host 从已验证 schema 读取能力，不靠包版本猜测。旧 Rename 只在 null 额外根语义相关时受门槛；旧 Download 只在会改变解释的手动目录值相关时受门槛；旧 Search 扩展范围须先完成 v3。既有 Download `host_rpc_v1` 授权写协议门槛继续生效。
7. **Feature 代码回滚**保留最新设置和 Token。回滚包必须能消费当前投影并通过其真实契约，不能用旧安装快照/整份备份覆盖新配置；不能表达当前行为就拒绝该包回滚。
8. **旧 Host 降级**是另一项操作。旧 Host 不读 v3，不能直接换旧镜像启动；`export_v2_candidate` 从当前 v3 值投影每个目标旧包，验证 schema、v2 reader 及往返快照，返回独立 v2 候选。新 300 秒等旧 Host 不能无损表达的设置必须拒绝，不能悄悄截断或恢复升级前备份。该接口不写主文件、不切换镜像，也不代表已经执行了降级。

v2 或 v3 独立切换已有 Caption release，会对相对或依赖 HOME 的 archive roots/mapping_file 在切换前按字段拒绝，保持旧实例与原配置；先将对应字段修复为明确绝对位置即可重试，不要求先解决其他插件的 v3 blocker。同版本内可保留原相对路径；根顺序、root_index、TSV 与 no-follow 行为保留。

目录图预检验证全部实体，包括未绑定项；同一规范路径不能由两个 ID 定义，v3 不接受遗留 shared.category_folder 影子。图写入在合并最新文档后复验，独立不同路径的并发编辑仍可合并；无关授权写入和未消费坏角色的插件继续可用。稀疏 v3 的旧用途补丁只物化被编辑角色及必要实体，禁用额外根无需创建目录表。

降级除当前进程快照外还核对原始所有权状态：目录身份/未绑定资源、公共 GitHub 继承与显式匿名、自动字幕“默认/显式”均须可逆。不能表达时拒绝精确字段；不会将“恢复默认”悄悄固定成显式开启。

边界：已有 v1 私有配置 importer 仍为独立 v1→v2 原子操作；它成功后可能已生成 v2，而后续 v3 预检阻塞。“不提交部分 v3”不能误读成整个 v1→v3 链在任意阻塞时字节完全不变。YAML 不可解析或 Bot 凭据不可读时，只能保留文件并输出安全错误，不保证 Telegram 管理入口可用。

## 执行裁决清单（按 ledger 原顺序）

| 序号 | Ruling 与实施取舍 | 判断错误的代价 |
| --- | --- | --- |
| 1 | 用户 Mac 禁 Git 的边界覆盖技能的 Git/worktree/commit 步骤；改用本地源快照、逐任务审阅和可核对日志。 | 破坏唯一 Unraid Git 工作区及同步边界，引入未经授权的仓库状态或发布动作。 |
| 2 | `endpoint_intervals` 是完整 canonical 字段；显式存在含空 map 时旧 `request_interval` 不补子项，缺失才按旧五类别 fallback 转换；非法显式值仍由 schema 拒绝。 | 用户显式 pacing 被旧别名/默认改写，实际请求节奏与配置不符，或意外放宽非法输入。 |
| 3 | `MigrationContext` 同时接受已安装历史 release 契约与 Host 镜像内置契约；后者由同源描述提供，运行容器不读源码 `features/`；缺描述保留 v2 并诊断，不猜测迁移。 | 未安装项无法预配置，或用错误默认静默迁移；开发 checkout 测试通过而真实镜像失败。 |
| 4 | 共享配置没有现成响应动作，采用 Host 校验的有限导航目标并生成按钮；不开放任意 Host callback 或通过导航写配置。 | Feature 伪造跨作用域写权，迟到响应绕过确认或覆盖凭据。 |
| 5 | Task 3 一次定义目录引用、Search 扁平、自动字幕 AND 和来源默认的完整 v3；Task 4/6 消费该结构；缺构建契约保留 v2，不产生需再次迁移的半成品 v3。 | 文件标记 v3 但语义不完整，后续升级再次解释旧值并破坏幂等性或恢复路径。 |
| 6 | Caption GitHub 继承仅由 Host 在 raw 显式判断之后、包默认合成之前展开；插件仍消费旧 provider 快照，旧 schema/default 协议字段保留，模板去重复继承项，显式匿名 override 保留。 | 双重继承互相遮蔽，把匿名变认证或丢失各源 TTL/策略，包独立运行契约漂移。 |
| 7 | 旧 Caption `validate_config` 的规范化返回值未写回 raw；实际 `_place → Rename.name_subtitle → Download.create_directory` 证明 `/after ` 保留到 HTTP `file_name`，迁移/降级按实际链保留；旧 UI 预 trim 单独处理。 | 把字幕交付到另一个目录，错误宣称无行为变化，降级再损失路径信息。 |
| 8 | 旧 Rename `/` 自身表示无额外根，旧 Caption scan_library 却列为全盘根；共享枚举修正一致性并声明 `x-telepiplex-inventory-root-contract: null_disables_extra_root_v1`。仅相关 null 语义迁移/激活门槛，保留 v2 管理/升级。 | 意外全盘盘点，或全面封锁旧包导致用户无法定向升级恢复。 |
| 9 | 旧 Download 启动 trim 手动目录外围空格，新实现保留；同形 schema 无法识别，声明 `x-telepiplex-download-directory-contract: preserve_segment_spaces_v1`，仅相关目录值要求新能力，不捆绑五包。 | 同一路径在不同包落到不同目标，或为无关值强制整组升级。 |
| 10 | 分类和额外盘点末尾空白存在 Caption 菜单/旧清理解释分歧；迁移及 v3 保存/激活报字段，既不 trim 也不移动。保留开头/内部空格与已证明的独立字幕末尾空格；不扩展第三组能力或广改执行器。 | 静默改变目录、清理保护失效；扩大修复面引入无证据的业务行为变化。 |
| 11 | 稳定实体 ID 顺序和菜单展示顺序分离；可选 `media_category_order` 必须是四 kind 的完整无重复列表，缺省标准顺序，迁移/投影/降级保留旧合法展示顺序，实体仍固定 kind 分配。 | YAML 排序或重启改变菜单，或同输入迁移产生不同 ID、错绑用途。 |
| 12 | 默认收敛仅针对有证据的合法 `restart_limit: 0`、`stabilize_seconds: 0` 冷启动 fallback 修正；不批量改变其他非法零/空值。allowed_user 示例不能变授权默认；Dispatcher 30 秒与 Host 1800 秒继续区分。 | 合法禁用值失效，或无意更改权限/重试/截止时间，使模板变成运行时授权。 |
| 13 | 多个显式旧字段同时无效时收集有限允许字段的批量修复草稿；完整候选校验后原子提交，不绕过包校验启动坏配置，不建第二用户配置；目录选择、当前值、完整影响列表均分页。 | 修一个字段仍因另一个失败而无法恢复，部分写回损坏配置，或 Telegram 限制隐藏真实影响。 |
| 14 | 当前共享目录及用途默认归 Host；包映射字段保留进程契约。工厂生成遇到 Download/Rename/Caption 对应默认冲突要明确拒绝或无损映射；未来改包映射默认要同步协调公共默认/映射，不能只刷新产物。 | 模板静默丢失合法菜单/目录、名称或顺序；源配置与生成物看似一致但实际默认行为被改写。 |
| 15 | Caption release 切换须保持旧有效 archive 位置；不可安全表达时按字段拒绝。本轮对v2/v3相对/HOME值先拒绝切换，引导显式绝对化后重试，不要求先完成全局v3。 | 用户需多一次字段修复；若误放行，可能读取另一个归档或在以后重试时固定错误位置。 |
| 16 | 旧 Host 候选须保留目录资源及来源继承/显式匿名和自动字幕默认意图；不能可逆表达就按字段拒绝，不以快照相同当作无损，不恢复旧备份。 | 可降级范围更窄；若漏检，配置看似相同但资源或以后默认/共享修改的效果会丢失。 |
| 17 | 第三方宽松旧 Download 的双规范化歧义可定向拒绝旧运行目标，保留无关服务及兼容新目标恢复，不扩大到所有旧包。 | 该窄情形需更新包或修复路径；若误放行可能静默使用另一目录，若过度拒绝会影响无关旧包。 |

## 最终源码版本（尚未发布）

| 对象 | 原基线 | 当前源版本 | 选择理由/兼容边界 |
| --- | --- | --- | --- |
| Host | 3.10.1 | 3.11.0 | v3 结构、迁移恢复及统一配置 UI 的 minor。 |
| Host API | 1.10 | 1.11 | 有限配置导航、路径语义及按包声明的超时能力；五个本轮新包 `host_api` 下限均为 `>=1.11,<2.0`，旧包保留各自下限。 |
| SDK | 2.3.0 | 2.4.0 | 新增公共 115 路径契约的 minor，随 Feature 打包，无需单独安装。 |
| Download | 2.3.0 | 2.4.0 | 手动目录段空格保持与公共编辑语义；`config_schema_version: 1` 保持。 |
| Search | 2.11.2 | 2.12.0 | 公开 HTTP 超时 1..300 能力；`config_schema_version: 2` 保持。 |
| Rename | 2.4.6 | 2.5.0 | null 禁用额外根的消费一致性与公共配置；`config_schema_version: 2` 保持。 |
| Sync | 2.1.3 | 2.1.4 | 共享配置兼容导航修正；`config_schema_version: 1` 保持。 |
| Caption | 1.3.5 | 1.4.0 | 单自动策略及 GitHub 公共继承的公开配置能力；`config_schema_version: 1` 保持。 |

主 YAML 版本、包配置形状版本、Host API 与包版本是独立维度。新行为以 schema 扩展声明识别，未改变旧进程配置形状的能力不盲目提升 `config_schema_version`。此表从最终源码核对，不表示已发布。

## 文件变更明细（逐路径）

状态：A=新增，M=修改。此处每个路径相对 `/Users/young/Documents/Telepiplex`；同一路径跨任务修改只计一次。

### Host 源码与生成内容

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `app/115bot.py` | 先建立可恢复管理面再语义 bootstrap；冷/热运行参数及 catalog/polling 使用统一默认，保留合法零值。Host 版本提升为 3.11.0。 |
| M | `app/config.yaml.example` | 同源生成完整 v3 示例，目录用途、扁平 Search、GitHub 公共继承及 Host 运维字段与源一致。 |
| M | `app/handlers/config_handler.py` | 安装前设置入口、真实会话 reentry、路径/命令区分、动态 Feature 命令切换及交付代际校验。 |
| M | `app/handlers/plugin_handler.py` | 有限共享导航、活动实例/owner/operation/代际检查，阻止迟到配置写回，结束原生配置后打开新 Host 卡片。 |
| A | `app/handlers/settings_editor.py` | Host 设置/目录编辑、迁移预演重试、有限批量修复、确认 nonce 及长内容/选项分页。 |
| M | `app/handlers/shared_config_handler.py` | 共享配置导航及反馈、Token 确认；按 chat/user 保存草稿，允许修复已知非法旧值并保留 CAS。 |
| M | `app/init.py` | Logger 默认级别读取统一 Host 默认。 |
| M | `app/runtime/central_config.py` | 唯一写入者：raw 别名、v2/v3 结构读取、字段/依赖 CAS、迁移备份摘要、原子提交、有限回滚及 legacy import。 |
| A | `app/runtime/config_aliases.py` | 纯 raw 显式别名规范化；保留 canonical 优先及 falsey 语义，不合成默认。 |
| A | `app/runtime/config_defaults.py` | 当前 Host/共享默认与基础 schema、GitHub 源 ID 和仅示例凭据来源；历史 v2 默认独立冻结。 |
| A | `app/runtime/config_projection.py` | 目录引用/角色解析、历史有效路径、各包隔离快照、能力门槛、自动策略及 GitHub 继承。 |
| A | `app/runtime/config_role_edits.py` | canonical/旧字段反向映射、单用途重绑、菜单/评分整体写入及实体依赖 CAS。 |
| A | `app/runtime/config_semantics_migration.py` | 无副作用完整 v3 预演、稳定 ID、历史值/本地路径迁移、blocker/notices 与当前值 v2 降级候选。 |
| A | `app/runtime/config_settings.generated.json` | 随 Host 镜像提供版本化原始包 defaults/schema 及 Host 基础字段契约，支持未安装项编辑和预检。 |
| A | `app/runtime/config_settings.py` | 有限字段所有权、已安装/内置契约读取、schema 引用解析、来源/敏感显示、保存/重置/批量修复校验。 |
| A | `app/runtime/directory_settings.py` | 目录实体与用途编辑分离，影响/引用预览、删除保护及角色/实体读依赖。 |
| M | `app/runtime/event_dispatcher.py` | 重试次数/间隔读取统一 Host 默认；独立投递截止预算保持其原所有者。 |
| M | `app/runtime/plugin_manager.py` | 冷启动/预演/重试状态、集中设置和目录事务、恢复 reload、相关消费者协调切换及真实包能力校验。 |
| M | `app/runtime/plugin_store.py` | stage/migrate/read/enable/rollback 使用验证过的投影契约；保留升级源读取恢复，错误位置翻译为公共 v3 路径。 |
| M | `app/runtime/plugin_supervisor.py` | 独立 supervisor 默认与 Host 参数来源一致。 |
| M | `app/runtime/plugin_update_monitor.py` | catalog 刷新间隔读取统一 Host 默认。 |
| M | `config/config.yaml.example` | 与 `app/config.yaml.example` 同源、逐字节一致的 v3 配置示例。 |

### SDK、生成器与执行记录

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| A | `sdk/src/telepiplex_plugin_sdk/storage_paths.py` | 纯 115 路径规范解析，供 Download 等消费者适配其快照形状，统一规则而不访问文件系统。 |
| A | `scripts/generate_config_templates.py` | 按 Host/共享默认及五包原契约生成/检查三份产物，验证映射默认无损，不依赖调用 CWD。 |
| M | `docs/superpowers/plans/2026-11-16-config-semantics-consolidation-progress.md` | 记录授权顺序、权威裁决、逐任务 RED/GREEN 与独立复审状态；保留原计划。 |

### Download

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `features/download/config.schema.json` | 声明路径段空格保持能力，快照路径语法与 SDK 一致。 |
| M | `features/download/src/telepiplex_download/client.py` | 旧 pacing 显式零值不被 truthiness 默认遮蔽，保留实际安全下限。 |
| M | `features/download/src/telepiplex_download/directories.py` | 共用 SDK 路径规范并保持旧无前导 `/` 的进程契约，保留段空格。 |
| M | `features/download/src/telepiplex_download/service.py` | 115 基础连接/手动目录统一导航；保留授权刷新、业务参数及实际下载选择。 |
| M | `features/download/tests/test_feature_runtime.py` | 实际轮询、目录输入/写回、路径 schema 与 SDK 一致性、共享导航及旧会话无秘密写回回归。 |

### Search

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `features/search/config.schema.json` | 声明真实 metadata `seconds_1_300_v1` 行为，不以配置形状版本代替行为能力。 |
| M | `features/search/src/telepiplex_search/adapters/anilist.py` | 严格解析 metadata 配置，合法超时直接用于实际 POST。 |
| A | `features/search/src/telepiplex_search/adapters/config_values.py` | 三 metadata adapter 共享安全类型/范围校验、缺失默认和字段级错误。 |
| M | `features/search/src/telepiplex_search/adapters/tmdb.py` | 严格解析并把合法秒值直接用于实际 HTTP 请求。 |
| M | `features/search/src/telepiplex_search/adapters/tvdb.py` | 严格解析并同时约束登录/数据 HTTP 的实际超时。 |
| M | `features/search/src/telepiplex_search/config_wizard.py` | 公共凭据/连接/超时转向唯一 Host 编辑；业务专用高级参数保留，类型/范围/默认读取包契约。 |
| M | `features/search/tests/test_config_wizard.py` | 单一编辑所有者、导航、来源 schema/default 及仍保留的高级输入回归。 |
| A | `features/search/tests/test_metadata_timeout_contract.py` | 捕获真实 HTTP 参数的 1/5/60/300 秒与非法参数请求前拒绝，含巨大整数。 |
| A | `features/search/tests/test_metadata_timeout_legacy.py` | 明确用旧 Host v2 契约快照冻结 1→5、300→60 历史有效行为。 |
| M | `features/search/tests/test_provider_config.py` | 本地 provider enable/endpoint/timeout 及元数据来源契约，去重复 Token 输入期望。 |
| A | `features/search/tests/test_shared_config_navigation.py` | 新旧/stale TMDB 会话关闭并安全导航，不回显秘密或发配置补丁。 |

### Rename

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `features/rename/config.schema.json` | 声明 null 禁用额外根的一致枚举能力。 |
| M | `features/rename/src/telepiplex_rename/ai.py` | 同一 helper 决定 legacy/canonical AI URL 优先级并用于实际请求。 |
| M | `features/rename/src/telepiplex_rename/caption_bridge.py` | 与盘点共用根目录枚举，保留分类顺序及自动字幕实际调用 gate。 |
| M | `features/rename/src/telepiplex_rename/config_wizard.py` | 基础连接/目录/字幕策略转有限 Host 导航，旧确认不再独立写共享事实。 |
| A | `features/rename/src/telepiplex_rename/inventory_roots.py` | Rename 和 Caption scan_library 共用有序分类/额外根枚举。 |
| M | `features/rename/src/telepiplex_rename/processor.py` | AI 就绪判定共用 URL 优先级，显式空 canonical 继续禁用推断。 |
| M | `features/rename/src/telepiplex_rename/service.py` | 盘点根来源切换到共用 helper。 |
| M | `features/rename/tests/test_ai_structured_output.py` | legacy URL、canonical 空值/优先级及真实 HTTP 地址回归。 |
| M | `features/rename/tests/test_caption_bridge.py` | 两个真实根消费者、自动关闭时不调用 Caption 但继续整理，以及测试 wrapper 参数修正。 |
| M | `features/rename/tests/test_config_wizard.py` | 有限 Host 导航及旧确认关闭回归。 |

### Sync

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `features/sync/src/telepiplex_sync/config_wizard.py` | TMDB/基础共享事实导航到 Host，旧凭据草稿不再独立提交，Fanart/Plex 专用流程保留。 |
| M | `features/sync/tests/test_config_wizard.py` | 共享入口新行为及保留 Fanart/Plex 交互回归。 |
| A | `features/sync/tests/test_shared_config_navigation.py` | 主入口和过期 TMDB 会话安全关闭，不回显秘密或回写旧 Token。 |

### Caption

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `features/caption/src/telepiplex_caption/engine.py` | 缺失单来源工作预算实际 fallback 修正为包默认 45 秒。 |
| M | `features/caption/src/telepiplex_caption/service.py` | 独立导出输入共用 SDK 路径，连接/目录/自动策略/GitHub 默认转唯一 Host 编辑，来源特有业务保留。 |
| M | `features/caption/tests/test_catalog_providers.py` | Host 继承后的真实 provider HTTP Header/缓存 TTL，匿名/独立 override、local archive no-follow 与 roots 顺序覆盖。 |
| M | `features/caption/tests/test_engine.py` | 捕获实际 `asyncio.wait_for` 的缺省 45 秒来源预算。 |
| M | `features/caption/tests/test_service.py` | 路径输入/规范、有限导航、旧 gate AND、手动总 gate 及实际拒绝/调用行为。 |

### Host 与跨包测试

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `tests/test_central_config.py` | raw 别名、结构读取、私有影子冲突、Token 并发、有限事务与修复 CAS。 |
| M | `tests/test_central_config_lifecycle.py` | 安装/停用/候选失败/恢复 reload、批量修复重试和迟到 Sync 写回的真实 Host/YAML 边界。 |
| A | `tests/test_config_aliases.py` | raw→中央投影→真实消费的 canonical/别名优先、falsey、复制/幂等回归。 |
| M | `tests/test_config_handler.py` | 真实 PTB 会话与原生操作→Host 新卡片、命令切换和统一交付校验。 |
| A | `tests/test_config_migration_bootstrap.py` | 实际 `build_plugin_manager` 的保留 v2/插件隔离、更新恢复、raw/effective timeout 与重试。 |
| A | `tests/test_config_projection.py` | 五包旧 schema 快照、目录/自动策略/GitHub/timeout/评分表/公开错误路径回归。 |
| A | `tests/test_config_semantics_migration.py` | 完整 v2→v3、稳定 ID、路径/来源/预算迁移、无秘密预检、幂等和当前值 v2 导出。 |
| A | `tests/test_config_semantics_recovery.py` | 六项首轮恢复缺陷及继承 label/稀疏分类后续修复的并发与真实冷启动回归。 |
| M | `tests/test_config_template_contract.py` | 同源完整模板/描述、原始包事实、缺描述安全失败、合法冷启动零值、生成漂移及有序菜单无损校验。 |
| M | `tests/test_feature_action_contracts.py` | Feature 命名空间、有限导航与旧响应不得再独立写共享事实。 |
| A | `tests/test_host_settings.py` | 未安装/停用配置、字段来源/重置、真实 PTB、目录资源/用途、Token 保留、CAS、迁移修复与权限。 |
| A | `tests/test_host_settings_review.py` | 动态命令、未安装目录契约、批量修复、迟到导航、长内容分页及跨 chat 草稿隔离。 |
| M | `tests/test_shared_config_handler.py` | 路径/命令含大小写、nonce 确认、非法旧值可修复、owner 隔离及真实消息路由。 |
| A | `tests/test_storage_paths.py` | 115 路径语法矩阵、存储域/根用途、大小写/Unicode/段空格不改写及无文件系统副作用。 |

### 版本、文档与混装验证补充

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `README.md` | 中文现行配置说明转 v3 目录/用途和统一入口，更新版本/API、自动迁移阻塞修复、独立升级及回滚说明。 |
| M | `README_EN.md` | 英文现行说明同步 v3、版本/API 和迁移恢复边界。 |
| M | `app/runtime/plugin_contract.py` | Host API 提升到 1.11，使依赖新配置能力的包可在安装前明确筛选。 |
| M | `examples/echo_feature/pyproject.toml` | 示例包 SDK 精确依赖更新为 2.4.0。 |
| M | `features/download/README.md` | 更新 2.4.0/API 1.11/SDK 2.4.0、目录语义与构建示例。 |
| M | `features/download/manifest.yaml` | Download 版本 2.4.0、Host API 1.11 门槛，保留原配置形状版本。 |
| M | `features/download/pyproject.toml` | 包版本 2.4.0 与随包 SDK 2.4.0 对齐。 |
| M | `features/search/README.md` | 更新 2.12.0/API 1.11、真实超时范围、公共分类/Token 入口、扁平 scoring 路径与构建示例。 |
| M | `features/search/manifest.yaml` | Search 版本 2.12.0、Host API 1.11 门槛，保留原配置形状版本。 |
| M | `features/search/pyproject.toml` | 包版本 2.12.0 与随包 SDK 2.4.0 对齐。 |
| M | `features/search/tests/test_config_schema_contract.py` | 当前版本一致性与配置形状版本保持的断言。 |
| M | `features/search/tests/test_feature_service.py` | 当前版本/API/SDK 及 README 构建示例一致性断言。 |
| M | `features/rename/README.md` | 更新 2.5.0/API 1.11、禁用额外根及公共字幕策略说明；阶段预算注明公共 caption_stage_timeout_seconds 与私有快照字段。 |
| M | `features/rename/manifest.yaml` | Rename 版本 2.5.0、Host API 1.11 门槛，保留原配置形状版本。 |
| M | `features/rename/pyproject.toml` | 包版本 2.5.0 与随包 SDK 2.4.0 对齐。 |
| M | `features/rename/tests/test_feature_processor.py` | 当前版本/API/SDK 及 README 构建示例一致性断言。 |
| M | `features/sync/README.md` | 更新 2.1.4/API 1.11/SDK、公共目录和 Token 入口，保留 Plex 业务说明。 |
| M | `features/sync/manifest.yaml` | Sync 版本 2.1.4、Host API 1.11 门槛，保留原配置形状版本。 |
| M | `features/sync/pyproject.toml` | 包版本 2.1.4 与随包 SDK 2.4.0 对齐。 |
| M | `features/sync/tests/test_feature_runtime.py` | 当前版本/API/SDK 及 README 构建示例一致性断言。 |
| M | `features/caption/README.md` | 更新 1.4.0/API 1.11/SDK、统一自动策略、GitHub 继承与独立导出公共用途。 |
| M | `features/caption/manifest.yaml` | Caption 版本 1.4.0、Host API 1.11 门槛，保留原配置形状版本。 |
| M | `features/caption/pyproject.toml` | 包版本 1.4.0 与随包 SDK 2.4.0 对齐。 |
| M | `sdk/pyproject.toml` | SDK 公共路径契约版本提升到 2.4.0。 |
| A | `tests/fixtures/config_contracts_host_3_10_1.json` | 冻结基线 Host 3.10.1 时五包原始 manifest/default/schema，用作真实旧契约混装测试输入。 |
| A | `tests/mixed_config_releases.py` | 用冻结旧契约或当前包契约构造本地惰性 TPX 测试包；不代表真实发布包安装已验证。 |
| M | `tests/test_bot_runtime_startup.py` | 当前 Host 显示版本更新断言。 |
| M | `tests/test_deployment_contract.py` | Host API/SDK 与 README 发布入口契约的当前版本断言。 |
| M | `tests/test_feature_builder.py` | 构建时在临时副本中操作 SDK/示例源码，排除已有生成物，避免测试刷新项目历史 egg-info；SDK 当前依赖断言更新。 |
| M | `tests/test_plugin_manager.py` | catalog update/install 检查携带当前 API 1.11 的断言。 |
| M | `tests/test_plugin_runtime_e2e.py` | 真实 wheel/子进程测试先复制 SDK 到临时目录，避免构建写回项目源码及旧生成物。 |
| M | `tests/test_release_catalog_generator.py` | 本地旧包与新 Search 混合 catalog 的真实声明/校验和、不同 Host API 可见更新集合回归。 |
| M | `tests/test_technical_identity_migration.py` | 当前五包/SDK 版本与 API 下限更新，技术身份及 main 来源断言保持。 |

Task 7 也在前表所列的启动、生成描述、Download 测试、中央生命周期、迁移 bootstrap 与投影测试中补入版本/混装验证，不重复计数。

| 状态 | 路径 | 目的 |
| --- | --- | --- |
| M | `features/caption/docs/catalog-usage.md` | 现行字幕来源说明对齐公共继承、显式匿名覆盖与目录角色，保留来源业务差异。 |
| M | `tests/test_feature_catalog_updater.py` | 独立 Search 发布保留旧兼容版本，并按 1.10/1.11 Host 选择旧/新 API floor。 |
| M | `tests/test_telepiplex_surface.py` | 主配置表面契约更新为 v3 directories/directory_roles/tmdb。 |
| A | `docs/audits/2026-11-16-config-semantics-consolidation.md` | 本次结果、全部文件用途、实施判定、实际验证与本地交付边界。 |
| A | `tests/test_config_semantic_boundaries.py` | 终审21项边界回归：v2/v3相对archive换包保护、全图校验/并发隔离、稀疏旧入口、无损导出与恢复默认、旧Download歧义、约束同源和别名诊断。 |

终审还修改前表中的 plugin_store、plugin_manager、config_projection、config_role_edits、central_config、directory_settings、config_semantics_migration 七份 Host 源文件，落实上述边界；路径只计一次。公共 GitHub 约束取当前所选包 schema（即使四源 override 均遮蔽），保留末尾换行拒绝语义；别名说明区分 applied/masked 且不含值。

## 最终本地验证

解释器与环境均为实际运行值。临时依赖为项目已声明的 Caption 测试依赖，未写入共享 Python。

```sh
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
DEP=/tmp/telepiplex-config-semantics-20261116/test-deps

# 项目根目录：完整 Host
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src:$DEP \
  "$PY" -m pytest -q -p no:cacheprovider tests -rs

# 各 features/<id> 目录：完整 download/search/rename/sync/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src:$DEP \
  "$PY" -m pytest -q -p no:cacheprovider tests

# 项目根目录：生成检查
PYTHONDONTWRITEBYTECODE=1 "$PY" scripts/generate_config_templates.py --check
```

| 范围 | 最终实际结果 | 原始日志 |
| --- | --- | --- |
| Host | 1022 passed，1 skipped，359 subtests，exit 0 | `final-root-v3-current.log` / `.exit` |
| Download | 273 passed，45 subtests，exit 0 | `task7-full-download.log` / `.exit` |
| Search | 3681 passed，2 skipped，232 subtests，exit 0 | `task7-full-search.log` / `.exit` |
| Rename | 525 passed，2 warnings，26 subtests，exit 0 | `task7-full-rename.log` / `.exit` |
| Sync | 162 passed，78 subtests，exit 0 | `task7-full-sync.log` / `.exit` |
| Caption | 515 passed，18 subtests，exit 0 | `task7-full-caption.log` / `.exit` |

合计 **6178 passed、3 skipped、758 subtests**。Host 未提供 TELEPIPLEX_OPERATION_ARTIFACTS，跳过可选已发布包矩阵；Search 两项线上供应方用例未启用联网/真实配置。Rename 两项既有 logger.warn 弃用警告位于 ai.py:246、processor.py:1083。日志均在 `/tmp/telepiplex-config-semantics-20261116/`。

第一次 Host 完整运行是 999 passed / 1 failed / 1 skipped，唯一失败为旧 v2 模板表面断言。更新 v3 断言并补独立 updater 用例后，完整 Host 先通过1001项，终审修正后的当前结果见上表及下段。Feature 完整通过后未重复全套，只另跑 Download 文档契约1项。历史 RED/GREEN 与限定复审见执行记录，没有把初次失败描述成成功。

准确的阶段次序：Task7 完整 Host 先通过1001项；终审新增19项边界后两次完整运行各为1019 passed/1 failed/1 skipped/359 subtests，分别失败于未改动的 Telegram 压测补偿计数（0≠1）和回调最大延迟120.837ms（要求小于80ms）。中间原样单跑压力文件18项/2 subtests通过。补自动字幕默认状态保护后，该轮完整 Host **1021 passed/1 skipped/359 subtests，exit0，219.47s**；两项先前失败本轮均通过。未改压力代码或阈值，历史失败原因仍未确认，不归因为已证明的调度问题。全部失败和复验日志保留。

随后主代理发现同一 archive guard 的 v3 范围遗漏：通过实际 profile 保存的相对路径仍可能随release改变。将现有guard扩到v3、仅按当前包默认合并本地archive配置，避免误用bundled公共token/TTL限制；新增真实provider/manager回归先RED，聚焦 **53 passed**。末次当前源码完整Host **1022 passed/1 skipped/359 subtests，exit0，193.92s**，原始 final-root-v3-current.log/.exit；生成器check exit0。上一轮1021全绿与全部失败记录均未覆盖或删除。

终审修正有13项初始 RED、129项联合 GREEN及后补边界 GREEN；残项以真实 Host reset 复现为1 RED，加入精确所有权检查后边界+投影42 GREEN。原始证据为 final-fix-report.md、final-fix-review.md、final-auto.diff 及对应 red/green 日志。Feature/SDK/schema 在最终波未改，因此五个完整 Feature 结果无需重复执行。

生成器已运行，三份输出 `--check` exit 0，双模板字节一致。描述与五包原始 manifest/default/schema 一致；历史五包夹具与初始 15 个契约文件逐一相等，34 个 egg-info SHA-256 与初始及 Task 7 起点相等。构建/E2E 测试改用临时源码副本；真实 wheel、venv 与子进程由 Host 测试覆盖。混装 TPX 使用真实契约和惰性 wheel/模拟 supervisor，不声称安装验证了线上发布包。

本地人工处理器检查：`task7-local-panel.py`，日志 `task7-local-panel.log`。真实 manager、随镜像描述及 handler，传输/认证为本地模拟。blocker 指向原 Caption 字段；预演未写文件；确认保存/重试卡片显示 v3 且无 blocker；摘要 mode 0600，不含测试秘密，最新 Token 保留。该检查覆盖实际交互与事务，不仅是 schema；未连接线上 Telegram、115 或 Unraid。

`test ! -e .git`、`test ! -e .worktrees`、`test -d .stfolder` 均 exit 0。未运行 Git、GitHub 发布或 Unraid 脚本，原计划、旧设计/审计、历史快照和已发布包不回写。

## 独立审阅与剩余边界

Task 1–7 均独立审阅通过。整体终审发现4项 Important、3项 Minor；一次集中修正后独立限定复审确认 I1/I2/I3 和三项 Minor 已解决，I4 原两个复现已解决但另留自动字幕 reset 状态残项。该残项生产增量仅增加一个原始字段比较；主代理另将I1同一guard范围补至v3，并核对两个小增量、真实重置/归档回归及当前完整测试，已落实既有无损/路径要求。没有将历史 not-clean 报告改写成另一份通过报告。

当前配置范围没有未关闭 Important。Task5 的5/60字面量已同源收敛，第三方 Download 窄歧义已定向保护，公共 GitHub 约束同源、被遮蔽别名安全说明均完成。Task7 复核的缺描述恢复、旧Host四源token/TTL正向导出证据保留。两项既有 Rename 警告及先前压力测试波动如实记录，不称为无警告、无波动或线上验证。

## Syncthing 交付

等待 Syncthing 显示 **Up to Date / 最新**。权威链路为 Mac `/Users/young/Documents/Telepiplex` → Unraid `/mnt/user/archives/Life Hacker/Telepiplex`（容器 `/Telepiplex`，Folder ID `telepiplex`）。本报告没有验证实时同步状态，也未代替用户发布。

用户在 Unraid 检查并从 `main` 发布；Host 与五个 Feature 仍独立版本/发布。旧 Host 安装前拒绝本轮 API 1.11 新包；新 Host 可恢复旧包并逐个更新。迁移冲突时从 `/config` 查看具体字段、修复/定向更新后重试，不需要手工重填整套配置。
