# Telepiplex Caption

当前源码版本：`1.2.0`；SDK `2.2.1`；Host API `1.9`。搭配 Host `3.9.1`、Download `2.2.0`、Search `2.6.1`、Rename `2.4.0`。本模块只查找、检查和补齐外挂字幕；不检查视频内置字幕，不生成或翻译字幕。

## 三个入口

1. **下载后补字幕**：Download 完成 → Rename 确认媒体身份及文件树 → 调用 Caption → 校验、选优并通过 Rename 写入外挂字幕 → Rename 刷新文件树后统一整理视频和字幕。Caption 没有安装、被关闭或未找到合格字幕时保留现有整理能力；存在未确认的存储写入时先等待写入结束。Plex 的既有手动入口保持。
2. **媒体库补字幕**：`/caption` 和 `/caption scan` 打开相同的目录菜单，选择 Rename 已配置目录，或输入其他 115 目录。也可用 `/caption scan /真人电影` 直接指定目录。复用 Rename 的完整扫描，扫描结束后显示视频数量，点击“开始补字幕”再逐文件确认身份、匹配字幕，在现有视频旁写入同名外挂字幕。此入口不移动或改名原有视频。同名作品或缺少原始语言时暂停当前批次，确认后继续；仍无法确定作品或季集的文件会保留并报告。
3. **单独查询字幕**：在目录菜单点击“单独查找字幕”后输入作品，或直接发送 `/caption 星际穿越 2014`、`/caption Breaking Bad S01E02`。只调用 Search 的元数据能力，不启动 Prowlarr 片源搜索或视频下载。同名作品先选择；缺少原始语言时先确认语言。即使没有视频，也按 Rename 规则保存到配置的 `output_path`，默认为 `/字幕`。整季／全剧查询会按包内明确季集分别选优，不把整包当成一个单集字幕；不能验证完整覆盖时会说明。

## 命令交互

`/caption` 的选择、扫描确认、进度与取消在同一个任务面板内完成，交互方式与 Rename 对齐：

1. 目录菜单每页最多 8 项，可翻页，也可选择“输入其他目录”或“单独查找字幕”。输入阶段可返回目录菜单。
2. 选择或输入目录后先扫描视频，展示数量并等待“开始补字幕”确认；确认前不检索或写入字幕。直接指定路径也经过这个确认步骤。
3. 存在多个作品候选时，展示编号、中文／原文标题、年份、国家或地区及电影／剧集／动画类型，支持点击作品按钮或回复编号。批次处理时同时显示当前文件，确认作品或原始语言后继续当前批次。
4. 运行中在同一面板更新进度，支持取消；已经写入的字幕保留。

快捷 query、`scan` 路径和 `--source` 详情链接入口继续可用。1.2.0 调整 `/caption` 交互；配套 Host 3.9.1 清理异步任务结束后残留的输入会话，避免影响下一条作品链接。`/caption_config` 本轮未重构，配置字段、字幕来源、质量优先级和命名规则保持。

## 选择规则

| 原始语言 | 优先级 |
| --- | --- |
| 英语 | 简中双语 ASS → 简中双语 SRT → 简中单语 SRT → 繁中 |
| 中文、粤语及其他语言 | 简中单语 → 繁中单语 |

简繁与单双语由字幕实际内容判断。SSA 按 ASS 家族解析并保持原文件格式。只生成 `.chi` 和 `.cht` 语言后缀，例如 `Movie.chi.ass`、`Movie.cht.srt`；双语不添加第二个语言标记。简中单语 ASS 不擅自插入英语片的指定优先级，也不通过删除英文台词伪造单语字幕。

匹配时核对作品名／可信编号、年份、季集、WEB／蓝光来源、剪辑版本及可用 FPS。压缩包成员再次检查，拒绝错片、错季集、仅样片、畸形文件及冲突信息。解析 SRT、ASS、SSA，支持 UTF-8、UTF-16、GB18030、Big5 等编码并保存为 UTF-8；验证中文内容、双语的同时出现证据、有效时码、合理时长、字幕量和重复内容。

ASS 中极少量无法显示的 OP／ED 单字特效（有效时间语法、`Effect=fx`、结束不晚于开始、最多 64 条且不超过 Dialogue 的 2%）不参与对白统计，并附 `ignored_nonrendering_effects` 提示，保存的 ASS 原文保持；普通对白的坏时码仍拒绝。此边界依据 [libass 的事件渲染判定](https://github.com/libass/libass/blob/master/libass/ass_render.c)，不代表已验证实际播放。

缺少视频时长、FPS 或完整发布信息时，不能确认相应兼容性；无视频时会报告 `video_timing_unverified`。时码结构与视频时长检查不等于逐句对白同步，`timing_verified` 不会被自动设为真。这些自动检查也不替代翻译准确性的人工审校。存在非零 Shooter 延迟且未应用校正的字幕会拒绝。候选检查有上限，达到上限会说明结果只是已检查候选中的最优项。

## 免费来源与实际边界

1.2.0 沿用 18 个适配器。免费个人 Key 可后补；不调用付费下载、AI 翻译或转写服务。网站限制按来源分别报告，接入数量不等于 18 个站点随时可自动下载。

| 来源 | 路径与本轮验证范围 |
| --- | --- |
| [ASSRT／射手网](https://assrt.net/) | 公开搜索和下载，无需 token；个人 token 可切换官方 API。真实英／中／日语正文通过检查。 |
| [Shooter Hash API](https://www.shooter.cn/api/subapi.php) | 保留视频哈希匹配；115 常规文件树没有四段哈希，缺哈希时明确跳过。 |
| [SubHD](https://subhd.tv/) | 普通公开搜索，保留站点发放的匿名 session，经过准备下载页面取得文件；《星际穿越》实际选出简英 ASS。 |
| [字幕库 Zimuku](https://zimuku.org/) | 详情→下载页→站点公开签名链接可用，真实整季 RAR 已按集检查。当前搜索入口 404，可使用下方详情链接入口。 |
| [迅雷](https://api-shoulei-ssl.xunlei.com/oracle/subtitle) | 片名查询及公开下载，不要求本地视频；真实简英 ASS 可用。分段文件会拒绝，来源标签不能代替正文判断。 |
| [SubDL](https://subdl.com/developers) | 官方 v2 API，需要自行申请免费 Key；无 Key 会报告待授权。API 已做契约测试，公开文件真实下载通过；尚未用个人 Key 实连 API。 |
| [SubSource](https://subsource.net/api-docs) | 官方 API，需要个人免费 Key；契约测试通过，真实公开字幕正文已复核，尚未实连授权 API。 |
| [OpenSubtitles](https://opensubtitles.stoplight.io/docs/opensubtitles-api) | 现有官方中文分类 API；需要 Key，账号／下载额度由来源决定。仅使用免费额度，排除已标注的机器翻译。 |
| [Subf2m](https://subf2m.co/) | 普通详情→刷新短效下载链接，UTF-16 简英 SRT 实测通过；当前搜索返回 HTTP 500，支持详情链接入口。 |
| [R3SUB](https://www.r3sub.com/) | 读取页面提供的公开 SRT 预览／导出，真实简中单语通过；有验证码的 ZIP 路径不视作可自动下载。 |
| [Addic7ed](https://www.addic7ed.com/) | 剧集、季集和中文分类网页，匿名样本下载通过；授权和配额不足时分别报告。 |
| [LWLTV](https://www.lwltv.com/) | 公开检索可用，当前样本下载要求 Turnstile；返回需网站操作，可下载后用本地归档入口导入。 |
| [YYSub](https://yysub.cc/) | 可检索，下载前遵循站点权限检查；当前未登录样本要求有效 Cookie，未验证用户账号下载。 |
| [喵萌奶茶屋](https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage) | 公开文件索引和 README 指向的成品，按需下载，保留来源与署名。 |
| [MingYSub](https://github.com/MingYSub/SubsArchive) | 只取 Releases 成品；不把制作工程文件当作最终字幕。 |
| [北宇治字幕组](https://github.com/Kitauji-Sub/subs-shikanoko) | 当前接入 `subs-shikanoko` 的官方单语成品发布，不代表字幕组全库。具体索引入口见[配置说明](docs/catalog-usage.md)。 |
| [拨雪寻春 Haruhana](https://github.com/HaruhanaSub/Haruhana-Storage) | 官方公开成品索引；无可靠季集映射时保持未解析。 |
| 本地字幕归档 | 用户配置只读目录和可选 TSV 映射，可导入自行取得的人人影视历史库及网站下载包；默认不扫描任何本地目录。 |

详情入口示例：`/caption Friends 1994 S01E01 --source https://zimuku.org/detail/2265.html`。支持 SubHD、字幕库、Subf2m、R3SUB 的正规详情地址；仍通过 Search 确认片源身份，并逐一验证来源真实作品、年份、季集和正文，链接不能强行覆盖匹配结果。

`/caption_config` 支持保存目录、自动开关及 ASSRT／OpenSubtitles／SubDL／SubSource 授权字段。其余字段写入 `/config/plugins/caption/config.yaml`，通过现有模块重载流程生效；见[默认配置](config.default.yaml)、[Schema](config.schema.json)和[字幕组／本地归档配置](docs/catalog-usage.md)。网站 Cookie 仅发送给对应来源域名，不发送到文件 CDN；不输出授权值。GitHub 公共索引不强制 token，需要时可自行配置以提高公开 API 配额。

动漫绝对集号不能直接当作已播季集；只有来源明确说明或用户提供 `episode_mappings` 才允许换算。未知映射报告 `episode_mapping_unresolved`。Search 提供的电影／精确单集时长仅用于辅助拒绝严重截断，不能替代视频实测，也不能证明对白同步。

单媒体默认检索预算 180 秒，网络并发 3、内容检查并发 2；相同请求共享实际工作，来源超时后尚未退出的线程继续占用原槽位，避免重试叠加请求。达到候选或时间上限会返回已检查的最优字幕并报告未完成范围。自动接续同时受总预算约束，预留写入时间。高负载测试只对离线替身执行，公开来源只做低速验证。

来源可能变更网页、配额或授权策略；本次成功样本不能保证所有条目都可下载。完整来源研究、实际命中及限制见[新版交付记录](../../docs/audits/2026-09-28-caption-free-providers-delivery.md)。

## 写入、取消和恢复

所有目标路径与文件名由 `media.rename` 计算，通过 `storage.provider` 的分块上传写入 115。每块最多 192 KiB，单文件最多 8 MiB；完整 SHA-1 一致后才提交。相同内容可重复执行，已有不同内容的同名字幕不会覆盖。遇到冲突会报告，用户可自行处理已有文件后重试。

ZIP、gzip、7z、RAR 采用受限解包，防止路径穿越、符号链接、重复路径、压缩炸弹和无界读写。RAR 需要解压后端；Host 3.9.0 的 Dockerfile 已配置安装 `unar`，本次 Mac 实测使用 `bsdtar`。缺少后端时报告并继续其他候选，不把无法检查的压缩包视为合格字幕。

支持取消任务；已经写入的字幕保留。模块重启后未完成任务标为中断，重新发起时依靠校验和及持久化上传记录避免重复写入。结果保存在 Feature 状态目录的 `caption.db`，最多保留 500 个任务；一次大型扫描的明细摘要保留前 100 项，处理数量单独统计。

## 本地验证和构建

```bash
cd features/caption
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  python3 -m pytest -q -p no:cacheprovider tests
```

测试需要安装 [受控依赖](requirements-feature.txt)。`.tpx` 正式包仍由用户从 Unraid 发布后交给现有构建流程生成。Mac 本地只编辑、测试；等待 Syncthing `Up to Date / 最新` 后交付，不执行 Git 或发布。
