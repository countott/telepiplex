# media_metadata v2-only 与终态单消息设计

**日期：** 2026-08-26  
**状态：** 已批准，直接实施  
**目标版本：** Host `v3.6.5-host`、Host API `1.7`、Plugin SDK `2.0.0`、search `2.0.0`、download `2.0.0`、rename `2.0.0`、sync `2.0.0`；caption `0.1.4` 不变。

## 1. 目标

telepiplex 的新任务和恢复任务只允许使用严格 `media_metadata` v2。删除 v1 的跨 Feature 读取、转换和命名适配，不再把 `title_original` 当作英文标题。与此同时修复 Rename `/m` 恢复分支丢弃 v2、终态消息封口时重复发送，以及两处已批准文案。

## 2. v2 唯一合同

v2 继续只冻结跨 Feature 必需事实：稳定身份、标题、媒体类型、范围和分类。`identity` 增加独立 `title_en`：

```text
identity = {
  primary_ref,
  provider_refs,
  media_type,
  title_zh,
  title_en,
  title_original,
  year,
}
```

`title_en` 与 `title_original` 语义严格分离。Search 必须从已验证英文来源或已验证英文片源证据生成 `title_en`；日文、西语等原文只进入 `title_original`。Rename 的顶层目录、季目录和媒体文件名统一使用 `title_en`。无法取得可靠英文标题时失败关闭或进入确认，不能拿其他语言冒充英文。

`metadata_id` 仍只依赖 primary ref、媒体类型和 scope，标题展示字段变化不改变同一身份和范围的幂等键。

## 3. 组件职责

### SDK

SDK `2.0.0` 只导出 v2 validator、attach/extract 和公共分类路由。删除 v1 converter；schema 1 输入返回 `unsupported_media_metadata_v1`。公共分类和字符串清洗从旧 v1 模块中拆出，v2 不再反向依赖 v1。

### Search

Search 的 Provider facts、别名、海报和剧集清单是内部候选证据，不是跨 Feature metadata。确认候选时投影为唯一 v2，并生成经过验证的 `title_en`。对外不得返回 `naming_metadata` 或 schema 1。

### Download

Download 严格验证并深拷贝 v2，持久化和 `download.completed` 原样传递。schema 1、缺少 `title_en` 或出现 `naming_metadata` 的提交在任何下载副作用前拒绝。

### Rename

Rename 直接消费 v2。正常 `/s` handoff 与 `/m` 运行中 `resolve_metadata` 恢复结果必须进入同一个 v2 adoption 函数。整理上下文由 v2、file tree、release/resource evidence 构成，不生成 private v1 adapter。

文件映射、移动、冲突、写后验证和清理结果写入独立 `organization_result`；v2 本身保持不可变。旧 schema 1 durable job 标记 `unsupported_media_metadata_v1`，文件保持原位。

### Sync

Sync 从 v2 读取身份、provider refs、scope 和 category kind；从 organization result 读取实际路径和扫描目标。不得依赖 v1 items、enriched placement、poster 或 naming metadata。需要的海报和语言信息使用 provider refs 查询。

## 4. 英文命名规则

命名唯一权威是 v2 `identity.title_en`。Game Life 的目标为 `游戏人生 (No Game, No Life)`；Hundred Years of Solitude 的目标为 `百年孤独 (One Hundred Years of Solitude)`。顶层目录、季目录和视频文件基础名一致使用同一英文权威值，不从 `title_original` 回退。

## 5. `/m` 恢复

当 download completion 不带 metadata 时，Rename 使用文件树生成 probe，调用 `media.search.resolve_metadata`。返回值必须先通过 v2 validator，再写回 durable payload，并直接构造整理上下文。不得调用 v1 `attach_media_metadata`，也不得因版本不匹配把 metadata 清空。

## 6. 终态单消息

OperationReportSink 的待渲染项必须保存其 segment id。已经由该 segment 渲染并封口的 revision 不得在活动 segment 清空后退回 legacy renderer。回归场景是：较早 revision 正在渲染、终态 revision 排队、同时 seal；断言只编辑既有消息，不再 `send_message` 第二条终态。

## 7. 文案

- `已选定片源，准备提交下载` 改为 `已选定片源，提交下载`
- `已下载，准备整理` 改为 `下载完成，开始整理`

相同语义的带句号或 handoff 变体一并统一，避免永久保留状态出现歧义。

## 8. 版本与混装保护

SDK 删除 v1 API，因此升级到 `2.0.0`。search、download、rename、sync 都升级到 `2.0.0` 并依赖 SDK `2.0.0`。Host API 保持 `1.7`，Host 自身因竞态和文案修复升级到 `v3.6.5-host`。caption 不消费媒体合同，保持 `0.1.4`。

同一运行环境不得混装理解不同媒体合同的 search/download/rename/sync。Feature 包的 SDK 依赖和技术身份测试作为安装前门禁。

## 9. 验收

1. SDK v2 合法 fixture 通过；schema 1、未知字段、缺少 `title_en` 均被拒绝。
2. Search 输出完整 v2，无 `naming_metadata`。
3. Download 保存和完成事件中的 v2 JSON 语义不变。
4. `/s` Game Life 全部命名使用 `No Game, No Life`。
5. `/m` Hundred Years of Solitude 恢复 v2 后进入真实整理处理器。
6. Rename 的 v2 不被 organization result 修改。
7. 终态并发 report + seal 只产生一条 Telegram 消息。
8. 两处新文案在源码和回归测试中一致。
9. Host、SDK 以及五个 Feature 的相关测试全部通过。

## 10. 本地边界

所有设计、修改和测试只在 `/Users/young/Documents/telepiplex` 完成。Mac 不执行 Git、worktree、发布或标签操作。完成后等待 Syncthing `Up to Date / 最新`，再由用户在 Unraid `/mnt/user/archives/life hacker/telepiplex` 检查和发布。
