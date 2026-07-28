# Search source convergence and diagnostics

本文件补充 Search 1.1.0 unified anchored search 的来源收敛、AI
兼容、诊断日志和用户交互合同。它不恢复旧的程序评分候选，也不让
AI 成为媒体事实来源。

## 目标

- 让 `冰果` 这类中文入口可以通过受约束的跨语言查询提示找到同一
  作品的 TVDB 或 Wikipedia 事实。
- 让豆瓣标题、年份和不可见 Unicode 控制字符在进入实体图之前完成
  清晰、可复用的规范化。
- 让 `ODDTAXI` 一类 AI fact binding 错误可以自动纠正一次，并在最终
  失败时留下足够还原问题的结构化日志。
- 用户界面不得展示 `series_root`、`v0`、`standalone`、
  `canonical_latin_title` 或 `wikipedia:not_bound` 等内部标记。
- Search 的普通 AI 请求必须遵守已有 `thinking_mode` 配置，并兼容
  DeepSeek OpenAI Chat Completions 的字符串或对象 JSON 内容。

## 方案选择

采用“anchored candidate 内的受约束查询提示”方案：

1. 首轮三源广泛召回保持不变。
2. AI 仍只用 fact ID 组织候选。
3. 某个候选缺少 Provider 时，单独调用查询提示编辑器。编辑器只能
   返回本次候选 ID、缺失 Provider 和待验证标题提示；不得返回稳定
   ID、URL、年份结论、正式元数据或 Prowlarr query。
4. 程序把候选根事实中已经验证的年份、媒体类型和 AI 标题提示组合成
   结构化 Provider 查询。
5. Provider 返回的事实重新进入实体图和 AI fact binding；未被 Provider
   验证的标题提示不得进入 `media_metadata v1`。

不恢复旧 `source_orchestration` 的多轮工具代理。它会与 Search 1.1.0
的候选编辑器形成两套并行决策链，增加状态分叉。本次只复用其
OpenAI-compatible transport 能力。

## 标题规范化

豆瓣适配器在构造事实前必须：

- 执行 Unicode NFKC；
- 删除 `Cf` 类不可见格式控制字符；
- 把末尾括号年份从标题拆出；
- 当结构化年份缺失时，可以使用刚拆出的四位年份；
- 标题、中文标题、别名不得继续携带同一个末尾年份。

日文规范拉丁标题的优先级为：

1. Provider 明确给出的 `romanized_original_title`；
2. 原名仅含平假名、片假名和拉丁字符时的本地规则转写；
3. Provider 明确给出的 `official_english_title`。

含汉字的日文原名不能靠字符替换可靠得到读音，例如 `氷菓` 不能由
规则安全推导为 `Hyouka`。无已验证罗马字和官方英文名时仍必须返回
`canonical_title_unavailable`，不得让 AI 直接写入正式标题。

## AI binding 修复

每次 discovery 或 source supplement 的 AI 候选输出都先经过严格
fact binding 校验。第一次校验失败时：

- 写入 `search_binding status=invalid` 日志；
- 将错误码、原候选绑定和允许使用的事实重新发给同一候选编辑器；
- 使用 `stage=binding_repair` 只重试一次；
- 第二次仍失败时抛出 `candidate_binding_failed`。

一次 repair 不得扩大候选数量上限，不得放宽 fact ID、媒体类型、
锚点或重复绑定校验。

## 日志合同

每次计划至少保留以下结构化事件：

- `search_binding status=received`：阶段、候选 ID、锚点、角色和全部
  fact binding；
- `search_binding status=invalid|repairing|ok`：阶段、错误码和候选数；
- `search_supplement status=planned`：缺失 Provider，以及每个 Provider
  的结构化标题、年份、媒体类型查询；
- `search_metadata status=incomplete|ready`：候选 ID、Provider、缺失
  字段；
- `search_planning status=failed`：plan ID、错误码和 reason codes。

日志不得包含 API Key、Header、Token、Cookie 或未脱敏 URL。

## 用户交互

- Provider、候选层级、媒体类型、来源状态和 unresolved 状态全部显示
  中文产品文案。
- 候选选择按钮表示“选择并验证”，不能在严格元数据尚未形成时承诺
  已经开始片源检索。
- `fixed_link_read_failed` 可以显示“重试精确读取”。
- `metadata_incomplete`、`metadata_conflict` 和 hydration 阶段的
  `candidate_binding_failed` 属于确定性错误，不重复执行同一冻结链接；
  保留候选导航和退出入口。
- 缺失字段必须显示为“规范拉丁标题”“TVDB 剧集根条目”等用户可理解
  文案，不得显示内部字段名。

## 验收

- 豆瓣 `冰果 氷菓‎ (2012)` 规范化为标题 `冰果 氷菓` 和年份 `2012`。
- `冰果` 的补源计划可以向 TVDB 发送 `title=Hyouka, year=2012,
  media_type=series`，且只有 TVDB 返回事实后才能进入候选。
- 日文汉字标题在没有罗马字但有来源确认的官方英文名时可以形成严格
  拉丁标题；两者都缺失时继续失败。
- 首次重复 fact binding 可经一次 repair 成功；第二次失败时日志包含
  原阶段和具体 binding 错误码。
- 候选和错误 UI 不再出现上述内部英文枚举或字段名。

