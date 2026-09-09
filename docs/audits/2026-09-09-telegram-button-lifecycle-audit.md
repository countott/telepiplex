# telepiplex 消息推进后旧按钮残留调查

日期：2026-09-09。范围：附件日志、当前 Mac 源码、离线故障复现。本文是调查结果与修复建议，未实施业务修复。

结论：寡妇湾日志证明了“进入处理状态后，旧范围选择正文和按钮又被写回”。当前实现还存在旧消息控制按钮未绑定消息段、旧消息清理失败被吞掉、迟到点击反馈绕过投影缓存等缺口。应补全现有 Host 消息段生命周期，统一按钮有效性、渲染和清理确认，而不是逐个按钮补删除调用。

## 1. 日志事实与边界

寡妇湾下载位于 `/Users/young/Downloads/20260908T170308+0800-06E8F490BAF5.zip`。另两包 `20260908T182227+0800-24F46CB8E569.zip`、`20260909T111524+0800-3F7E8A862C9E.zip` 未找到寡妇湾下载交互。后两包次日网络异常、getUpdates Conflict 不能归为此次按钮问题的原因。

三包启动日志均记录 Host revision `c75e70cbc5567f38309c731551e42688a1a5b938`，但 `host_version=null`。没有访问 Git，也没有据此声称日志运行版本与本地源码完全一致。此次 Feature 版本为 search 2.1.2、download 2.1.0、rename 2.1.0。

主 operation 为 `c4e0357189b84aa49c887005a78e5c2f`，search session 为 `3412de18e1`。下表行号均指主 ZIP 内的 `telepiplex.machine.jsonl`，时间为北京时间。

| 时间 | 消息与事件 | 行号 |
|---|---|---|
| 17:07:59.762 | `/s 寡妇湾` | 78 |
| 17:08:00.809 | 2213：识别进度，带取消任务按钮 | 84 |
| 17:08:17.213 | 2214：候选照片 | 97 |
| 17:08:39.833 | 2214：全剧、返回、退出按钮 | 125 |
| 17:08:41.511 | 用户点击全剧 | 126 |
| 17:08:42.556 | 2214：正在确认媒体身份 | 139 |
| 17:08:42.596 | 2214：又显示旧范围选择正文及全剧、返回、退出按钮 | 140 |
| 17:08:43.196 | 2214：身份摘要，显式空键盘 | 141 |
| 17:08:43.216 | identity 段封口成功 | 142 |
| 17:08:45.669 | 2215：片源列表 | 183 |
| 17:10:03.191 | 用户选择片源 | 244 |
| 17:10:06.833 / 06.852 | 2215：提交下载、显式空键盘；search 段封口成功 | 258–259 |
| 17:10:08.503 | 2216：下载消息，带取消按钮 | 291 |
| 17:10:22.481 / 22.502 | 2216：下载完成、显式空键盘；download 段封口成功 | 323–324 |
| 17:10:23.161 | 2217：整理消息，带取消按钮 | 378 |
| 17:10:38.556 / 38.577 | 2217：已整理寡妇湾、显式空键盘；rename 段封口成功 | 794–795 |

第 128、131 行显示，全剧 callback 返回时仍携带 `awaiting_input / series_scope / revision=3`；第 133–134 行随后记录 Host 接受 `running / identity_confirmation / revision=4`。第 140 行重新展示的 Feature 按钮已经换成 `~1.3~`，说明旧业务界面被重新赋予了新的按钮代次。约 0.60 秒后才由最终身份投影清空。

不能据附件证明 2214–2217 在最终封口后仍永久残留按钮；它们都有空键盘投递记录。2213 没有后续内容编辑记录，也不能证明它未被删除：当前 diagnostics 没有记录 `delete_message` 和 `edit_message_reply_markup`。主包没有相应删除失败警告。日志证明的是服务端 API 投递顺序，没有用户客户端截图或持续界面观察。

## 2. 已定位的实现缺口

### 2.1 后台任务启动返回旧可交互快照

`features/search/src/telepiplex_search/service.py:3423` 的全剧选择进入 `_start_selected_release`，再到 `:1856` 的 `_start_release_search_task`。这里先 spawn，再原样返回当前 operation；没有在返回前推进处理状态、撤销范围按钮。新状态由后台任务稍后上报。

`app/handlers/plugin_handler.py:661` 在处理 callback 结果前释放按钮 claim；`:899` 可渲染返回的 operation，`:673` 的 finally 又会尝试重绘释放后的消息。因此“RPC 已返回”可能被当作“可以重新开放当前界面”，即使异步业务已经开始。已经发往 Telegram 的旧编辑也不能仅靠后续 revision 校验撤销。

这与日志里的旧 revision 响应、范围按钮回写顺序一致。应在业务入口同步消费当前选择并返回新处理状态；Host 应在接受返回状态后，根据交互是否仍有效决定恢复按钮，不能仅以 RPC 结束为恢复依据。

### 2.2 点击处理反馈绕过统一渲染，缓存无法识别迟到覆盖

`app/handlers/interaction_handler.py:1518` 的 busy 编辑由 `:1546` 单独创建任务发送，未经过 operation renderer 的串行写入路径。它结束后在 `:1685` 调用 reconcile，但 `:1877` 会依据已记录的 revision/hash 跳过编辑。

`app/runtime/interaction_coordinator.py:1639` 的 release 确实清空了 rendered hash，但只发生在释放时。仍有以下交错：

1. busy 编辑已发起但延迟完成。
2. callback release 清空缓存。
3. 正式 renderer 写入当前界面，并将 rendered hash 标为最新。
4. 迟到 busy 编辑覆盖当前界面。
5. reconcile 发现数据库 hash 已相同，不发送修复请求。

通过真实 SQLite coordinator、renderer、python-telegram-bot 22.3 的 BaseRequest/RequestData，以及受控传输替身复现：text/photo 两种消息最终均停留在 busy 文案，数据库却为 `callback_state=idle`、hash 相同，修复请求为 0。分别模拟省略 markup 时保留或清除按钮，两种策略都能复现正文失配，因此该竞态不依赖服务端对省略 markup 的解释。

该实验直接证明投影一致性缺陷，不证明此次线上永久残留来自此交错。

### 2.3 Host 控制按钮未绑定当前消息与代次

Feature 按钮已经编码 segment generation 与 callback generation。Host 自动添加的退出、取消等按钮在 `app/handlers/interaction_handler.py:1365` 只包含 action 和 operation ID。

`:1066` 对同 operation 的控制按钮直接放行；`:1152` 的控制 handler 校验用户、任务终态和当前 action，但没有校验点击来源 message ID、segment 或按钮代次。

离线复现：当前 download 段绑定消息 222，从旧消息 111 点击同 operation 的取消按钮，gate 放行，真实控制 handler 向 download 分发了一次 `operation.control`。

这说明旧控制按钮不一定“其实无效”：任务未结束且 action 仍匹配时，它可以控制后续阶段。终态校验仍会挡住已结束任务，不能据此声称能够重启已完成下载。修复应明确历史阶段卡片的按钮失效，若需要任务级长期控制，应另有明确的有效控制入口。

### 2.4 清理失败不保留待办，日志可能误报成功

`app/handlers/interaction_handler.py:2231` 的 `_discard_replaced_segment_message` 先删旧文本，失败后尝试清键盘。第二次失败被吞掉，随后仍打印“已清理其按钮”。旧消息 ID 已被 promotion 替换，当前段不再保有待清理旧消息的持久记录。legacy `_clear_message_keyboard`（`:2444`）也吞掉错误。

离线注入删除和清键盘同时失败，函数正常返回，且日志确实声称“已清理其按钮”。这是一条能够留下旧消息按钮且没有持续修复保证的路径，但附件未证明本次触发了双失败。

### 2.5 失效点击与日志尚未形成修复闭环

`app/handlers/interaction_handler.py:1104` 对被拒绝的编码回调只回答“当前任务进行中”，不修复触发消息；无 active operation 时，编码 callback 也可能在 dispatch 校验处静默返回。终态控制按钮调用 render 时针对当前 operation 投影，不保证清除点击的那张历史卡片。

应区分“旧消息”和“当前消息旧代次”：前者定向清除历史消息按钮，后者恢复当前有效键盘，不能直接清掉同一消息上已经更新的新按钮。拒绝动作、清理意图、清理结果应记录同一组消息身份。

`app/runtime/telegram_diagnostics.py:62` 起没有覆盖删除消息、仅编辑键盘的 API；投递日志记录请求的 markup，不能将无 markup 字段直接视为已经确认清空。

## 3. 现有正确机制应继续保留

- Host 已有持久 segment、owner 校验、callback generation、原子 claim 和按 operation 串行 renderer，无需重建整套任务系统。
- `app/handlers/interaction_handler.py:1903` 已在正式 renderer 无按钮时发送 `{"inline_keyboard": []}`。
- `:1929` 已处理编辑在途时收到 seal 的竞态：若刚完成的编辑包含按钮，先清除再完成封口。9 月 5 日审计指出的这一旧问题在当前源码已有对应处理，不能再次当作未修复根因。
- search 片源选择 `service.py:1991` 起会同步冻结选择并清空 details，可以作为其他选择入口的参考。
- 当前 Host 对非当前 Feature 按钮、终态 operation 已有防护。直接调用 Feature fixture 可以重放旧 confirm，但这没有证明可以穿透真实 Host gate；不作为线上重复下载证据。

## 4. 根除方案与验收条件

建议按以下顺序补全现有机制：

1. **先统一交互有效性。** 所有按钮，包括 Host 退出/取消，绑定 operation、segment、源 message 和 interaction generation；接受点击时原子消费。search 全剧确认等入口在 spawn 前推进业务状态、去掉旧 keyboard。callback 返回后先接受状态，再决定是否恢复交互；重试需要显式产生有效的新代次。
2. **统一消息写入。** busy、后台 report、callback 返回、seal、错误恢复共用一个投影写入路径。`answerCallbackQuery` 仍可立即异步回答；Feature RPC 不需要等待 Telegram 编辑。正文、媒体、按钮、busy/idle 和交互代次共同构成目标投影。迟到请求完成后必须重新比较实际写入版本与当前目标，不能被仅业务内容的缓存跳过。
3. **持久记录旧消息清理。** 在替换游标、封口、取消、过期或终态时登记待清理的已知 message ID。清理成功或明确消息已不存在才完成；网络失败保留任务并退避重试，重启继续。新按钮启用前应满足旧交互已失效；不可把清理失败写成成功。无法编辑的消息应保留明确失败状态，旧回调仍不得执行。
4. **修复失效点击。** 已知旧卡片清除自身按钮并提示已进入后续步骤；当前卡片的旧代次点击触发最新投影修复。只处理有归属证据的消息，不扫描或猜测用户全部历史聊天。
5. **补齐可观测性和故障测试。** 记录操作、段、消息、交互代次、按钮数量、意图/成功/失败/重试，以及 API 返回确认。尤其覆盖 `edit_message_reply_markup`、`delete_message`。把“进入下一步后旧按钮不重新出现”作为业务流验收，而不是只检查 Python 调用了 None。

验收至少包括：全剧选择→身份封口→片源→下载→整理；双击及旧卡片点击；同消息旧代次；取消/过期；Telegram 慢请求、编辑失败、删除与清键盘双失败；业务完成但清理失败后重启；迟到 busy 在最终投影之后到达。

必须分别断言：业务副作用只执行一次；当前消息显示最新正文和有效按钮；历史消息没有可执行交互；清理失败有持久待办；晚到任务不能恢复旧按钮。不要以“测试绿”替代这些具体性质。

## 5. 实际验证

本轮 Host 定向测试：

```bash
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider \
  tests/test_interaction_handler.py tests/test_interaction_coordinator.py \
  tests/test_plugin_handler.py tests/test_telegram_diagnostics.py
```

结果：**236 passed，40 subtests passed，4.46 秒**。这是现有回归通过，不代表本轮发现的问题已经修复。

search 定向测试由并行调查执行：

```bash
cd /Users/young/Documents/telepiplex/features/search
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  -m pytest -q -p no:cacheprovider tests/test_feature_service.py \
  -k 'selecting_partial_release_freezes or exiting_partial_results or preseal_cancellation or unresolvable_release or first_wave_incremental_selection'
```

结果：**6 passed，142 deselected**。

传输层与迟到 busy 复现脚本为本机临时文件 `/tmp/telepiplex_wire_race_audit.py`，主调查者已审读并再次运行：

```bash
cd /Users/young/Documents/telepiplex
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src \
  /Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \
  /tmp/telepiplex_wire_race_audit.py
```

输出 `ALL_LOCAL_ASSERTIONS_PASSED; NO_NETWORK_REQUESTS`：表示缺陷复现断言成立，不表示修复通过。四个 text/photo × 省略键盘策略场景均 `db_hash_equal=true`、`repair_requests=0`，可见正文仍为迟到 busy。

真实 PTB 请求构造还确认：`None` 不输出 reply_markup 字段；`InlineKeyboardMarkup([])` 在 PTB 22.3 输出 `{}`；原始字典 `{"inline_keyboard": []}` 保留显式空数组。但当前 [Telegram 官方 Bot API 源码的 get_reply_markup](https://github.com/tdlib/telegram-bot-api/blob/master/telegram-bot-api/Client.cpp#L9906-L9972) 将这三种情况都解析为 null；[TDLib editMessageText 文档](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1edit_message_text.html) 也将 null 表达为没有新键盘。因此不能把“None 省略所以保留旧按钮”列为根因，更不能把替换 None 当作根治。前述双策略实验中的 preserve 只是敏感性测试，不代表官方服务端行为。

另外运行了只读源码配合现有测试 fixture 的内存复现，确认旧消息取消仍分发一次、删除与清理双失败正常返回且误报成功。没有真实 Telegram、115、Prowlarr 或文件整理操作。没有执行完整五 Feature 测试，也没有验证线上修复。

边界检查 `test ! -e .git && test ! -e .worktrees && test -d .stfolder` 返回 0。

## 6. 文件交付

项目内仅新增本报告 `docs/audits/2026-09-09-telegram-button-lifecycle-audit.md`，用于保留日志证据、根因、修复范围与验收要求。未修改、删除或重命名业务源码和测试。

临时复现文件只在 `/tmp`，不作为 Syncthing 交付物。等待 Syncthing 显示 `Up to Date / 最新` 后，本报告同步至 Unraid `/mnt/user/archives/life hacker/telepiplex`。未进行 Git、发布或 Unraid 操作。
