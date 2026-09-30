# Telepiplex Caption 免费来源新版执行记录

目标：尽量补齐中文圈现成外挂字幕的免费来源；可使用用户自行申请的免费 key，排除必须付费的依赖。重点集成 SubHD、字幕库，扩展上一轮研究的可行来源；用 Flip the server 已有媒体登记抽取真实样本；完成压测、相关版本迭代及本地交付后停止。

边界：Mac 只改源码、文档与本地测试，不运行 Git/clone/发布，不修改用户媒体文件，不重放其他项目脚本。SubHD/字幕库使用页面公开的正常请求流程，不伪造验证 token。不把账号缺失或挑战误报成没有字幕。完整字幕只留临时验证目录，不随源码交付。

## 验收要求

- [x] 免费来源实现并注册：ASSRT/Shooter/OpenSubtitles保留；SubHD、字幕库正常流程；Xunlei、SubDL、SubSource、Subf2m、R3SUB、Addic7ed；LWLTV/yysub 的可行路径与真实限制。
- [x] 公开字幕组成品、本地历史归档可检索。不能仅写下一步建议；只有没有可用字幕/授权的入口可明确保留限制。
- [x] SubHD和字幕库实际搜索→下载→正文验证；如搜索不可用，必须有真实证据和可用详情导入路径，不能只留占位实现。
- [x] 修复分段字幕、ASS制作素材、中日双语误判，保留少量日语歌词/招牌的正常单语字幕。
- [x] 高压缩成品有安全的大小/数量/时限边界，恶意/损坏包继续拒绝。
- [x] provider超时/取消不会导致阻塞线程突破并发限制；同请求共享、下载缓存和异常隔离通过测试。
- [x] 配置模板/Schema/Telegram免费key入口、凭据隐私及重载衔接完整。
- [x] 使用真实媒体库标题分层做低速联网冒烟；离线高负载压测报告实际耗时、并发、缓存和失败行为。不对第三方站点做高并发攻击式压测。
- [x] 三业务链路回归：Download→Caption→Rename→Plex；媒体库扫描；无视频query；chi/cht与原始语种优先级不变。
- [x] 相关manifest/pyproject/changelog/文档版本一致，打包与安装/握手可用，本地全套必要测试通过。
- [x] 交付列出每个变更文件、实际验证与限制，提醒Syncthing最新，不发布。停用阶段汇报自动化，目标完成审计通过后结束。

## 当前工作分工

- 主任务：Engine容量/缓存，配置与服务，Search时长上下文，媒体库测试样本、压测、版本与集成交付。
- providers：providers.py、test_providers.py、中文站实现及工厂；HTTP上下文预算与cookie隔离。
- rename_integration：extra_providers.py及相关测试，六个新增免费影视来源。
- quality：quality/matching/archive及对应测试；保持现有接口，使用新增expected_duration_seconds作为元数据参考。

## 已验证事实

- 从Flip the server的2026-09-17机器交接包抽取16个真实作品标题，覆盖真人电影/剧、动画电影/剧及英语/中文粤语/日语/韩语/意大利语测试策略。只证明历史登记存在，不证明当前服务器文件或实时Search解析。
- 样本已保存为features/caption/docs/library-samples-2026-09-28.json，不包含服务器路径或凭据。
- SubHD当前公开prepare-download需要保留匿名session；正常链已取得ASS。字幕库详情链已取得RAR，当前搜索路径404，已完成严格身份校验的详情链接入口。
- 主任务第一轮Engine/metadata/service针对测试：56 passed（新增压力回归加入前）。不是最终验收结果。

后续验证结果与最终文件清单另存日期化交付审计，不能把本记录中的进行中项目当作已通过。

## 完成审计

Caption 1.1.0、Search 2.6.0 已完成本地验收；全套2548 passed，最终跨模块补测19 passed；1000请求/32并发离线压测及两包断网安装/握手通过。16媒体库样本首轮15命中，Reply1988身份/召回修复后网站526限制如实保留。Key缺失、验证挑战、动漫映射与未满季均有明确限制；详见 [最终交付审计](../../audits/2026-09-28-caption-free-providers-delivery.md)。阶段汇报在最终验收后停用，不发布、不执行Git。
