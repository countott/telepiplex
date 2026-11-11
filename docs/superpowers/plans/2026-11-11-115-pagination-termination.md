# 115 Pagination Termination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修正已读满声明总数后仍探测尾页的确定性问题，保留完整性校验与云盘文件保护。

**Architecture:** 在 SDK 树枚举和 Rename 两处目录枚举中，完成该页验证、身份去重及必要递归后，以累计条数等于已声明 count 作为终止条件。没有 count 时继续读取，不能因短页推断结束。不添加重试，不改变移动和源目录删除的授权条件。

**Tech Stack:** Python、pytest、SDK 与独立 Feature 包。

**Spec:** 用户提交的五份诊断包及本次只读文件 ID/父目录/大小/SHA1 字段比对；证据记录于 `docs/audits/2026-11-11-115-pagination-diagnosis.md`。

## Global Constraints

- Mac 仅修改源码和本地测试；禁止 Git、发布和真实 115 账户文件操作。
- 明确区分已移动、未通过完整核验、空目录删除失败，不能把未记录的失败响应补写成事实。
- 保留未知 count 的连续分页、计数变化、缺页、超额、重复 ID、深度与容量保护。
- SDK 源码变化升至 2.2.2；五个正式 Feature 的精确 SDK 依赖与不可变包版本同步更新。Host 不变。
- 不修改历史审计或生成构建产物；交付列明本轮文件及实际验证结果。

## Review Focus

- 已知 count 的单页、多页、嵌套包装和最后一页缺失 count。
- 不含 count 的短页与假空页。
- 显式 continuation 与 count 矛盾，以及跨页 count 变化。
- 同 ID 重复、递归目录和大快照安全边界。
- 源 offset 0 矛盾与 provider 770004 不属于已证明修复的范围。

## Tasks

1. [x] 对照三个新 ZIP，核对移动前后稳定文件身份和最终报告；复现额外尾页请求。
2. [x] SDK：新增失败回归，修正 count 终止，运行树/快照定向验证。
3. [x] Rename：新增失败回归，修正目录核验及库存 count 终止，保留其他安全门槛。
4. [x] 同步 SDK/Feature 版本、构建示例、当前文档与版本断言。
5. [x] 完成 Feature 全量、Host/SDK 相关跨模块回归和最终只读审查，记录未解决限制。
