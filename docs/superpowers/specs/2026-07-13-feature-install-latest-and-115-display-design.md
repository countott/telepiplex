# Feature 最新版安装与 115 展示名设计

## 背景与根因

Core 已具备 catalog 驱动的最新版选择和 Telegram 安装按钮，但已存在的
`/config/config.yaml` 不会被新镜像覆盖。旧配置仍可能写着
`plugins.catalog: /config/plugins/catalog.yaml`；当该文件不存在时，Core 只能返回
`catalog_unavailable`，于是 `/plugin` 无法生成安装按钮，手工输入不带版本的
`/plugin install open115` 也会被现有 `name@version` 校验拒绝。

GitHub Release 的 `platform-v1.0.1/catalog.yaml` 与 `releases/latest` 地址当前均可访问，
所以本次故障不是 Release 资产缺失，而是旧的本地 catalog 默认值与新的远程发布方式
之间缺少运行时兼容。

## 目标

- 首次安装或从旧配置升级后，发送 `/plugin` 能看到当前可安装 Feature 的按钮。
- 按钮始终绑定 catalog 中最新的稳定、Core API 兼容版本。
- `/plugin install <name>` 也能解析同一最新版，作为按钮之外的可用入口。
- 用户界面将内部插件标识 `open115` 显示为 `115`。
- 不改动既有 `.tpx` manifest、catalog key、安装目录和 capability 依赖中的内部
  `open115` 标识。
- 显式配置且存在的本地 catalog 仍可用于离线或固定版本部署。

## 方案比较

### 方案 A：只要求用户修改现有 config

将 `/config/plugins/catalog.yaml` 手工改成 GitHub URL 即可恢复按钮，但每个旧部署都会
重复遇到同一问题，不能满足首次安装体验。

### 方案 B：兼容旧默认值并增强安装入口（采用）

Core 将官方 Release catalog 作为缺省来源。如果配置指向插件根目录下的旧默认
`catalog.yaml` 且文件不存在，则自动使用官方远程 catalog；若该文件存在，仍优先使用
本地文件。其他显式本地路径保持原意，不做静默替换。

Telegram 层继续使用 catalog 候选中的精确 `name@version` 作为回调和最终安装引用；
界面只做展示名映射。裸名称安装先读取 `available_plugins()`，再选择同一个最新兼容
候选。因此按钮、裸名称和精确版本不会形成三套版本选择规则。

### 方案 C：把 manifest plugin_id 物理改成 `115`

这会改变标识格式、artifact、持久化目录、依赖关系、回滚记录和已安装状态，需要跨
Core 与 `feature/115` 迁移。收益只是内部名称变化，不适合作为安装体验修复。

## 详细设计

### Catalog 来源兼容

在 Core 启动装配层集中解析 catalog 来源：

1. `plugins.catalog` 为 HTTPS URL 时原样使用。
2. `plugins.catalog` 为存在的本地文件时原样使用。
3. 未配置 `plugins.catalog` 时使用官方 `releases/latest/download/catalog.yaml`。
4. 配置值等于 `<plugins.root>/catalog.yaml` 且该文件不存在时，视为旧默认值并回退
   官方 URL。
5. 其他不存在的显式本地路径不回退，继续返回 `catalog_unavailable`，避免绕过用户的
   离线或固定版本意图。

默认回退不写回私有配置，只影响本次运行时来源。

### 最新版按钮和裸名称安装

`/plugin` 继续调用 `manager.available_plugins()`。该调用刷新远程目录，并由 catalog
选择最新的非预发布、Core API 兼容版本。只有依赖满足的候选显示安装按钮。

`/plugin install <name>` 新增如下解析：

- `115` 先映射为内部 ID `open115`。
- 其他合法裸名称直接作为内部 ID。
- 从 `available_plugins()` 中查找候选并使用其精确 `reference`。
- 依赖未满足时返回明确的依赖或 capability 错误，不调用安装事务。
- `name@version` 和本地 `.tpx` 路径仍直接交给 manager，保持固定版本与离线入口。

### 115 展示名

Core 提供一个无状态展示名函数：`open115 -> 115`，其他 ID 原样返回。它用于：

- `/plugin` 已安装、可安装、依赖说明、操作结果与状态文本；
- `/config` Feature 列表、区块提示和写入结果；
- 更新通知文本。

按钮 callback、配置会话数据、manager 调用和 catalog reference 始终保存内部 ID，避免
展示改名影响业务协议。

### 错误与日志

- 默认远程目录也不可访问且无有效缓存时，继续显示 `catalog_unavailable` 或具体
  `CatalogError.code`，Core 和其他 Feature 不退出。
- 更新监控日志记录安全的错误 code，而不仅是 `CatalogError` 类型名；不记录 URL
  查询参数、token 或异常详情。
- 安装按钮仍是一次显式人工确认点，不自动批量安装。

## 测试与验收

- 缺失的旧默认 `<root>/catalog.yaml` 会解析为官方 HTTPS catalog。
- 已存在的旧默认本地文件与其他显式本地路径均保持本地来源。
- `/plugin` 将 `open115@最新版本` 显示为 `115`，回调仍携带
  `open115@<version>`。
- `/plugin install 115` 与 `/plugin install open115` 都调用最新候选的精确引用。
- 依赖未满足的裸名称不会进入安装事务。
- `/config` 对用户显示 `115`，内部仍用 `open115` 读取和写入配置。
- Python 3.12 下运行定向测试、全量 unittest、pytest、compileall、pip check 和
  `git diff --check`。

## 非目标

- 不修改 `feature/115` 的下载、授权或存储业务。
- 不改变 `.tpx` manifest schema、artifact 文件名或 capability 名称。
- 不自动安装全部 Feature，也不绕过安装按钮的用户确认。
