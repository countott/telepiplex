# 官方字幕目录与本地归档

Caption 只取得独立 ASS、SSA、SRT 或包含这些文件的 ZIP、7z、RAR，不下载视频、不提取 MKV 内嵌字幕。下列目录用于发现实际成品，候选仍须通过统一的作品、季集、语言、结构和时长异常检查；公开可下载不等于字幕完整、译文已审校或音画同步已验证。

## 公开官方目录

| 配置键 | 实际读取范围 | 成品界限 |
|---|---|---|
| `nekomoe` | [喵萌 Storage](https://github.com/Nekomoekissaten-SUB/Nekomoekissaten-Storage) 的目录树、作品 README 与[项目表](https://github.com/orgs/Nekomoekissaten-SUB/projects/1)公开行 | 优先 README 指向的 `subtitle_pkg`；不读 `subtitle_effect` / `subtitle_jpn` 成品源。按组方说明仅少量 1–4 文件作品允许直接 ASS/SRT。 |
| `mingy` | [MingY SubsArchive Releases](https://github.com/MingYSub/SubsArchive/releases) 的作品表与实际 asset | 只使用 Release 成品；表格的中、日、英文同一行建立别名。包内简中、繁中、日语、中日双语分别验体。 |
| `kitauji` | [北宇治《鹿乃子》最新 Release](https://github.com/Kitauji-Sub/subs-shikanoko/releases/latest) | 目前只覆盖这个已核验作品的 `_mono.zip`，不把整个字幕组所有作品写成已覆盖，也不读取待合并的源 ASS。 |
| `haruhana` | [拨雪寻春 Storage](https://github.com/HaruhanaSub/Haruhana-Storage) 的目录树、作品 README 与[项目表](https://github.com/users/HaruhanaSub/projects/2)公开行 | 官方直接提供的字幕文件/包；排除 Fonts、源特效、OP/ED、Commentary 等。大量成品是中日双语，可能不符合当前日语片的单语优先级。 |

这些公开目录无需 API key、登录或付费。可选 `github_token` 仅发送给 `api.github.com` 获取较高读取额度，不发送给 raw、Release 下载或其他域名。默认缓存公开元数据 3600 秒，缓存位于本次运行的 provider 实例内；正文按需下载。目录树截断会返回 `catalog_incomplete`；公开项目表可能只返回当前筛选的部分行，未找到不代表该组从未制作该作品。MingY 默认最多读取 3 页、每页 30 个 Release，可按需设置 `max_release_pages`（1–10）。

## 别名与动漫季集映射

Search 的标题、原名和已确认别名先与官方目录核对。`title_aliases` 可补充用户确认的目录名/资产名与作品名关系；不要用模糊词、简称或不确定翻译强行关联。来源 README 明确写出 `Season 1/2` 并对应具体成品链接时可形成映射；其他动漫绝对集号不会自行猜成 TMDB aired 季集。

下面是配置结构示例，应放在现有 Caption 配置的 `providers` 下；实际配置位置以部署中的 Caption 配置为准。`season` 与 `offset` 必须基于你确认的 Search/TMDB 季集关系填写。

```yaml
providers:
  nekomoe:
    enabled: true
    cache_ttl_seconds: 3600
    github_token: ""  # 可留空
    title_aliases:
      5Hanayome: ["五等分的新娘", "Go-Toubun no Hanayome"]
    # 该作品 README 本身已有 Season 1、Season 2 及对应链接。
  mingy:
    enabled: true
    max_release_pages: 3
    episode_mappings:
      Food.Court.7z:
        season: 1       # 示例：请先确认目标元数据的季集编排
        offset: 0
        first: 1
        last: 6
  kitauji:
    enabled: true
    episode_mappings:
      subs-shikanoko:
        season: 1       # 示例：请先确认目标元数据的季集编排
        offset: 0
        first: 1
        last: 12
  haruhana:
    enabled: true
```

映射键优先匹配资产文件名，其次匹配官方作品目录名。本地目录则使用完整相对文件路径。`episode = 原始集号 + offset`，例如已确认原编号 13–24 对应第二季 1–12 时，可设 `season: 2, offset: -12, first: 13, last: 24`。明确 `S02E01` 的文件不再次平移；SP、OVA、OP/ED、小数集、跨集范围、季号矛盾和范围外文件不会映射。无映射的绝对集号会保留未决状态，而不是把整包随机匹配到用户指定的一集。

片源版本仍须核对；例如 Encore、BD/Web、剧场版/TV 不应只因同名视作相同时间轴。映射只解决编号，不能证明同步。保留所有原始 ASS 样式及来源署名，最终 Plex 名称仍由 Rename 生成 `chi` / `cht`。

## 本地历史归档

`local_archive` 默认 `roots: []`，不会扫描未配置目录。用户已有的历史字幕、人人字幕归档等可自行解包到受控目录，再把**运行 Caption 的容器能够读取的路径**加入 `roots`。Mac/Unraid 宿主机路径不会自动变成容器路径；请先配置只读挂载。本模块不下载 18 GB 全量历史包，也不声称已导入这些数据。

```yaml
providers:
  local_archive:
    enabled: true
    roots:
      - /media/subtitle-archive
      - /media/subtitle-backup
    mapping_file: /config/caption-subtitle-index.tsv
    cache_ttl_seconds: 300
    max_files: 20000
    max_depth: 12
```

普通规范命名可直接检索，例如 `Example.2020.chi.srt`、`Example (2020)/Season 2/Example.S02E03.chi.ass`。压缩包只索引容器文件名，不预先展开所有内容；实际选中后再安全提取并逐个核对。下载时使用逐路径组件的 no-follow 文件描述符再次检查，拒绝软链接、路径逃逸、非常规文件与超限文件。超过目录条目/深度上限会明确报告索引不完整；可以缩小 roots 或合理提高限额。

对于数字编号文件、站点历史 ID 或作品别名，用可选 UTF-8 TSV（**实际制表符分列**，不是空格）提供显式索引：

```tsv
root_index	relative_path	title	year	media_type	season	episode	aliases	imdb_id	tmdb_id
0	movies/12345.zip	Example	2020	movie			示例电影	tt1234567	
1	series/67890.srt	Example Series	2022	series	2	3	示例剧集		
```

`relative_path` 必填，仅能引用对应 root 内的文件；`root_index` 从 0 起，未填默认 0。建议始终填写 `title`，其他列按实际已知信息填写，多个 `aliases` 用分号分隔。`season/episode` 表示这个索引文件确实对应的目标季/集；**整季包不能填某一集来掩盖包内集号未知**，应使用 `episode_mappings`。`imdb_id/tmdb_id` 仅填写已核实的电影或系列身份。未知资料留空，不从文件 ID 推断年份或作品。

用户归档的来源与许可仍以原字幕组/归档说明为准；本地导入不改变字幕权利，也不会自动修改字幕正文以伪装成单语或“修复”成完整内容。

## 成品 ASS 的有限兼容与本地验证

部分成熟成品的 OP/ED 动画会残留不可显示的单字事件。[libass 渲染实现](https://github.com/libass/libass/blob/master/libass/ass_render.c)只显示开始时间不晚于当前帧、且结束时间晚于当前帧的事件；结束不晚于开始的事件没有可显示区间。Caption 只对时间语法有效、`Effect=fx`、明确 OP/ED 样式、去除样式后单字、每文件至多 64 条且不超过全部 Dialogue 2% 的这种事件，从语言/时间统计中忽略，报告 `ignored_nonrendering_effects`，**原始 ASS 事件仍完整保留**。普通对白、其他样式、缺失 fx、多字、比例超限或坏时间语法继续拒绝。

2026-09-28 本地复验复用了研究时实际取得的官方元数据快照与字幕包，并补读相关公开 README/项目表；这不是所有来源全目录的在线可用性测试。显式配置 Food Court 季集映射后，MingY 普通版得到 6 集；Encore 版在未指定对应片源时拒绝。北宇治该单语包得到 4 集，喵萌五等分第一季包得到 8 集；其余有普通零时长对白、其他坏事件或语言证据不足，仍被拒绝。拨雪寻春此次电影包的两份中日双语均不符合当前日语片单语规则。以上只说明这些文件通过当前身份与正文门禁，没有视频同步、全片完整性或翻译审校保证。
