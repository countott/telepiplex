# Telepiplex Caption 简中字幕来源扩展研究

核验日期：2026-09-28。本轮扩大来源调查，只保存研究文档和验证统计，没有修改 Caption 运行代码，也没有把新来源列为已接入。研究范围是现成外挂字幕，不下载影片、不提取内置字幕、不调用 AI 翻译生成替代品。

## 结论

可行来源明显多于目前接入的五项。优先补充 **迅雷、SubDL、SubSource、Subf2m、R3SUB**，再接 **Addic7ed、字幕组公开成品仓库、人人影视历史字幕归档、已有资源站的外挂字幕附件**。有验证码的中文站可保留用户完成网站下载后导入的路径，不应把网页可搜索等同于后台可以无人值守下载。

这些来源不能简单累加为独立库存：多个网站可能保存同一版字幕，同一字幕组也可能把成品分发到多个站点。需要按内容哈希去重、保留来源和版本信息，再执行用户要求的简繁、单语/双语与格式优先级。

本次网络样本证明的是特定公开文件可以取得。没有实际影片，未验证逐句同步或翻译准确性；没有用完整 `CaptionEngine.retrieve` 跑通这些尚未实现的 provider。正文结构检查与正式接入验收分开记录。

证据等级：**A** 为本次取得真实字幕文件/完整公开预览并检查正文；**B** 为一手页面确认中文条目或有效发布入口，尚未完成下载或正文核验；**C** 为历史实现或当前服务未证实；**X** 为不适合当前范围或明确停用。A 不代表该源的全部字幕均合格。

## 优先候选

| 来源 | 证据 | 对简中需求的实际价值 | 可行路径与限制 |
| --- | --- | --- | --- |
| 迅雷字幕库 | A | 按片名/发布文件名检索，实取简中双语 ASS/SRT、简中单语 SRT/ASS；电影和剧集均有样本 | 公开 `oracle/subtitle?name=` 接口，无 key。未找到正式开放平台契约，必须隔离适配器、缓存与节流，不承诺长期可用 |
| [SubDL](https://subdl.com/developers) | A | 《沙丘》简中目录有既有双语 ASS，匿名 ZIP 下载成功 | 正式使用官方 API，自有免费 key；本轮未注册。缺 key 的请求实测 401 |
| [SubSource](https://subsource.net/api-docs) | A | 《沙丘》真实简中单语 SRT 下载成功；可作为官方字幕版本的补充 | 官方 API 自有 key；公开页面下载路径也有成功样本。未认证 API 实测 401 |
| [Subf2m](https://subf2m.co/subtitles/interstellar/chinese-bg-code) | A | 《星际穿越》简中双语 SRT 下载成功 | HTML 详情页到公开下载链接，本次无登录/key；下载 URL 短效，需按条目重新取得 |
| [R3SUB](https://www.r3sub.com/show.php?id=rVshnE23414) | A | 取得站点标注 iTunes 来源的简中单语 SRT；适合补充电影单语字幕 | 正常公开预览/导出路径成功；ZIP 下载路径另有验证码，不能混称全部下载链可自动完成 |
| [Addic7ed](https://www.addic7ed.com/season/8438/1) | A | 《Physical》S01E01 简中 SRT，518 条时间轴，匿名下载成功 | 可补欧美剧集。普通网页请求与第三方账号适配器是两种路径；遇登录/验证码需明确报告 |
| [喵萌奶茶屋](https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage) | A | 官方外挂 ASS 仓库与成品包，实际取得简中单语样本 | 只读 HTTP 索引与按需取文件；选择成品，保留字幕组署名和版本说明 |
| [MingYSub](https://github.com/MingYSub/SubsArchive) | A | 公开成品压缩包以及简中 ASS，实际下载与解包成功 | README 明确成品应从 Releases 取；不能把全部源文件都当成完整字幕 |
| [人人影视历史字幕归档](https://huggingface.co/datasets/qundao/yyets-subtitles/tree/main) | B | 中文互联网历史影视字幕可建立本地检索库，免去每次在线站点检索 | 官方分享公告与公开镜像目录可核验；尚未导入全库，不承诺持续更新 |
| [资源站字幕附件](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/app/modules/subtitle/__init__.py) | B（实现依据） | 附件与下载片源通常有更直接的版本关联，尤其适合 Download 前后衔接 | 复用已有资源详情 URL 和用户自有站点会话；本轮未登录私有站点实测 |

## 迅雷：此前遗漏的重要入口

本次直接请求 `https://api-shoulei-ssl.xunlei.com/oracle/subtitle?name=...`，五个查询全部 HTTP 200、业务 `code=0`：

| 查询 | 返回条数 |
| --- | ---: |
| `Interstellar.2014.1080p.BluRay.x264` | 46 |
| `星际穿越` | 46 |
| `流浪地球.2019` | 57 |
| `你的名字.2016` | 52 |
| `Breaking.Bad.S01E01` | 54 |

中英文同片查询结果存在重复，表中条数不能相加作为覆盖量。片名查询不要求本地视频，因此具备服务独立 query 场景的潜力。参考 [Xunlei Subtitle 项目](https://github.com/Vector341/xunlei-subtitle) 的公开用法；[迅雷旧官方文章](https://yangtai.xunlei.com/?cpage=1&p=10364)仅用于说明字幕库背景，今天的可用性来自本轮实际请求，而非旧文章。

两轮实际取得 16 份字幕。当前质量函数结构接受 14 份，其中 13 份具有非零用户规则优先级；但至少 2 份疑似分段字幕仍获接受，因此 **不能把 13 写成完整合格字幕数**。

可确认的代表样本：

| 样本 | 正文检查 | 限制 |
| --- | --- | --- |
| `Interstellar.2014.720p.BluRay.x264.DTS-RARBG.ass` | 简中双语，2,240 个有效 cue，末尾约 168.9 分钟 | 未与真实片源核对 |
| `Interstellar.2014.1080p.BluRay.x264.DTS-RARBG.srt` | 简中双语，2,376 个有效 cue | 同上 |
| `流浪地球.简体中文.ass` | 简中单语，1,831 个有效 cue | 两份同名文件内容哈希不同 |
| `Kimi.No.Na.Wa...HDChina.srt` | 简中单语，1,470 个有效 cue | 同名 ASS 有格式问题，被拒绝 |
| `绝命毒师.S01E01_track3_chi.ssa` | 简中双语，688 个有效 cue，末尾约 57.2 分钟 | SSA 按现有 ASS 家族处理；未证明与任意发布版匹配 |

两个重要反例是《星际穿越》的 `-002.ass`（约 64.5 分钟）和《绝命毒师》的 `-001_track3_chi.ssa`（约 9.8 分钟）。这说明取第一份高优先级 ASS 不够，必须再处理分段与完整性。

## 海外中文库与订阅

### SubDL

[《沙丘》简中目录](https://subdl.com/subtitle/sd1628968/dune/chinese-bg-code)中本次可见 8 个条目；[已下载条目](https://subdl.com/s/info/gJBYsee3tr/dune)发布于 2022-05-09，标注 `chs&eng`，ZIP 内两份 ASS，分别有 1,001 / 1,261 条 Dialogue。两份均有中文、英文正文，OpenCC 简繁辅助检查支持简体判断；不是本次调用 AI 生成。文件数和条数不同，仍须检查发布版、字幕覆盖与具体内容。

[官方开发文档](https://subdl.com/developers)提供 v2 搜索、按文件名搜索、按条目下载、季集和包内文件信息，并支持 Bearer / X-API-Key 认证。正式适配应使用用户自有 key，明确区分未授权、限流和无结果。

[现行价格页](https://subdl.com/pro)显示：免费额度 2,000 API 请求/日；Plus 为 $3/月但不增加 API 额度；Pro 为 $5/月，30,000 API 请求和 2,000 次 API 下载/日；Max 为 $10/月，60,000 请求和 5,000 次下载/日。对当前需求，先用免费 key 即可评估，不必为了获取既有字幕购买 AI 翻译。价格为页面展示，未进入结算、未验证税费与地区购买条件。

### SubSource

[《沙丘》条目](https://subsource.net/subtitle/dune-2021/chinese_bg_code/2706679)的公开下载 ZIP 成功取得。SRT 为 UTF-8-SIG，84,221 字节、1,263 条时间轴，正文支持简中单语判断。站点标注其为 iTunes 版本；本轮没有独立核验字幕制作来源。

[官方 API 文档](https://subsource.net/api-docs)要求个人 key，当前列出 60 请求/分钟、1,800/小时、7,200/日。本轮未注册，未发现需要购买才能取得 key 的官方条件，也没有核验到应向用户推荐的付费价格。`Chinese_bg_code` 中可能混有简繁，语言参数只筛候选，最终仍以正文判定。

### Subf2m 与 Addic7ed

Subf2m 的[《星际穿越》中文分类](https://subf2m.co/subtitles/interstellar/chinese-bg-code)中一条实际上是无汉字拼音，另一条才是[简中双语](https://subf2m.co/subtitles/interstellar/chinese-bg-code/1081833)。成功样本 ZIP 内 SRT 为 **UTF-16 BOM**，2,217 条时间轴；把编码误当 UTF-8 会造成假阴性。下载地址有时效，不应长期缓存签名链接。

Addic7ed 的[《Physical》第一季页面](https://www.addic7ed.com/season/8438/1)明确有简中与繁中行，S01E01 简中公开下载返回 SRT 36,047 字节、518 条时间轴。[Bazarr 适配器](https://github.com/morpheus65535/bazarr/blob/master/custom_libs/subliminal_patch/providers/addic7ed.py)另有账号/cookie要求以及客户端配额设置；不能把第三方代码内的历史数字当作源站现行 VIP 价格或购买保证。

上述四个海外源的五份实际文件另经当前 `extract_subtitles`、`inspect_subtitle`、`subtitle_priority` 复查，全部被判为 `chi` 且接受：SubDL 双语 ASS 为 1,000 / 1,261 个有效 cue、优先级 400；SubSource 单语 SRT 为 1,263 个、优先级 200；Subf2m UTF-16 双语 SRT 为 2,213 个、优先级 300；Addic7ed 单语 SRT 为 518 个、优先级 200。有效 cue 与原始行计数口径不同。所有样本仍为 `timing_verified=false`，不证明版本同步或译文准确性。

## 中文电影与剧集站点

### R3SUB：公开预览可以导出字幕

[《Greenland 2: Migration》详情](https://www.r3sub.com/show.php?id=rVshnE23414)列出繁/简/粤/英 SRT，简中成员名为 `Greenland.2.Migration.2026.iTunes.cmn-Hans.[sg].srt`。正常公开预览通过页面声明的 POST 表单返回 HTTP 200、123,980 字节 HTML 转义文本；预览本身提供下载该版本 SRT 的操作。

按网页换行和实体规则还原，得到 80,612 字节 SRT。当前 Caption 检查为 `chi`、单语、1,398 个有效 cue、末尾约 92.7 分钟、优先级 200。首次简单替换 `<br>` 时重复保留原有换行，得到不合法 SRT，被检查器正确拒绝；修正导出换行后通过，没有改对白或时间轴。这个细节应进入未来适配器契约测试。

整包下载的 `/download.php` 路径有 reCAPTCHA，本轮没有完成，也未把公开预览成功写成 ZIP 成功。[上传规则](https://www.r3sub.com/up/)限制其收录类型，因此定位为官方来源标签/个人译本单语 SRT 的补充，不应承诺它提供字幕组简英 ASS。所谓“iTunes 官方”是站点标注，本轮未独立核验制作来源。

### 人人影视历史归档

[2024 年官方分享公告](https://weibo.com/1660646684/P3s774TqO)、[公开整理项目](https://github.com/qundao/backup-yyets-subtitles)、[Hugging Face 镜像目录](https://huggingface.co/datasets/qundao/yyets-subtitles/tree/main)形成可核验的路径。镜像约 18.3 GB，包含 `data.zip.001` 至 `.004` 及 `data2.zip`；公开表格有字幕信息、语言/格式关系及文件 ID 映射。实际读取元数据前 256 KiB 成功，未全量下载或逐包验证。

[cncases/subtitles](https://github.com/cncases/subtitles)已有将数据库与本地字幕目录组合检索的实现，可参考架构。其公开演示首页可访问，但本轮查询返回 403，因此优先考虑本地索引，不依赖演示站。归档可增加历史库存，不代表 2026 年的新剧更新源。

### 需要授权或尚未证实的中文入口

| 来源 | 当前证据 | 决策 |
| --- | --- | --- |
| [LWLTV](https://www.lwltv.com/subtitles/639180042974330879) | 2026-09-27 的《碟中谍5》简英 ASS 详情；支持 IMDb/豆瓣/片名，作品页有 RSS；下载经过 Turnstile | B。保留检索与用户下载后导入，未证明无人值守取文件；CMCT/FIX 等组目录是聚合标签 |
| [yysub.cc](https://yysub.cc/subtitle/67495) | 《犯罪心理》S19E09 等近期 ASS/SRT；下载检查实际返回需要登录，另有 Geetest | B。账户授权后再测；未通过官方互链确认其自称身份，不能混同人人历史归档 |
| [yysubs.com](https://yysubs.com/About) | 字幕组自述页面，声明旧 YYets 域名等不受其管理；未看到可运行字幕搜索下载库 | 不凭相似名称新增 provider |
| [A4K](https://www.a4k.net/) | 本环境 TLS 连接失败 | C。待核验，不断言全网停服，也不与 Kodi a4kSubtitles 项目混淆 |
| [深影译站](https://sysub.com/) | 官方账号/论坛互链可核验，2026 年仍有译作；本轮样本下载指向 MP4/MKV/网盘影片 | 可作来源追踪，暂不当独立外挂字幕库 |
| [FIX](https://www.zimuxia.cn/) | 本环境访问失败；聚合站有作品标签 | 直站待核验，不能将转载目录说成已接入官方渠道 |
| [TLF](https://sub.eastgame.org/) | 当前显示建设中 | 不列为现行可用检索源 |
| SSK 旧域名、泛称字幕网、同名导航页 | 无可靠当前外挂文件证据，部分旧域名已改为影片聚合 | 不用站名填充来源数量 |

## 动漫与字幕组公开成品

| 来源 | 本轮实际证据 | 接入取舍 |
| --- | --- | --- |
| [喵萌 Storage](https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage) | 《我们的七日战争》SC ASS 下载并检查通过；整季 7z 也下载成功，但触发解压限制 | 首选成品。公开文件树有 6,118 个字幕/压缩扩展条目，含多语/特效/多个版本，不能换算成作品数 |
| [MingY Releases](https://github.com/MingYSub/SubsArchive/releases/tag/202507) | Food Court 成品包有 JPN/JPSC/JPTC/SC/TC 共 60 个 ASS；另取《北极百货店》CHS ASS | 按成品及包内语言选择。24 个 SC/TC 单语成员与双语成员不能混用；当前双语误判详见质量检查 |
| [北宇治《鹿乃子》](https://github.com/Kitauji-Sub/subs-shikanoko/releases/tag/v1.0.0) | `_mono.zip` 匿名下载成功，24 个 ASS，简繁各 12 集 | B（正文待验）。优先明确单语成品，不抓未合并 OP/ED/Screen 源文件 |
| [拨雪寻春 Haruhana](https://github.com/HaruhanaSub/Haruhana-Storage) | 《秒速五厘米》7z 实取两份简日/繁日 ASS，均被当前日语片单语规则拒绝 | 下载路线成立，本样本不适用；按项目表选择确实有单语的作品 |
| [bipy/Anime-Subtitles](https://github.com/bipy/Anime-Subtitles) | AIR01 ASS 实取，但为中日双语，被拒绝 | 社区旧番备份，不能因仓库称简中就视为单语 |
| [foxofice/sub_share](https://github.com/foxofice/sub_share) | 《明日之丈》01 简中 ASS 实取并结构检查通过 | 社区旧番补充。作者已声明停止更新此 GitHub 仓库；树响应被截断，必须分目录索引 |
| [绿茶字幕组](https://github.com/Studio-Green-Tea/Studio-GreenTea-Subtitle-Storage) | 明确只放字幕；有 JPSC/JPTC 文件清单，未下载正文 | B。不能把简日双语放进日语原声的默认单语候选 |

八份独立字幕/字幕包匿名 HTTP 下载成功，其中七份主样本用当前 Caption 检查，北宇治本轮只核验 ZIP 目录。MingY 成品包当前 28 个 `accepted` 中有 4 个 JPSC/JPTC 第 06 集误判，不能直接报告为 28 份合格字幕。喵萌 7z 为 331,852 字节，声明展开 66,927,295 字节，压缩比约 201.7，仍维持现有拒绝。

[VCB 官方发布模板](https://github.com/vcb-s/VCB-S_Publishing/blob/master/发布模版.html)把字幕分享指向 Anime 分享论坛，本次论坛 403、附件未验，不是统一公开 API。[诸神 Q&A](https://subs.kamigami.org/30720.html)说明通常不单独发外挂；内封提取不在本任务范围。少量历史外挂例外的旧链接本次也未成功下载。

[LoliHouse 2026-09-25 发布页](https://acg.rip/t/364002)虽然标题标内封，但文件目录另有独立 `Subtitles.7z`。这是值得保留的发现路线；本次只读取目录，未启动 BT 或下载片源，不能声称字幕直链已打通。RSS 更新后可先检查文件清单，只有独立字幕文件才继续，优先回溯字幕组原始成品仓库。

保留每组署名、作品说明和原始版本，不因代码仓库许可证推断其中全部转载字幕的统一许可。来源清单通过只读 HTTP 获取，不运行仓库脚本，不使用 Git/clone；采用缓存和增量目录请求，避免每次 query 扫全库。

## 不依赖统一 API 的接入方式

1. **字幕组成品索引**：定期读取公开作品清单、发布记录与文件树，建立标题/别名/季集/版本索引，按需下载 ASS/SRT/压缩包。无需在 Mac 使用 Git。索引仅保存必要元数据和缓存哈希，不拉取整个视频资源库。
2. **历史归档本地检索**：把公开提供的字幕归档作为用户配置的外部目录，预先索引文件名、剧集、语言和正文哈希。适合人人影视历史包等固定库存；无视频 query 也可用。
3. **资源站附件**：已有 Search / Download 上下文传递资源详情与附件信息，Caption 获取外挂字幕，随后仍交 Rename。[MoviePilot 当前源码](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/app/modules/subtitle/__init__.py)有按资源详情页及站点 cookie 寻找“字幕”附件的实例。Prowlarr API key 不能代替源站会话。
4. **用户下载后导入**：SubHD、LWLTV 等需要用户网站操作时，保留来源条目；用户正常下载后投入指定目录，再执行相同正文质量检查和 Rename。该方式不是无人值守 provider，不应隐藏其人工步骤。
5. **RSS / 发布订阅 / 网盘清单**：作为新字幕发现和索引更新入口。只有实际存在的外挂 ASS/SRT 或字幕包才入库；订阅中的内封/硬字幕影片不能算外挂字幕来源。既有 115 同目录字幕也只算可复用资产，不是新的全网字幕搜索库。

## 暂不承诺的来源

| 来源/工具 | 本次判断与依据 |
| --- | --- |
| TVSubtitles | [源站统计](https://www.tvsubtitles.net/search1.php)有 Chinese 分类，未成功取得具体简中正文；B，低优先级 |
| Gestdown | [当前适配源码](https://github.com/morpheus65535/bazarr/blob/master/custom_libs/subliminal_patch/providers/gestdown.py)声明中文，未验证正文；C，不与其关联的 Addic7ed 重复计库存 |
| SubtitleBest | [旧官方集成源码](https://github.com/ChineseSubFinder/ChineseSubFinder/blob/master/pkg/logic/sub_supplier/subtitle_best/api.go)证实历史上存在真实字幕 API；本次网站/API访问失败，当前运营、额度、订阅与价格未证实；C |
| ChineseSubFinder | [项目自己的停更公告](https://github.com/ChineseSubFinder/ChineseSubFinder/blob/master/SeeYou/README.md)说明 2025-05-10 云服务到期后主程序无法正常使用，不作为当前可部署的依赖。此公告不等于独立 SubtitleBest 已停服 |
| Podnapisi | [Bazarr v1.6.0](https://github.com/morpheus65535/bazarr/releases/tag/v1.6.0)明确因其离线而移除，不依据旧 provider 清单推荐 |
| SuperSubtitles / Wizdom | [前者](https://github.com/morpheus65535/bazarr/blob/master/custom_libs/subliminal_patch/providers/supersubtitles.py)只声明匈牙利语/英语，[后者](https://github.com/morpheus65535/bazarr/blob/master/custom_libs/subliminal_patch/providers/wizdom.py)只声明希伯来语，不计中文来源 |
| SubtitleBee / Subgen | [SubtitleBee](https://subtitlebee.com)与[Subgen](https://github.com/McCloudS/subgen)提供转写/翻译能力，不是本轮需要的现成中文字幕库 |
| Bazarr / MoviePilot | 调度或接入工具，不是独立字幕库存；逐一验证底层 provider，不按插件数量声称中文覆盖量 |

## 接入前必须补的质量检查

- **完整性与版本**：结合 Search 元数据时长、剧集时长、片源实际时长、分段文件名和同作品候选时间覆盖识别明显片段。字幕常在片尾前结束，不能用单一固定比例草率拒绝全部；没有视频时保持 `timing_verified=false`。
- **非英语原声的单语要求**：本次中日双语 ASS 有个别被当前函数误判为中文单语。必须联合实际对白、同时间轴多行、ASS 样式、假名及文件标记处理；画面日文或 OP/ED 的少量假名不能等同于整片双语。不能通过删除日文轨道冒充字幕组发布的单语成品。
- **成品与素材区别**：单独 OP、ED、Screen、模板、特效草稿不可当正片字幕。部分仓库要求成品从 Releases 取，需尊重其发布方式。
- **安全解包边界**：动漫特效 ASS 压缩率可能很高；本次喵萌成品 7z 触发现有压缩比保护。未放松保护，应另行设计有硬性大小/数量上限的提取策略再验证。
- **格式与真实性**：UTF-16、GB18030、Big5、简繁混合、拼音、forced-only、机器翻译标记与网页冒充文件都需继续验证。提供者标签不能直接决定 `chi` / `cht` 或用户优先级。

建议下一次实现按“迅雷 + SubDL + SubSource”先扩大常用影视覆盖，同时修复上述质量问题；随后加 Subf2m / R3SUB / Addic7ed，再以一个通用文件索引适配器覆盖字幕组成品和历史归档。保留现有 ASSRT 主干；新增来源不能改变既定优先级与 Rename → Plex 链路。

## 交付与验证边界

新增本文与同目录 [机器可读统计](provider-expansion-validation-2026-09-28.json)。统计保留公开来源、样本名称、HTTP 状态、字节数、哈希及检查结果，不包含完整字幕正文或凭据。下载文件只在 `/tmp` 临时保存。

本轮为研究文档变更，没有运行全量 pytest，也没有宣称新 provider 已完成实现。实际执行公开 HTTP 请求、少量文件下载、受限解包、正文解析/语言检查和文档数据校验。后续接入需要独立离线契约测试、真实网络测试及三个业务入口回归。

文档本地校验实际通过：JSON 可解析、9 组证据数据、16 个迅雷文件与 5 个海外文件的统计一致、R3SUB 的 1,398 cue 结果一致、本地 Markdown 链接有效、统计不含字幕正文/凭据字段。可重复基础命令如下，两项本轮均返回退出码 0：

```bash
python3 -m json.tool features/caption/docs/provider-expansion-validation-2026-09-28.json >/dev/null
test ! -e .git && test ! -e .worktrees && test -d .stfolder
```

未执行 Git、发布、账号注册或付费购买。等待 Syncthing 显示 `Up to Date / 最新` 后，文档随现有链路同步到 Unraid `/mnt/user/archives/life hacker/telepiplex`。
