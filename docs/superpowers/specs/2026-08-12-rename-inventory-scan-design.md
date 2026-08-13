# rename 存量媒体补整理设计

## 范围

新增 Telegram `/rename` 命令，用于扫描 115 中已经存在、但没有成功进入 rename 管线的媒体。命令只增加新的入口和存量扫描编排，复用当前 `content_probe → media.search → confirmed media_metadata → rename`，不修改 Search 对整包、单集、全剧的识别和范围优先级，也不修改 Host 海报降级链路。

## 交互

`/rename` 首先列出 rename 配置中的四个 `category_folder`，并追加 `unorganized_path`。用户选择一个目录后，Feature 在后台只读扫描该目录的直接子项；每个直接子文件夹视为一个 release，必须读取它的完整后代文件树，内部目录不会再拆成独立任务。直接视频文件按一个单节点 release 进入未完成队列；执行时创建作品容器目录，重命名视频并移动进去。

扫描从根目录和每个后代目录分别调用 115 列表接口，并按 1000 项一页持续读取，直到最后一页，不使用 `get_file_tree` 的默认深度和总节点上限。如果 provider 忽略分页导致页面不前进，扫描直接报告文件树不完整，不允许用截断树做完成判定。扫描完成后，Telegram 面板只显示“已完成”和“未完成”两类统计，并提供“开始补整理”和“取消”。只有用户确认后才执行远端重命名或移动。

## 判定与幂等

扫描 Job 使用 115 `file_id` 生成稳定 `inventory:<file_id>` 身份；缺少 `file_id` 时使用完整源路径摘要。Job 只用于批次恢复和执行幂等，不参与当前目录的完成判定。即使历史 Job 已完成，只要当前文件树不符合 rename 终态结构，仍归入未完成并允许重新执行。

分类目录和 `/未整理` 使用完全相同的结构判定：只有直接子项本身是文件夹，并且其完整文件树符合 rename 终态结构，才算已完成；不符合就是未完成。根目录裸视频、没有视频、命名不规范、层级不规范、季号不一致或目标名仍含非法字符的子项，都属于未完成，不再单列或跳过。

电影终态为 `中文名 (English Title)/English Title.ext`；电影合集终态为 `合集中文名 (Collection Title)/电影中文名 (English Title)/English Title.ext`。剧集终态为 `中文名 (English Title)/English Title Season NN/English Title SxxExx.ext`，季目录与视频文件的季号必须一致。所有目标路径段都必须已经通过 rename 的跨平台目标名清洗，包括 Unicode NFC 统一、移除控制字符、Windows 禁止字符与常见全角形式、末尾空格和点，并规避 Windows 保留设备名。清洗只应用于生成目标，不改源路径、Job ID、callback 或确认元数据身份。

## 执行

待处理条目严格串行。分类目录来源的目标目录保持为用户选择的分类目录；未整理来源在元数据确认后根据 `media_metadata.placement.category_kind` 查找 rename 配置的 `category_folder` 目标。目标路由缺失时只失败当前条目，不移动源文件。

元数据唯一时直接处理；有歧义时暂停批次，在当前 operation 中显示现有候选选择面板。用户确认后处理当前条目并继续余下队列。每个成功条目仍发布 `media.organized`，但存量批次父 operation 只在整个队列结束后完成，避免第一个条目交接 sync 后提前终止批次。

## 失败与恢复

单个条目失败时记录失败并继续下一项，最终汇总成功和失败数量。用户取消时停止尚未开始的条目；正在进行的存储调用沿用当前协作式取消边界。扫描列表和父批次是当前 Telegram 会话状态；每个条目的稳定 Job 保存在现有 rename 数据库中。重复扫描时以实时文件树为权威：结构已完成的不进入批次，结构未完成的可重开同一稳定 Job。

## 配置与发布面

rename 配置新增与 Search 相同结构的四项 `category_folder`，作为存量入口菜单和未整理反查后的目标路由权威。Feature manifest 新增可见命令 `/rename`，既有 `/rename_config` 保持隐藏配置命令。
