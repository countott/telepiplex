# Feature 配置向导与热重载修复设计

## 背景与目标

Platform v1.0.4 暴露了五类配置回归：Feature 默认模板没有在运行目录形成可跟随版本更新的示例文件；open115 更新成功后 `/config` 仍可能沿用旧运行态；Core 会静默跳过配置读取失败的 Feature；通用 schema 表单暴露了过多内部参数；`/reload` 只重读 Core YAML，没有重新加载 Feature。

本次修复保持 Feature 分支与私有配置完全独立。Core 不理解任何业务字段，只负责列出 Feature、委派配置命令、报告错误、验证热更新状态和协调重载。每个 Feature 自己实现独立 Telegram 配置向导。

## 方案选择

采用“显式公开配置 + Feature 独立向导 + 事务式热加载”方案。

- 不再由 Core 根据任意标量 schema 自动生成通用 `key=value` 表单。
- 可视化配置入口必须由 Feature schema 显式声明 `x-telepiplex-config-command`，且 manifest 必须注册对应命令。
- Core `/config` 委派到当前 router 中该 Feature 的活动进程。
- Feature 自己决定页面、步骤、校验、敏感字段处理和写入方式。
- YAML 中保留完整高级参数；Telegram 只展示约定的用户字段。

## Core 职责

### `/config` 发现与反馈

Core 枚举所有已安装 Feature，并为每项形成明确状态：

- `configurable`：schema 声明了有效自定义配置命令，活动 route 也注册了该命令；显示可点击按钮。
- `invalid_config`：活动配置无法解析或不符合 schema；保留在列表中并显示错误码，不再静默消失。
- `route_unavailable`：Feature 已安装但未形成活动 route；显示缺失能力或运行状态。
- `not_configurable`：Feature 没有声明配置向导；显示说明但不伪造入口。

任何读取异常只显示稳定错误码和 Feature ID，不回显配置内容、Token 或底层异常细节。

### Feature 更新后的运行态一致性

安装或更新只有在以下状态全部一致后才能返回成功：

1. `PluginStore.active()` 指向目标版本与来源提交。
2. `PluginSupervisor.process()` 运行目标 release。
3. `CapabilityRouter.plugin_route()` 持有目标 manifest 和新进程 client。
4. 新 schema 可从活动 release 读取。
5. Feature 健康检查通过稳定期。

成功后清理该 Feature 的业务会话、Core 配置会话和旧按钮索引。响应中附带 `配置 <Feature>` 按钮；用户不需要重启 Core 即可进入新版向导。一致性检查失败时，更新事务回滚到旧 release、旧 route 和旧进程，并返回失败，不能显示“安装成功”。

### `/reload`

`/reload` 执行分层重载并输出逐项结果：

1. 严格解析并校验 `/config/config.yaml`；解析失败时保留现有内存配置。
2. 立即应用可安全热更新的 Core 值：`log_level`、`allowed_user`、安装/启动/排空/稳定期/重启限制等管理参数。
3. 对每个已启用 Feature，从当前 `/config/plugins/<plugin_id>/config.yaml` 重新读取并校验配置，再以 shadow process 启动、切换 route、验证稳定性并停止旧进程。
4. 单个 Feature 失败时保留它的旧进程和 route，继续处理其他 Feature，并在最终摘要中单独报告。
5. `bot_token`、`plugins.root`、catalog 地址或刷新周期等需要重建连接/存储/监视器的字段若发生变化，只读取并报告“需重启容器”，不声称已热生效。

最终消息分别列出 Core 已应用项、Feature 重载成功项、失败项和需重启项。只有所有请求的热重载都成功时才显示整体成功。

## Feature 独立配置向导

每个向导都采用按钮选择区块、分步录入、确认后提交配置补丁的流程。取消、超时、输入不完整或写入失败均不修改配置。敏感值只显示“已配置/未配置”，不回显内容，不写入日志。

除 open115 的授权流程外，Feature 不直接写文件或替换自己的运行中服务对象。向导完成后返回仅针对自身配置的不透明嵌套补丁；Core 将补丁合并到该 Feature 当前配置，执行 schema 校验，再通过 `PluginManager.configure()` 原子写入并以 shadow process 重载。Core 只处理结构，不解释字段语义。这样运行中任务仍由旧进程安全完成或排空，新配置只在新进程验证健康后接管 route。

### open115

保留现有独立向导：

- Access Token → Refresh Token 两步录入。
- 115 扫码授权。
- 写入 `open115/config.yaml` 后立即更新客户端 Token。扫码授权需要在后台完成写回，因此继续使用 Feature 自身的私有原子写入器，不走通用补丁返回。

### media-search

向导包含三个区块：

- Prowlarr：地址、API Key。Prowlarr 始终启用，不显示启用开关。
- TVDB：启用、API Key、Subscriber PIN。
- AI：启用、API 地址、API Key、模型。

Wikipedia、timeout、status timeout、Indexer IDs、分类编号、结果数量、分类目录和评分参数只保留在 YAML。

### plex-management

向导包含四个区块：

- Plex：地址、Token。
- TMDB：API Key。
- Fanart.tv：API Key。
- AI：启用、API 地址、API Key、模型。

扫描轮询、扫描超时、普通 timeout、AI tool rounds、MCP 全部只保留在 YAML。

### renaming

向导包含两个区块：

- TVDB：启用、API Key、Subscriber PIN。
- AI：启用、API 地址、API Key、模型。

未整理目录、存储/元数据超时、文件选择阈值等只保留在 YAML。

重复出现的 TVDB 或 AI 配置分别写入对应 Feature 的私有 YAML。Core 不建立共享密钥文件，避免任一 Feature 依赖另一个 Feature 或 Core 的业务配置。

## 模板契约

每个 `.tpx` 中的 `config.default.yaml` 是该 Feature 当前版本的完整模板。安装或更新 release 时，Core 总是把它复制为：

`/config/plugins/<plugin_id>/config.yaml.example`

规则如下：

- `config.yaml.example` 随 release 更新并允许覆盖。
- 已存在的 `config.yaml` 永不因模板更新被覆盖。
- 首次安装时，`config.yaml` 仍从当前默认模板创建，权限保持私有。
- Core 的 `app/config.yaml.example` 与 `config/config.yaml.example` 继续保持字节一致，只描述 Core 配置，并明确列出 Feature 模板路径。

## 版本与分支

- `feature/telepiplex-core`：发现反馈、更新一致性、模板落地、`/reload` 协调。
- `feature/media-search`：独立向导与补丁版本升级。
- `feature/plex-management`：独立向导与补丁版本升级。
- `feature/renaming`：独立向导与补丁版本升级。
- `feature/115`：复用现有 1.0.1 向导；只有确需修正 Feature 自身代码时才再次升补丁版本。

各分支单独提交和验证，不合并到 `main`，不推送远端，除非用户另行要求发布。

## 错误处理与安全

- Core 与 Feature 错误均使用稳定错误码，敏感值统一清洗。
- 更新和重载采用新进程验证通过后再切 route 的事务顺序。
- Core 的配置补丁写入以及 open115 的授权写回均采用临时文件、`fsync` 和原子替换，配置权限为 `0600`。
- 热重载失败不会停止仍可工作的旧进程。
- 配置发现失败不会改变 Feature 的启用状态。

## 验证标准

自动化测试必须覆盖：

1. open115 从旧版更新到新版后，不重启 Core 即可在 `/config` 中进入 Token/扫码向导。
2. 更新成功返回前，store、supervisor、router、manifest 和 schema 处于同一 release。
3. 非法 media-search 配置仍出现在列表并显示 `invalid_config`。
4. 四个 Feature 的向导只显示本设计列出的字段，且非 open115 向导只返回自身的不透明补丁，由 Core 事务式应用。
5. 向导取消、超时、部分输入和写入失败不改配置且不泄露敏感值。
6. 手工修改有效 Feature YAML 后执行 `/reload`，新进程读取新值；失败 Feature 保留旧 route，并在摘要中报告。
7. Core YAML 解析失败不清空当前运行配置。
8. Feature 更新会刷新 `config.yaml.example`，同时保留原 `config.yaml`。
9. 两份 Core 模板字节一致且 YAML 可解析。
10. 各分支完整测试、Python 编译、schema/default 校验和 whitespace 检查通过。
