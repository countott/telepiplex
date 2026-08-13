# Feature 安装依赖与配置迁移诊断设计

## 目标

修复 `download` Feature 发布产物中插件依赖声明与内置 SDK wheel 不一致导致的离线安装失败，并让 Host 在配置自动迁移失败时安全地指出不兼容字段路径。

## 已确认根因

`download-1.0.7.tpx` 的 `plugin.whl` 声明依赖 `telepiplex-plugin-sdk==1.1.0`，但该产物的 `wheelhouse/` 只包含 `telepiplex-plugin-sdk 1.2.1`。Host 使用 `pip --no-index --find-links` 离线安装，因此不存在可满足的 SDK 版本，安装必然失败。

正式发布的 `rename 1.1.0` 配置与 `rename 1.2.2` 默认配置可通过 Host 当前的“只补缺失键”策略迁移：新增完整的四项 `category_folder`，保留其余用户值，并能通过新 schema。现有笼统的 `config_migration_required` 无法指出真实 Unraid 配置中可能存在的额外字段、类型错误或缺失字段。

## 设计

### 构建时依赖闭包校验

Feature 构建器在创建 `.tpx` 前读取 `plugin.whl` 的 `Requires-Dist`，并读取 `wheelhouse/*.whl` 的 distribution name 与 version。每个在当前环境生效的直接依赖都必须在 wheelhouse 中至少有一个满足其版本约束的 wheel，否则构建以 `FeatureBuildError` 失败。

这项校验覆盖 SDK 和第三方依赖，直接阻止“插件要求 1.1.0、产物只带 1.2.1”一类不可安装产物进入发布流程。

### download 发布身份

`download` 升级到 `1.0.8`，`pyproject.toml` 的 SDK 依赖改为 `telepiplex-plugin-sdk==1.2.1`。manifest、包元数据、README 构建示例和版本契约测试保持同步。

### 安全的配置错误路径

Host 用 JSON Schema 校验配置时收集最多 20 个失败字段路径。路径来自 schema 的实例位置、`required` 缺失键和 `additionalProperties` 额外键；只包含字段名，不包含配置值或原始校验错误文本。

`StoreError` 和 `PluginOperationError` 通过结构化 `details.config_error_paths` 传递这些路径。自动迁移继续 fail closed：不删除未知字段、不改写类型不兼容值、不重置用户配置。Telegram 的安装/更新错误在稳定错误码后追加“请检查配置项：<路径>”。

## 测试

- 构建器测试：插件要求 SDK 1.1.0、wheelhouse 只有 1.2.1 时必须失败；版本匹配时通过。
- Store 测试：类型错误、缺失字段和额外字段返回稳定路径，且错误文本与 details 均不包含敏感值。
- Manager/Telegram 测试：迁移失败保留旧 release 和旧配置，用户反馈包含字段路径但不包含字段值。
- 真实构建：构建 `download 1.0.8.tpx`，检查 manifest、插件依赖、wheelhouse SDK，并在临时 venv 中离线安装。

## 边界

- 不修改或删除 Unraid `/config/plugins/*/config.yaml`。
- 不放宽 Feature schema。
- 不在 Mac 工作区运行 Git、创建 worktree、发布或推送。
- 完成本地测试后仅通过 Syncthing 交给用户在 Unraid 发布。
