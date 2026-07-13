# Plex 批次扫描、海报链路与写确认设计

## 扫描粒度

一个 `media.organized` 事件是一个批次。Feature 先为每个最终媒体路径创建独立 durable job，再按 Plex 媒体库分组：每个媒体库只记录一次扫描前快照并调用一次 Plex scan。相同扫描结果写入组内所有 job，之后每个 job 使用自己的 `final_path`、季号和集号逐项定位验证。

重试复用已完成的 scanning step，不重新扫描。若一个批次异常中断，仍保留每个 job 的持久化步骤状态。

## 普通媒体完整链路

普通电影和普通剧集保持 `scan -> locate -> match -> zh-CN -> artwork -> streams`。`artwork` 是固定且只执行一次的步骤：优先 TMDB/Fanart 无字海报并写入 Plex；没有候选或外部服务失败时记录 warning，但不伪造成功，也不重复覆盖。

TVDB 官方 Special 的保留规则不属于普通媒体，本次不改变。

## MCP 高风险确认

保留现有最小工具面和每个写工具的一次性确认。新增 `plex_apply_metadata_batch`，只允许打包 match 修复、中文元数据刷新和无字海报设置。预览阶段生成一个一次性确认 Token；用户确认一次后按顺序执行整个批次。扫描、任务重试、音轨和字幕操作不能混入元数据批次。

非 loopback MCP 继续强制 Bearer Token，配置键统一为 `mcp.auth_token`。

