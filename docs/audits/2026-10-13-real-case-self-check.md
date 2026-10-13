# Telepiplex 真实案例自检

日期：2026-10-13（Asia/Shanghai）。本轮检查业务正确性、代码风险和提速空间；没有修复业务代码、修改配置、提升版本或发布。六套完整测试在补齐声明依赖后共 **2,650 passed、3 skipped、626 subtests passed**，额外案例仍确认 **7 个问题**。优先处理目录误删、兼容移动删源顺序和字幕季集冲突，再处理误拒和菜单合同。

结构化结果保存在 [自检数据](2026-10-13-real-case-self-check.json)。其中的故障注入是实际实现配合受控外部响应的复现，不表示已发生真实云盘数据丢失。

## 验证范围

当前源码版本：Host `3.9.2-host`、SDK `2.2.1`、download `2.2.0`、search `2.6.1`、rename `2.4.0`、sync `2.1.1`、caption `1.3.0`。这些是本地源码版本，不是生产安装版本。

| 验证层 | 本轮实际结果 | 可支持的结论 |
| --- | --- | --- |
| Host 与五个 Feature 全量测试 | 2,650 passed / 3 skipped / 626 subtests | 已覆盖的本地行为、RPC、重启、取消、幂等、合同和完整性回归通过 |
| Search 离线 corpus | 70 条中执行 8 条，8 条业务成功，62 条缺 fixture 而跳过 | 8 条真实业务实现回放，不能计作 70 条完成 |
| 公共元数据业务读取 | 7 条：4 条捕获投递成功，3 条安全拒绝 | Wikipedia/Wikidata 等公共身份读取及实际 Search 交互；片源为 fixture，投递由 AuditHost 捕获 |
| 免费字幕来源 | 双城之战、奇巧计程车 2 条实际检索、下载、解包及正文检查成功 | 已检查预算内的字幕匹配；未验证视频对白同步、翻译准确性或媒体写入 |
| Host/RPC 压力 | 200 条 / 并发 32，10 次故障全部恢复，0 失败 | Host 交接控制面与持久化，不代表真实下载吞吐 |
| Telegram 队列压力 | 正常、慢 busy 各 10 条，重复点击；两组正确性通过 | 实际 PTB 队列、handler、RPC 和 Host 实现，Telegram 及业务阶段为受控边界 |
| Caption 压力 | 500 请求 / 并发 24，500 matched，0 失败 | 实际引擎调度与缓存，字幕正文为合成样本；开启 tracemalloc |

真实 Telegram、Prowlarr、115、Plex 部署端到端未验证。本轮没有向这些服务发出媒体写入，也没有读取生产凭据或连接本项目 GitHub。

## 已确认问题

### F1 P1 目录响应矛盾时仍判空并删除

位置：[file_executor.py](../../features/rename/src/telepiplex_rename/file_executor.py) 第 1639–1665、1748 行；同类完整性缺口在第 997–1030 行。

对目录返回 `{"list": [], "count": 1, "offset": 0}` 时，`_fresh_directory_state` 忽略 count，返回 `empty`。真实 `cleanup_source_directories` 随后调用整个源目录删除；内存存储中尚有的 `notes.txt` 一并消失，结果仍为 `complete=true`。

`_complete_directory_items` 同样把 `count=2`、实际只返回 1 项的页面当作完整列表，可使冲突检查漏项；已有 SDK 完整树扫描器对同形状响应正确拒绝。

应将矛盾、不完整或偏移不符的响应归为读取失败，保留文件与目录；统一复用 SDK 的完整性判定。验收必须断言矛盾列表时删除次数为 0，并覆盖目标冲突、分页截断和负向响应。此缺口位于常规目录清理路径；本轮仅注入异常响应，未证明线上已经出现误删。

### F2 P1 兼容移动未验证复制目标就删除源文件

位置：[client.py](../../features/download/src/telepiplex_download/client.py) 第 973–986 行；调用方为 [file_executor.py](../../features/rename/src/telepiplex_rename/file_executor.py) 第 752–782 行。

`move_file_detailed` 收到 copy 成功响应就直接删源。真实 Open115Client、真实执行器和 HTTP 形状替身复现：关闭 native capability，copy 返回成功但目标未产生；执行器最终报告 `failed / target_missing_after_move`，此时源视频也已删除。

应在删源前读取目标并核实内容身份、SHA1、大小和预期路径，无法验证则保留源文件。该问题限于旧 capability 或 native 移动不可用时的兼容路径，默认 native 移动正常路径未触发；不据此断言真实 115 今天返回虚假成功。验收需包含成功响应但目标缺失、异步可见、内容不符、删源失败和取消。

### F3 P1 字幕季集字段覆盖明确发布名冲突

位置：[matching.py](../../features/caption/src/telepiplex_caption/matching.py) 第 187、200–204、271–274 行。

请求作品为 2020 年 `S01E02`，SubDL 来源结构化字段也为 `year=2020 / season=1 / episode=2`，但发布名明确为 `Example.2021.S02E03.WEB`。实际 SubDL 适配器仍返回候选；正文成员使用通用名称 `chi.srt` 时，完整 `CaptionEngine.retrieve()` 返回 `matched`、选择 1 条、没有 rejection。

结构化字段可以补缺，不能静默覆盖明确季集冲突。应保存各项证据并拒绝冲突候选；年份需区分作品首播年和季度播出年，不能将全部年份差异一律拒绝。验收应覆盖发布名冲突、通用成员名、明确单集成员名及季度年份的合法差异。本轮未向媒体库写入错集字幕。

### F4 P2 分辨率被误判为作品年份

位置：[matching.py](../../features/caption/src/telepiplex_caption/matching.py) 第 124–137、306–311 行。

请求 2020 年 `S01E02`，成员名 `Example.S01E02.1920x1080.chi.srt` 被提取出年份 1920，因 `document_year_mismatch` 拒绝；加上显式 `.2020.` 后才接受。年份正则只排除了相邻数字，未排除分辨率。

本轮真实 ODDTAXI 下载包也出现这一原因，但这些成员还有其他身份/季集拒绝原因，不能声称单独修年份就能采用。应先排除明确技术数字，再提取年份；保留数字片名、真实错误年份和季集身份校验。

### F5 P2 下划线分隔片源被误拒

位置：[release_gate.py](../../features/search/src/telepiplex_search/release_gate.py) 第 95–106 行。

确认同一 Fargo `S02E03`，固定来源仅改变分隔符，真实 Search `command/callback` 结果不同：`Fargo.S02E03.1080p.WEB-DL` 捕获投递 1 次；`Fargo_S02E03_1080p_WEB-DL` 返回 `no_eligible_release`、投递 0 次。

身份分词接受下划线，范围正则的 `\b` 却将下划线视为单词字符，导致季集未识别。应统一分隔符处理；验收覆盖整季、单集、范围、串联集数及下划线，继续拒绝错误季集。

### F6 P2 剧名中的 Special 和 Extras 被当作特别内容

位置：[release_gate.py](../../features/search/src/telepiplex_search/release_gate.py) 第 205–225 行。

正常已播库存和有效链接下，`Special.S01.1080p.WEB-DL`、`Extras.S01.1080p.WEB-DL`、`Special.Ops.S01.1080p.WEB-DL` 均先得到 `identity_match=True`，随后被 `unsupported_special_content` 拒绝；Fargo 对照通过。代码只为电影排除已匹配的作品标题部分，剧集仍检查整条标题。

应对电影与剧集一致地排除已确认标题，只检查发行尾部的特别内容标记，仍拒绝 Season 0、OVA、OAD 等。线上 Extras 本轮只确认身份 Q23826 并得到泛化的无片源结果；该在线结果未单独用作上述缺陷的证明。

### F7 P2 根作品选已完结季度跳过单集菜单

位置：[service.py](../../features/search/src/telepiplex_search/service.py) 第 3644–3658 行；合同依据 [Search README](../../features/search/README.md) 第 66、69 行。

只点击实际渲染按钮、使用同一套完整库存：`/s Fargo S02` 确认后显示“第二季 全季 / 第一集 / 第二集 / 第三集 / 返回”；`/s Fargo` 确认 2014 剧集后点击“第二季”，直接进入整季 `release_selection`。用户无法从这一路径继续选单集，与两入口遵循同一菜单合同的文档不一致。

应先进入季度菜单，再由用户选全季或单集。验收需覆盖已完结/连载季度、直接季度输入和根作品选择。单季根作品只显示“全剧（共 1 季）”是 README 明确规定，未作为新增问题；公网菜单样本缺可靠单集库存，菜单差异的证明来自受控库存下的真实交互实现。

## 业务规则与在线可用性

download 在自动交接前删除非视频文件（包括下载包字幕）及默认低于 100 MiB 的视频；阈值为 0 仍过滤非视频。存量 `/rename` 则保留未匹配文件。这是当前文档与测试确认的不同业务规则，本轮未当作缺陷，也未改变策略。

公共来源成功样本为 Fargo 1996、Westworld 1973、想见你 2022、千与千寻 2001。千与千寻冻结 Q155653，包含 AniList/豆瓣引用；审计配置没有启用 AniList adapter，本轮不能证明 AniList 在线调用或 Romaji 命名。

Fargo 全剧的模拟片源仅包含 2 季，无法覆盖真实库存；Fargo `S02E03` 因公共来源未得到可验证库存返回 `metadata_not_verified`。安全拒绝没有算作业务成功，也没有直接当作代码错误。生产季集可用性还需实际 TMDB/TVDB 配置、来源响应及同会话日志验证。

字幕实际样本：双城之战 S01E01 在 14.266 秒选中 SubHD 简中双语 ASS（466 cues）；奇巧计程车 S01E01 在 11.981 秒选中迅雷繁中 ASS（1,269 cues）。检索预算最多检查 6 个候选，两例均 `timing_verified=false`，带有视频时序、年份和发布版本未验证提示，不能据此证明与实际视频同步。

sync 的全套测试覆盖手动扫描、恢复中断、取消边界、退避、批量路径索引与同作品元数据复用，本轮未确认新增缺陷。

## 性能测量与改进顺序

以下受控耗时都是 Mac 本地实现加模拟网络延时，不能换算线上吞吐。仅临时实验修改批量或 SQLite 索引，生产源码和配置没有变更。

| 方向 | 当前测量 | 可评估的改动 | 状态与限制 |
| --- | --- | --- | --- |
| Rename 批量移动 | 1,000 文件，batch 32：32 次 move、130 次 list、1.4220 秒；batch 100：10 次 move、42 次 list、0.9268 秒 | 使用已有上限，评估更大或动态批量 | 单次对照约快 35%，请求量下降有明确证据；须先补列表完整性，保留每批写前/写后验证 |
| Caption 搜索与下载阶段 | 快来源 0.0153 秒出候选，慢来源 0.3103 秒结束，首次下载到 0.3110 秒才开始；总 0.6191 秒 | 将候选下载检查与其他来源搜索重叠 | 该延时场景理论约 0.32 秒，未实现或实测改后收益；须保留来源公平性与候选预算 |
| Caption 重复正文处理 | 160 相同请求 / 并发 24，检索和下载各 1 次，解包/正文检查各 160 次；无 tracemalloc 总 0.5588 秒 | 对内容摘要及完整质量上下文作有界复用 | 重复计算已经证实；小正文样本总成本不高，收益随正文大小变化，不能缓存整份 owner 任务 |
| Host 历史失败查询 | 100k 历史 failure、待投影 0 条，仍全表扫描：700,017 VM steps，中位 4.174 ms；临时 partial index 后 18 steps、0.007 ms | 为待投影 dead letter 加匹配 partial index | 各 30 次；小历史 1k 仅约 0.046 ms，属于低优先级增长治理，不是主要端到端瓶颈 |

Telegram 正常 50 ms 和 busy 500 ms 两组各 10 条：前台完成中位从 747 ms 到 1,157 ms，点击后 Feature RPC 中位仍约 2.6/2.0 ms；慢 busy 仍通过渲染锁影响后续展示。两组任务与 FD 最终增量均为 0，按钮封存正确。计入重复点击和恢复共 12 次 API/任务，不能与单次点击的 9–10 次预算直接相比。只有在生产日志确认长尾后，才评估合并过时投影，不能为了省等待破坏最终显示与按钮清理。

Search 已有发现缓存有效：Fargo、Fargo S02、想见你冷发现约 7.569/2.734/8.468 秒，再次命中约 1.524/0.813/1.734 ms；确认阶段约 4.868/5.584/5.354 秒。门禁处理 1k/5k/20k 条约 20/100/420 ms，近似线性。token memo 原型没有稳定收益，不建议泛加缓存。优先取得确认阶段各来源耗时和请求数，再评估独立读取并发。

SDK 小目录扫描有额外空页探测：21 个目录、40 节点，共 42 次列表，其中 21 次空页。是否可用已验证 count 减少探测需单独审查；F1 已证明响应可能自相矛盾，不能只信 count 或直接省去下载清理后的第二次整树复验。

## 本地验证命令与结果

Python：`/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`。字幕额外依赖位于隔离目录 `/tmp/telepiplex-audit-caption-deps`。

初次按默认 runtime 运行，Host 为 728 passed / 2 failed / 1 skipped，Caption 为 423 passed / 44 failed / 6 skipped；声明的 OpenCC/7z 等依赖未在默认路径中。补齐后全套通过，生产依赖已在 caption 的 pyproject 和 requirements 中声明。此前存在的 `/tmp/telepiplex-caption-test-deps` 目录此次没有实际模块，不能作为有效环境。

| 测试套件 | Passed | Skipped | Subtests | 本轮耗时 |
| --- | ---: | ---: | ---: | ---: |
| Host | 730 | 1 | 333 | 108.08 s |
| Download | 180 | 0 | 33 | 2.55 s |
| Search | 694 | 2 | 152 | 46.62 s |
| Rename | 413 | 0 | 28 | 6.87 s |
| Sync | 160 | 0 | 80 | 5.36 s |
| Caption | 473 | 0 | 0 | 8.76 s |

实际完整测试分别执行如下；Feature 各自在自己的目录运行，避免同名 tests 包冲突：

```bash
cd /Users/young/Documents/telepiplex
PY=/Users/young/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:.:sdk/src \
  "$PY" -m pytest -q -p no:cacheprovider --tb=short tests

for module in download search rename sync; do
  (
    cd "features/$module"
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src \
      "$PY" -m pytest -q -p no:cacheprovider tests
  )
done

(
  cd features/caption
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:src:../../sdk/src \
    "$PY" -m pytest -q -p no:cacheprovider tests
)
```

额外实际执行入口：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_operation_pipeline.py --pipelines 200 --concurrency 32 --milestone-faults 10

# 以下两组各执行一次，第二组额外传 --busy-latency-ms 500。
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:sdk/src "$PY" \
  tools/pressure_telegram_pipeline.py --pipelines 10 --concurrency 1 \
  --frontend-mode queue --telegram-latency-ms 50 --duplicate-clicks 2 --timeout-seconds 60

# 在 features/caption 目录。
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:src:../../sdk/src "$PY" \
  tools/live_provider_check.py --providers assrt,subhd,xunlei --limit 2 --candidates 6 \
  --output /tmp/telepiplex-audit-caption-live.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/telepiplex-audit-caption-deps:src:../../sdk/src "$PY" \
  tools/pressure_test.py --requests 500 --concurrency 24 \
  --output /tmp/telepiplex-audit-caption-pressure.json
```

临时独立复现保存在 `/tmp/telepiplex-storage-audit-20261013.py`、`telepiplex-storage-more-audit-20261013.py`、`telepiplex-caption-repro.py`、`telepiplex-caption-performance.py`、`telepiplex-search-boundary-repro.py`、`telepiplex-search-release-repro.py`，以及 `/tmp/telepiplex-audit-20261013/event_cost.py`。运行时分别提供对应 Feature 的 `src`、SDK `src` 和字幕依赖路径；Caption 两个脚本还使用其 `tests` fixture。核心复现结果、在线样本与性能数据已归档到本报告旁的 JSON；临时脚本路径可能被系统清理。

Search 另运行 `tools/run_live_pipeline_audit.py` 的离线 corpus、4 条公共案例，再用独立脚本追加 3 条公共案例；还检查了根作品/季度公共菜单与 16 次离线场景诊断。部分场景结果不符审计器预期：缺库存的 `scope_changed`、fixture 日期归一化与既有剧集年份容忍，经真实渲染按钮和现行规则复核后未作为生产缺陷；未把它们写成全部通过。

## 文件与交付

本轮仅新增两个审计文件，没有修改、删除或重命名已有项目文件：

| 新增文件 | 用途 |
| --- | --- |
| `docs/audits/2026-10-13-real-case-self-check.md` | 本报告：问题优先级、复现、验证范围、性能测量和修复验收方向 |
| `docs/audits/2026-10-13-real-case-self-check.json` | 可复核的测试汇总、7 项问题、实际样本、故障注入结果及性能数据 |

实际边界检查通过：`.git` 与 `.worktrees` 不存在，`.stfolder` 保留。没有执行 Git、标签、PR、发布、Unraid 命令或远端媒体修改。等待 Syncthing 显示 **Up to Date / 最新**，审计文件同步到 `/mnt/user/archives/life hacker/telepiplex`；后续修复及任何发布按用户指定流程进行。
