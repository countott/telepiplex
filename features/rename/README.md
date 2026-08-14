# rename Feature

`features/rename` 是 telepiplex 的独立媒体整理 Feature。rename 1.5.0 消费 durable `download.completed`，也支持 Telegram `/rename` 扫描 115 存量目录；它通过 `storage.provider` 执行文件级变更，并只在验证整理结果后发布 `media.organized`。

## file-first 整理链路

rename 会对下载根或用户选择的扫描根建立一次完整、递归、分页的文件树快照。扫描根和原有文件夹都只是遍历范围及弱提示，不是媒体身份或冲突边界。每个视频和字幕文件分别解析文件名中的标题、年份、季集、语言与内容角色，再按兼容的文件证据形成临时作品组；`Veep` 与 `Veep (2012)` 可进入同一候选组，而两个明确不同的年份不会被强行合并。

文件名是主要身份依据，字幕名与视频名具有同等的作品识别资格。父目录只可补充缺失提示或打破完全相同的候选，不能覆盖有效文件名、制造身份冲突或授权文件变更。`S01E01`、`1x01`、中文季集、动漫绝对集数、`Ep04`、`S1 - 01` 与 `Season 1 - 01` 等格式由确定性规则优先处理。

临时作品组仍必须交给 `media.search.resolve_metadata` 形成已确认的 `media_metadata v1`。AI 只处理确定性规则无法覆盖的长尾文件映射，不能确认媒体身份、覆盖外部元数据或授权删除。DeepSeek 请求保留 thinking，并要求 JSON 最终输出；rename 只解析最终 `content`，不解析、不保存也不记录 `reasoning_content`。最终内容为空、无效或因长度截断时只重试一次，仍失败则把受影响文件保留原位。

每个文件是身份、规划、执行、重试与结果的最小单位。无法识别、目标冲突、AI 失败或 provider 失败只影响对应文件，不会把整个目录移到 `/未整理`，也不会自动删除未匹配视频、样片、花絮、字幕或未知文件。源路径与最终目标相同是成功 `no_op`；同目录只改文件名时只调用 rename，不再追加复制后删除式移动。源目录仅在重新读取后确认完全为空时才可清理，扫描根永不删除。

## rename 终态规则

所有下列 `中文名`、`English Title`、合集名、季目录名和文件名都会先经过跨平台目标清洗：统一为 Unicode NFC，全角括号转半角，连续空白合并；移除控制字符、Windows 禁止字符 `\\ / : * ? " < > |` 及其常见全角形式；移除路径段末尾的空格和点；并把 Windows 保留设备名 `CON`、`PRN`、`AUX`、`NUL`、`COM1`–`COM9`、`LPT1`–`LPT9`、`CONIN$`、`CONOUT$` 改成带 `_` 后缀的安全名称。源路径、Job ID、callback 和元数据身份不预清洗，只在实际生成目标目录和目标文件名时应用。

| 内容 | 目标结构 | 关键约束 |
| --- | --- | --- |
| 单部电影 | `分类/中文名 (English Title)/English Title.ext` | 只有一个目标视频；文件名只保留确认的英文名和原扩展名 |
| 分类根目录裸视频 | 先创建 `分类/中文名 (English Title)/`，再生成 `English Title.ext` 并移入 | 永远视为未完成；不能因为文件名看起来规范就跳过作品目录 |
| 电影合集 | `分类/合集中文名 (Collection Title)/电影中文名 (English Title)/English Title.ext` | 合集中文名去掉末尾“系列”，英文名去掉末尾 `Collection`；每部电影目录只含一个同名视频 |
| 单集、整季、全剧 | `分类/中文名 (English Title)/English Title Season NN/English Title SxxExx.ext` | 季号两位；集号小于 100 时两位，100 起三位；季目录与文件 `Sxx` 必须一致；每个目标集号唯一 |
| 特别篇 | `分类/中文名 (English Title)/English Title Season 00/English Title S00Exx.ext` | 统一进入 `Season 00` |
| 电影外挂字幕 | `分类/中文名 (English Title)/English Title.chi.srt` | 同样支持 `.ass`、`.sup`、`.vtt`；可与视频同批，也可纯字幕合入已有目录 |
| 剧集外挂字幕 | `分类/中文名 (English Title)/English Title Season NN/English Title SxxExx.chi.ass` | 可平铺在源根、跨目录且只覆盖部分集；目标与同集视频共享名字主体 |

英语原作优先采用确认元数据中的原始英文标题；如果中文标题末尾已经重复英文标题，会去掉重复部分再生成 `中文名 (English Title)`。电影和剧集都先使用确认元数据与文件级确定性证据，只有仍无法映射的文件才交给有界 AI 兜底。

字幕目标名只保留 Plex 兼容的 ISO 639-2/B 语言后缀与源文件真实扩展名；`forced`、`sdh`、`cc` 等源标记不进入目标名。简体、繁体、双语及其他已知语言都保留；未知语言或无法安全确定集号的字幕保持原名原位，不阻塞其他文件。多个字幕若会生成完全相同的目标名，按稳定 `source_id` 分配规范名与 `variant-NN` 防冲突名，绝不静默丢弃。

rename 会在写操作前按文件预检目标冲突。已有目标与相同 provider 身份对应时作为幂等 `no_op`；同路径不同身份只阻断当前文件且不覆盖。执行中途失败会记录当前可观测路径，其他无关文件继续按自己的计划处理。

如果 Host 在交接前确认 sync/Plex 管理未安装或未启用，rename 会保留已经完成的整理结果并收敛为成功终态，明确通知“已跳过后续处理”，且不会发布无人消费的 `media.organized`。用户通知使用纯文本，文件名和路径不会依赖 Telegram Markdown 转义。

```bash
python tools/build_feature.py features/rename /tmp/rename-1.5.0.tpx \
  --repository local/telepiplex --branch main \
  --commit 0000000000000000000000000000000000000000
```
