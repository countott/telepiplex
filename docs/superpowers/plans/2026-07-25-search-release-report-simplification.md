# Search Release Report Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将增量 Prowlarr 报告压缩为两行摘要与每片源一行，同时保留稳定选择和完整纯规格标签。

**Architecture:** 只修改 `release_report.py` 的展示层，不改变门禁、排名、Indexer 并发、稳定片源 ID 或提交合同。报告继续消费现有 `gate`、`ranked` 与 `indexer_summary`，但只公开结果数、Indexer 完成数和异常数。

**Tech Stack:** Python 3.12、unittest/pytest、Telegram operation 文本合同。

## Global Constraints

- Search 版本保持 `1.0.5`。
- 正向结果严格两行摘要，随后每个片源一行，最多 12 行。
- 真实错误详情保留在日志和内部状态，Telegram 只显示异常数量。
- Mac 本地不执行 Git；完成后经 Syncthing 交给 Unraid。

---

### Task 1: Simplify the release report

**Files:**

- Modify: `features/search/tests/test_release_report.py`
- Modify: `features/search/src/telepiplex_search/release_report.py`
- Modify: `features/search/README.md`

**Interfaces:**

- Consumes: `format_release_report(query: str, gate, ranked: list[dict], indexer_summary: dict) -> str`
- Produces: 相同函数签名，输出两行摘要与最多 12 行紧凑片源。

- [x] **Step 1: Write the failing report contract tests**

  使用固定 fixture 断言首两行分别为：
  `🔍 Constantine 2005` 与
  `搜索结果 12｜索引器完成 1/3｜异常 2`；断言片源行为
  `① 128分｜整片｜4K / REMUX / HEVC｜做种46｜~35G｜标题…`。
  同时断言 UI 不再包含 `门禁`、`来源`、Indexer 名称和 `(+N)` 分项。

- [x] **Step 2: Run the focused test and verify RED**

  Run:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:../../sdk/src python -m pytest -q -p no:cacheprovider tests/test_release_report.py`

  Expected: FAIL because the current report still emits Prowlarr、门禁、来源和分项分值。

- [x] **Step 3: Implement the minimal formatter change**

  将 `2160p` 映射为 `4K`，规格标签用 ` / ` 连接，大小转换为四舍五入约数
  `~<N>G`；删除公开的 Indexer 与分项分值，并保留标题末尾截断。

- [x] **Step 4: Verify GREEN and regressions**

  先运行 `tests/test_release_report.py`，再运行 Search Feature 全测试与根项目测试。

- [x] **Step 5: Refresh the local Search 1.0.5 artifact**

  构建 `/tmp/search-1.0.5.tpx` 并执行 `unzip -t`；不执行 Git 或发布。
