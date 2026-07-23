# Feature 安装与更新按钮设计

## 背景与根因

Telepiplex 已有 catalog 驱动的最新版选择和 Telegram callback，但旧部署的
`/config/config.yaml` 不会被新镜像覆盖。当前容器日志明确显示
`plugins.catalog: /config/plugins/catalog.yaml`；该本地文件不存在时，`/plugin` 只能
显示 `catalog_unavailable`，自然无法生成安装按钮。

GitHub `platform-v1.0.1` Release 的 `catalog.yaml` 当前可以通过
`releases/latest/download/catalog.yaml` 正常访问。本次首先需要补齐旧默认配置与远程
目录之间的运行时兼容，然后把安装和更新统一放进 `/plugin` 按钮界面。

## 已确认目标

- `download` 名称与内部 `plugin_id` 均保持不变。
- 发送 `/plugin` 后，未安装 Feature 直接显示“安装最新版”按钮。
- 已安装 Feature 有新版时，直接显示“更新到最新版”按钮。
- 按钮绑定 catalog 已选出的精确 `name@version`，点击就是显式授权点。
- 正常流程不要求用户发送 `/plugin install ...` 或 `/plugin update ...`。
- 精确版本和本地 `.tpx` 命令只保留为高级、离线和固定版本兜底。
- 已存在的本地 catalog 继续优先，不能破坏离线部署。

## 方案

### 方案 A：只让用户手工改 config

把旧本地路径改成 GitHub URL 可以立即恢复按钮，但每个旧部署仍会重复遇到问题，不能
解决升级兼容。

### 方案 B：兼容旧 catalog 并扩展 `/plugin` 概览（采用）

Telepiplex 将官方 Release catalog 作为缺省来源。若配置恰好指向
`<plugins.root>/catalog.yaml` 且文件不存在，则运行时回退官方 URL；文件存在时继续使用
本地目录，其他显式本地路径也保持原意。

`/plugin` 在同一页面查询未安装候选和已安装更新：可安装候选生成 install callback，
更新候选生成现有 update-confirm callback。版本选择仍全部由 catalog 完成，UI 不自行
比较版本。

### 方案 C：删除所有命令入口

纯按钮最简单，但会丢失离线 `.tpx`、固定版本和故障恢复能力。本次只把命令降级为高级
兜底，不从底层删除。

## 详细设计

### Catalog 来源兼容

Telepiplex 启动装配层集中解析来源：

1. 未配置 `plugins.catalog` 时使用官方
   `https://github.com/countott/telepiplex/releases/latest/download/catalog.yaml`。
2. HTTPS URL 原样使用。
3. 存在的本地文件原样使用。
4. 配置等于 `<plugins.root>/catalog.yaml` 且文件不存在时，视为旧默认值并回退官方
   URL。
5. 其他不存在的显式本地路径保持不变并报告 `catalog_unavailable`。

该兼容只影响运行时来源，不写回或覆盖 Feature 私有配置。

### `/plugin` 页面

页面顺序如下：

1. 已安装 Feature 与当前版本、状态。
2. 可更新 Feature：当前版本、目标版本和“更新”按钮。
3. 可安装 Feature：目标版本、依赖状态和“安装”按钮。
4. “配置 Feature”按钮（存在可配置的已安装 Feature 时）。
5. 高级/离线精确引用提示。

安装按钮 callback 保持
`host-plugin-install:confirm:<plugin_id>@<target_version>`；更新按钮保持
`host-plugin-update:confirm:<plugin_id>@<target_version>`。两者都只允许授权用户触发，
并继续复用 manager 的校验、安装、shadow startup、原子切换和回滚事务。

当没有已安装 Feature 时，不查询更新候选；当已有 Feature 时，更新和安装查询分别隔离
错误，某一项失败不抹掉另一项已得到的按钮。错误只显示稳定 code，不回显异常详情。

### 更新通知

周期更新通知继续保留，仍提供“确认更新”与“暂不更新”按钮。`/plugin` 页面新增的是
用户主动打开时的按需更新入口，两者使用同一个 update callback 和精确版本引用。

更新监控日志输出 `CatalogError.code`，不再只打印 `CatalogError` 类型名，也不记录原始
异常文本或敏感信息。

## 测试与验收

- 缺失的旧默认 catalog 回退官方 URL；存在的本地文件和其他自定义路径保持本地。
- `/plugin` 对未安装 `download` 显示安装按钮，callback 携带精确最新版引用。
- 已安装 `download@1.0.0` 且 catalog 有 `1.1.0` 时显示更新按钮，callback 携带
  `download@1.1.0`。
- 无更新时不显示更新按钮；依赖未满足时不显示安装按钮。
- 安装和更新 callback 继续验证用户权限并调用正确 manager 方法。
- catalog 查询部分失败时，成功取得的另一组操作仍可用。
- Python 3.12 下通过定向测试、全量 unittest、pytest、compileall、pip check 和
  `git diff --check`。

## 非目标

- 不修改 `download` 名称、manifest、artifact 或业务功能。
- 不自动安装或批量更新 Feature。
- 不改变现有 `.tpx` 校验与回滚事务。

## 发布补充：复用未变 Feature 的不可变产物

### 失败根因

`platform-v1.0.2` 的 Telepiplex 镜像和四个 Feature 构建成功，但 catalog 生成失败。对同一
`feature/115` commit 连续构建两次得到不同 `.tpx` SHA-256，证明 wheel/zip 构建时间戳
使产物不是字节级可复现。catalog 正确地拒绝了“同一 Feature version 对应不同 digest”。

### 采用方案

发布 job 继续构建每个 Feature，以验证当前分支可构建并读取当前 manifest。随后下载
上一成功 Release 的 catalog 和 `.tpx`：

1. 当前 artifact 的 `plugin_id`、`version` 和 `source.commit` 均与上一 catalog 相同时，
   校验上一 `.tpx` 的 digest、manifest identity、branch 和 commit，再用上一 Release 的
   原始字节替换本轮临时构建物。
2. version 相同但 `source.commit` 不同时不复用，后续 catalog 门禁继续以
   `version digest changed without version bump` 拒绝发布。
3. catalog 声称存在可复用版本但上一资产缺失、损坏或 digest 不符时立即失败。
4. 第一次发布或新 Feature version 没有上一条目时使用本轮新构建物。

这样不会放宽不可变版本规则，也不要求没有业务改动的 Feature 为每次 Telepiplex 发布虚增
版本；新 Release 可以再次附带与上一版完全相同、可校验的 Feature 资产。
