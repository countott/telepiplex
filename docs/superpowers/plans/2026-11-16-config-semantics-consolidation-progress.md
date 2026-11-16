# SDD ledger — plan: docs/superpowers/plans/2026-11-16-config-semantics-consolidation.md

用户于 2026-11-16 指示“按顺序推进”，授权执行全部七项任务。本记录跟踪实现、判断与实际验证；原计划及历史设计保留不改。

## 工作边界

只在 Mac 主工作区本地修改、测试；禁止 Git、worktree 和发布。保留其他 chat 的修改、`.stfolder`。采用源文件快照与 Python unified diff 审阅，不调用依赖 Git 的技能脚本。报告和测试日志位于 `/tmp/telepiplex-config-semantics-20261116/`。

## 预检

| 任务/接口 | 检查与决定 |
| --- | --- |
| Task 1 | raw 别名必须先于默认合并；新超时范围在 Task 4 才启用。 |
| Task 2 | 规范路径与旧 Download 相对快照并存；不改变存储域或落盘。 |
| Task 3 | 主 v3、引用、迁移预检、冷启动恢复需同时具备。 |
| Task 4 | Caption 自动开关合并保持 AND；Search 新范围需包能力声明。 |
| Task 5 | 唯一编辑流程，旧 callback 保持原作用域与权限。 |
| Task 6 | 模板由默认/schema 生成，不增加手工参数事实副本。 |
| Task 7 | 最后调整版本，复核混装与全套本地测试。 |
| 1 → 3/4/6 | normalize_explicit_aliases 为纯 raw 转换；投影、迁移和生成器共用，不能各复制一套。 |
| 2 → 3/5/6 | normalize_115_directory 仅解析 115 路径；实体、UI、生成器使用同一规则。 |
| 3 → 4/5/6/7 | config_projection/config_semantics_migration 是共享接口；后续任务顺序修改，不并行写这些文件。 |
| 4 → 5/7 | 能力声明和旧开关投影由 Host 检查；UI 与激活采取同一约束。 |
| 5 → 6 | 安装前字段描述由 Task 6 生成；Task 5 先建立读取契约，Task 6 接入生成物后统一验收。 |
| 6 → 7 | 模板、设置描述及包版本信息需最后重新生成，再完整验证。 |

Ruling: 用户的 Mac 禁 Git 约束覆盖技能的 Git/worktree/commit 要求；使用本地文件快照和逐任务审阅，保留可核对的测试日志。

Ruling: Task 1 的 endpoint_intervals 是整体 canonical 字段；只要显式存在（包括空 map），旧 request_interval 不补入其中。只有新字段缺失时才按旧 client 的五类别 fallback 迁移。显式非法值仍交由 schema 拒绝，不因别名转换放宽范围。

Ruling: Task 3 的 MigrationContext 既接受已安装 release 的历史契约，也接受 Host 随镜像携带的内置契约，以支持未安装项。最终来源为 Task 6 的同源生成描述，不在运行容器读取源码 features 目录；描述缺失时不得猜测并自动迁移，可保留 v2 并提供诊断。

Ruling: 共享配置导航不存在现成的插件响应动作。Task 5 采用 Host 校验的有限导航目标并生成按钮，不开放任意 host callback 命名空间或通过导航写配置。

Ruling: Task 3 一次定义完整 v3 主结构（目录引用、Search 扁平层级、旧自动字幕 AND 及来源默认迁移）；Task 4/6 消费该结构，分别完成包行为/界面与模板生成，不产生需要再次迁移的“半成品 v3”。缺构建契约时保留 v2 的安全门槛直到 Task 6 补齐。

Ruling: Caption 的 GitHub 公共默认只由 Host 在 raw 显式键判断之后、包默认合成之前展开；插件继续消费原 provider 快照，不新增第二套公共继承实现。包内旧 default/schema 的协议字段保留，主模板生成时去掉重复继承项；显式匿名 override 继续保留。

Ruling: 历史 Caption validate_config 的路径标准化返回值未写回配置，不能据此推断 raw YAML 被 trim。真实 `_place → Rename.name_subtitle → Download.create_directory` 探针确认 `/after ` 保留到 HTTP file_name；迁移及降级按实际链保留，旧 UI 的预先 trim 单独对待。

Ruling: 旧 Rename 的 `/` 在自身 inventory 表示无额外根，但 Caption scan_library 仍把它列为全盘根。Task 4 用共享根目录枚举修正两处一致性，并声明 `x-telepiplex-inventory-root-contract: null_disables_extra_root_v1`。旧 Rename 只在涉及 null 根的迁移/激活受定向兼容门槛保护，v2 管理与升级入口保留。

Ruling: 旧 Download 启动会 trim 手动目录外围空格，新实现保留，schema 形状相同不足以判别。新增 `x-telepiplex-download-directory-contract: preserve_segment_spaces_v1`；仅解释会改变的目录值需要新能力，不强迫五包一起升级。

Ruling: 媒体分类及额外盘点的末尾空白存在 Caption 菜单/旧清理保护解释分歧，本轮迁移及 v3 新保存/激活保守报字段，不 trim 或移动目录；明确为消费链限制。开头段空格和内部空格按证据保留，独立字幕导出保留已验证的末尾空格行为。不扩展第三组能力声明或广泛文件执行器修改。

Ruling: 稳定 ID 分配顺序与菜单展示顺序分离。新增可选 `shared.directory_roles.media_category_order`（四 kind 的无重复完整列表，缺省为既有标准顺序）；迁移保留旧合法分类列表的展示顺序，投影与降级保留，实体分配仍按固定 kind 顺序。

Ruling: Task 6 默认来源收敛时，用独立回归修复合法 `restart_limit: 0`、`stabilize_seconds: 0` 冷启动被 `or default` 忽略的问题，使之与现有热重载一致；不对其他非法零值/空值做无依据的全量行为更改。模板示例 allowed_user 不成为授权默认；独立 Dispatcher 的 30 秒与 Host 配置的 1800 秒继续区分。

Ruling: 多个已明确提供的旧字段同时无效时，Host 编辑器收集一份有限允许字段的批量修复草稿，验证完整候选后原子提交；不绕过包校验启动坏配置，不创建第二份用户配置。分页覆盖目录选择、当前值和完整影响列表。

## 进度

- Task 1: complete (review clean)
- Task 2: complete (fix round 1 reviewed clean)
- Task 3: complete (fix round 2 reviewed clean)
- Task 4: complete (review clean)
- Task 5: complete (fix round 2 reviewed clean)
- Task 6: complete (fix round 1 reviewed clean)
- Task 7: complete (review clean)

## 验证与审阅

### Task 1

- 变更：新增 raw 别名规范化，在默认合成前调用；统一 AI URL 优先级，保留空 canonical；Download 显式零值；Caption 缺省来源预算 45。Search 旧截断仅固定回归，不改变。
- RED：Host 新回归 9 failed/1 passed；Download 轮询 2 ≠ 10；Rename 别名/空值 2 failed；Caption 实际等待 30 ≠ 45。
- GREEN：Host 876 passed/1 skipped/359 subtests；Download 277 passed/33 subtests；Rename 521 passed/26 subtests；Caption 507 passed；Search 3617 passed/2 skipped/246 subtests。
- 命令：既有解释器，`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src:<test-deps>`（Feature 为 `src:../../sdk/src:<test-deps>`），`python3 -m pytest -q -p no:cacheprovider tests`；各完整日志和 task-1-report.md 保存在本任务临时目录。
- 环境：原 Caption 测试缺已声明 OpenCC 依赖，临时目录补齐真实依赖后通过；未改变共享 Python 或项目依赖。Rename 两个既有 logger.warn 弃用警告保留记录。
- 独立审阅：Spec compliant；quality approved；无待修问题。见临时目录 task-1-review.md。

### Task 2

- 初次实现：共享 SDK 115 路径纯解析、Download 旧快照适配、Caption/公共编辑器输入与显示接入。
- RED/GREEN：Host 针对性最终 73 passed/2 subtests；Download 最终完整 277 passed/34 subtests；Caption 完整 509 passed/8 subtests。
- 审阅发现：目录输入的保留命令正则大小写敏感，与 Telegram CommandHandler 的大小写处理不同，`/START`、`/ReLoAd`、`/Q` 会被当作路径。进入 fix round 1，增加混合大小写回归并修复过滤器；路径本身大小写仍保留。
- Task 2: fix round 1/5 (1 addressed, 0 open)：命令过滤采用 re.IGNORECASE；新增混合大小写回归先 1 failed，修复后三个针对性文件 45 passed/2 subtests。独立 scoped review 为 ADDRESSED，无新问题。

### Task 3

- 初次实现后完整 Host 927 passed/1 skipped/359 subtests；最后消费链限制和 label 继承修正后针对性集合 77 passed。Caption 实际交付/no-follow 子集 66 passed/8 subtests。最终全量仍在 Task 7 运行。
- 独立审阅：6 Important 待修，进入 fix round 1：旧 source admission 阻断兼容 target 更新；v2 类别旧列表覆盖与 v3 角色依赖 CAS；Caption 公共 defaults 校验；分类菜单顺序；保留 v2 时共享值单消费者重载；TMDB null 阻断管理面。报告在 task-3-review.md。
- Task 3: minor (deferred)：第三方宽松 schema + 历史非幂等 Download 路径规范化的双处理边界；仓内实际旧包 schema 已拒绝所举输入。最终审阅需重新分级。
- Fix round 1 进展：六项均已先复现 RED（17 个失败用例）；修正后投影/迁移/冷启动/生命周期联合集合 94 passed。完整 Host 验证进行中，尚未完成 scoped re-review。
- Task 3: fix round 1/5 (5 addressed, 1 open, 1 new Important)：最终完整 Host 949 passed/1 skipped/359 subtests；最终针对性 103 passed/2 subtests。复审确认兼容升级、公共默认校验、菜单顺序、共享重载、空凭据恢复已解决；继承名称与路径同时改的并发保护仍漏一条旧实体依赖，新增“只有分类顺序、采用默认目录”的合法稀疏配置编辑 KeyError。进入 round 2，限定修复这两项。
- Task 3: fix round 2/5 (2 addressed, 0 open)：两项修复共 3 RED → 3 GREEN；最终针对性 106 passed/2 subtests；独立 scoped review 两项均 ADDRESSED，无新增阻断。全量 Host 最近一次为 round1 的 949 passed，最终 Task7 将对全部交付代码重跑。

### Task 4

- 新 Search HTTP 超时严格支持 1..300 秒；非法参数在请求前安全拒绝，旧行为回归使用 Host 旧契约快照。新增三项 schema 行为声明，Rename 两处目录枚举共用 helper。
- Search/Sync 的共享 TMDB 输入及旧会话写入被收敛为公共导航；Caption 自动策略导航由 Host 后续接入。GitHub 公共默认只在 Host 展开，并验证实际请求头、匿名 override 和缓存 TTL。
- RED 包含超时 39 failed、导航 5 failed、Sync 3 failed、自动策略 1 failed、真实根目录 1 failed、超大整数 3 failed。
- 最终实际验证：Search 3682 passed/2 skipped/245 subtests；Caption 516 passed/18 subtests；Rename 525 passed/26 subtests（2 个既有弃用警告）；Sync 163 passed/79 subtests；Host 针对性 36 passed/9 subtests。后补手动总 gate 和实际调用抑制用例分别 1、24 passed。
- 独立审阅：Spec compliant，quality approved，无待修问题。记录见 task-4-report.md / task-4-review.md。

### Task 5

- 初次实现完成，进入独立审阅。新增有限 Host 字段编辑器、目录资源/用途编辑、保存/继承重置/删除事务、迁移预演与修复入口；五插件基础入口统一导航，保留业务专用交互和 115 授权。
- 已用真实 PTB 会话处理器验证从插件页到 Host 输入、确认仅写目标字段，以及真实协调器关闭原生操作后新建配置卡片；覆盖 nonce、缺插件/停用保存、候选失败保留、Token 并发、同插件新增引用和来源匿名/继承。
- 最终针对性：Host 352 passed/103 subtests；Search 21 passed/38 subtests；Sync 9 passed；Rename 6 passed；Caption 42 passed/18 subtests；Download 81 passed/30 subtests。完整套件在 Task7 最终运行。
- Task6 描述文件接口已固定：features 各行保留原包 defaults/schema/version/config_schema_version；host.defaults/schema 按主文档形状提供，当前运行环境编辑只需 plugins.root/runtime_root；共享 GitHub 元数据取 Caption 原 schema，无需新建另一套共享 schema 表。
- 独立审阅发现 5 Important，进入 fix round1：动态 Feature 命令误当字段；未安装目录配置未过实际包 schema；多个坏字段不能同时修复；省略 operation 的过期导航可替换当前草稿；目录/当前值/影响列表可能超过 Telegram 上限。
- Task 5: minor (deferred)：剩余两处历史 5/60 字面量与 timeout_bounds 事实相同但来源未统一；最终审阅需分级，Task6 默认收敛时若自然覆盖可消除。
- Fix round1 实现与覆盖完成，等待限定复审：动态命令切换、真实包路径语法及未安装校验、有限批量修复、迟到导航代际校验、目录内容/选项分页。Host 365 passed/103 subtests；Download 全量 273 passed/45 subtests；Search 21/38、Sync 9、Rename 6、Caption 42/18。真实候选启动失败保持原 v2 字节与运行进程，重试成功；分页完整覆盖 80 个长 ID、4500 emoji 路径及七处引用。
- Task 5: fix round 1/5 (5 addressed, 0 original open, 2 new Important)：原五项均 ADDRESSED；新命令切换分支跨 chat 清除同一用户的 Host 草稿，以及普通业务命令解除旧 Sync 草稿保护，迟到的旧代际 config_patch 可覆盖当前凭据。进入 round2，仅修这两项及实际分派回归；Task6 仍未开始实现。
- Task 5: fix round 2/5 (2 addressed, 0 open)：普通命令不解除配置退役保护；配置响应在副作用前核对 owner/代际，Host 草稿按 chat/user 保存。4 RED → 4 GREEN；八个 Host 模块 278 passed/72 subtests。独立限定复审均 ADDRESSED，无新增 Critical/Important，Task5 完成。

### Task 6

- Task5 复审通过后开始实现；准备阶段仅只读核对。默认模块、三份生成输出和运行时消费点按同一来源收敛，不提前调整版本。
- 首轮实现完成，进入独立审阅：新增默认模块、生成器与描述 JSON，重新生成双 v3 模板；Host/runtime 当前默认单源、历史 v2 独立冻结；Search raw patch 空评分表与公共错误路径已修复。目录当前默认归 Host，共享路径与 Rename/Caption 映射包默认冲突时生成器明确报所属字段，防止静默漂移；缺角色仍采用所选 release 默认。
- 实际验证：Host 针对性 203 passed/5 subtests；Search 16 passed/38 subtests；最后生成路径校验/缺描述回归后模板+投影 28 passed/5 subtests。生成器、`--check`、双模板字节一致与本地目录边界均 exit0。完整日志及 RED/GREEN 见 task-6-report.md。
- Task5 deferred minor 5/60：本轮已收敛到 timeout_bounds，等待 Task6 审阅确认。
- 独立审阅发现 1 Important：生成器删除非空 Download 默认菜单而未映射或拒绝，会静默丢弃未来合法默认；当前空菜单示例有效。进入 fix round1，保留名称/路径/顺序，或对 Host 所有的角色默认差异明确报所属字段。

Ruling: 当前共享目录及用途默认归 Host；旧包内映射字段作为进程契约保留。工厂生成遇到 Download、Rename、Caption 对应默认与公共默认不一致时必须明确拒绝或无损映射，不能静默丢弃；因此未来更改包映射默认时需同步协调公共默认或映射，而不是只刷新生成物。

- Task 6: fix round 1/5 (1 addressed, 0 open)：有序完整比较 Download 包默认菜单与公共绑定，差异报两个所属字段，名称/路径/顺序保持；1 RED → 29 GREEN/5 subtests，`--check` exit0，输出无需改写。独立限定复审 ADDRESSED，无新问题，Task6 完成。旧5/60 deferred minor 已确认解决。

### Task 7

- Task6 复审通过后开始：重新核对源版本，提升实际能力边界和各包版本，补真实旧契约混装/恢复/降级及 catalog 回归，最后重生成并完整本地验证。
- 源版本：Host3.11.0/API1.11/SDK2.4.0；Download2.4.0、Search2.12.0、Rename2.5.0、Sync2.1.4、Caption1.4.0。五个新包最低 API1.11，旧包兼容及 Download2.3 授权门槛保留，各包配置/状态 schema identity 不变。
- 真实基线五包契约混装、冷恢复、逐包升级、Download 回滚保留最新双 Token、旧 Host API 安装/更新拒绝、目录源隔离后升级恢复、有损导出和两类 catalog 选择均覆盖。构建/E2E 改用临时副本，34个 egg-info 与原始快照完全一致。
- 最终全量：Host1001 passed/1 skipped/359 subtests；Download273/45；Search3681/2 skipped/232；Rename525/2既有warning/26；Sync162/78；Caption515/18。六套 exit0；生成器check exit0。首轮Host唯一失败为旧v2表面断言，修正测试并加入独立updater后重跑整套通过；五Feature不重复全套。
- 独立审阅：Spec compliant、quality approved，无Critical/Important。另独立确认缺真实描述时保留v2五个blockers，恢复描述后可重试；旧Host导出保留四源有效token/TTL及显式匿名。
- Task7 minor (deferred)：Rename ai.py:246、processor.py:1083 两个既有 logger.warn 弃用警告，终审分级；全量日志如实保留。

## 整体终审

- 七项任务均已完成逐任务门槛；新增交付审计列出110个路径与全部14项判定及代价。整体终审准备读取原始baseline→最终源差异；未发布、未执行Git或Unraid操作。
- 整体终审 NEEDS FIXES：0 Critical、4 Important。保留 v2 时 Caption 换 release 改变相对 archive 根/映射含义；目录图重复规范路径/无效未绑定实体/遗留 shared.category_folder 被漏检；稀疏 v3 三个旧用途补丁 KeyError；旧 Host 导出丢未绑定目录和继承/匿名意图。一次集中修复波处理，不逐项派重复执行者。
- 终审 3 Minor 一并纳入该波：第三方宽松旧 Download 双规范化歧义、Caption 公共 token/TTL 约束重复手写、被 canonical 遮蔽的旧别名缺安全说明。两个既有 logger.warn 警告继续如实保留，不扩展历史清理。

Ruling: v2 因其他项受阻时，Caption release 切换仍必须保留旧有效 archive 路径；来源上下文可确认时安全固定为旧绝对位置，否则在切换前按字段拒绝并提示修复。不可用新 release 的 CWD 重新解释，也不要求先迁完整 v3。

Ruling: 旧 Host 候选须保留当前用户可见目录资源及来源继承/显式匿名意图；旧格式不能可逆表达时按字段拒绝导出，不以当前快照相同替代无损，不恢复旧备份。

Ruling: 对仓内旧包 schema 不允许、仅第三方宽松旧契约可触达的 Download 双规范化歧义，允许定向拒绝其旧运行目标，保留无关服务和兼容新目标恢复路径；不静默 trim 到另一目录，不扩大到所有旧包。

- 唯一限定复审：I1/I2/I3 及三项 Minor 均 ADDRESSED；I4 原两个复现已解决，但 UI 恢复默认后的 caption.automatic 缺失仍被导出/重迁变为显式 true。按已有“无损否则拒绝”判定落实最小残项修正，不作为延期项遗留用户意图损失；由实施者补真实 reset 回归，主代理核对该小增量，不重启广泛审阅。
- 最终波覆盖：13 RED、129 联合 GREEN、最终边界19 GREEN，模板check与34份egg-info均通过。两次完整 Host 各 1019 passed/1 failed/1 skipped/359 subtests，分别是未修改的 Telegram 压测补偿计数及120.837ms超80ms；单独整份压力文件18 passed/2 subtests。原因未证实，不称环境问题或全套绿色；保留全部日志。
- 收尾残项修正后再跑一次源码冻结的完整 Host，期间不并行执行本地性能探针，保持原测试及阈值；不修改另一个chat负责的消息/压力代码。
- 残项增量主代理核对：生产仅在旧Host导出 raw所有权比较中加入 features.caption.automatic，新增真实 Host reset 回归。显式 False/True 导出重迁均正常，恢复默认状态按精确字段拒绝且主YAML不变；1 RED → 42 GREEN，生成器check exit0。未增加版本、未改 Feature/SDK/压力代码。
- automatic 残项修正后完整 Host：**1021 passed/1 skipped/359 subtests，exit0，219.47s**（final-root-current.log/.exit）；前两项压力失败该轮均通过，历史原因仍未确认，失败记录保留。该阶段五Feature此前完整结果保持有效，另有2个既有Rename弃用警告。
- 终审 I1/I2/I3 及 M1/M2/M3 的修复已独立复审通过；I4 原两个问题及 reset 残项均已修正并验证。当前配置范围无未关闭 Important。最后残项由主代理核对小增量及原始测试证据，没有冒称历史“not clean”复审已经变成另一份通过报告。
- Caption archive 本轮采用保守拒绝分支：相对或 HOME-dependent 值在 release 切换前按字段拒绝，保持原实例/配置；显式绝对路径修复后可更新或回滚，不自动暗写原 v2、不要求先迁完整 v3。
- 原收尾对账确认111个路径、17项判定；其后逐行核对发现 archive release guard 仅检查v2，而v3合法profile仍可保存relative。相同保护扩至v3并使用所选包默认合并local_archive，不用bundled公共约束误校验独立包。
- 末次小增量主代理核对：真实 v3 manager.configure_patch、真实 archive roots/TSV 源读取、更新拒绝保留进程及YAML、绝对修复后更新成功；1 RED → 53 GREEN，check exit0。只修改同一guard与现有边界文件；未改Feature、SDK、版本或压力代码。
- **最终当前源码完整 Host：1022 passed/1 skipped/359 subtests，exit0，193.92s**（final-root-v3-current.log/.exit）。五Feature完整结果不变，最终总计 **6178 passed/3 skipped/758 subtests**，另2个既有Rename警告。所有失败与前阶段全绿日志保留，未声称压力波动原因已证实。
- 本地交付完成，保留工作区及全部源/审阅证据；111个路径、17项判定及实际结果在交付审计逐项对账。不发布、不执行Git，等待Syncthing最新后由用户在Unraid检查发布。
