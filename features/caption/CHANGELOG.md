# Telepiplex Caption 更新记录

## 1.1.0 · 2026-09-28 · 本地源码，待用户发布

- 扩展为 18 个适配器：保留 ASSRT、Shooter、OpenSubtitles；完善 SubHD／字幕库正常公开下载；加入迅雷、SubDL、SubSource、Subf2m、R3SUB、Addic7ed、LWLTV、YYSub、四个字幕组目录及本地归档。
- SubHD 可使用匿名 session；字幕库、Subf2m 的搜索故障明确报告，并可通过 `/caption <作品query> --source <正规详情地址>` 使用已知条目。网站 Cookie、免费 Key 与文件 CDN 凭据严格分离。
- 动漫仅使用有成品证据的文件，绝对集号需要来源证据或用户映射；本地归档默认不扫描任何目录，可使用受限目录与 TSV 索引。
- 以真实正文区分简繁、单语／双语，补充分段、未完成制作素材及剪辑版本拒绝；7z 在独立受限进程中解包。选择规则和 Plex `chi`／`cht` 命名保持。
- 请求合并、缓存、免费配额退避及实际线程容量有界。单媒体默认 180 秒，超时仍保留已经完成内容检查的字幕，包括整季包内已完成的单集；用户取消仍立即停止后续写入。
- 配套 Search 2.6.0 提供已确认电影／明确季集的参考时长；保留无视频独立查询。Download 2.2.0、Rename 2.4.0、Host 3.9.0、SDK 2.2.1 与既有 Plex 接续兼容。

真实网站、免费 Key 未授权范围、媒体库样本、压测和文件清单见[交付审计](../../docs/audits/2026-09-28-caption-free-providers-delivery.md)。网站限制不视为字幕不存在；未声称付费源、验证码或未授权 API 已通过。
