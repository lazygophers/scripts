# PRD: finish-work 集成 neat-freak 文档清理

## 背景

当前 `trellis:finish-work` 仅处理任务归档和 session 日志，不涉及文档/记忆同步。
neat-freak 作为独立 skill 需手动触发，容易被遗忘。

## 目标

在 finish-work 流程中自动包含 neat-freak 文档清理步骤，确保每次收尾时 CLAUDE.md、docs、memory 均与代码状态对齐。

## 需求

### 1. 修改 finish-work SKILL.md

在 Step 2（sanity check）和 Step 3（archive task）之间插入 neat-freak 步骤：

- 调用 neat-freak skill 清理文档/记忆
- neat-freak 产生文件变更时，自动 commit：`chore: sync docs and memory`
- neat-freak 无变更时跳过 commit

### 2. 不修改 neat-freak skill 本身

neat-freak 保持独立 skill，finish-work 通过引用方式调用。

## 验收标准

- `finish-work` 执行时自动触发 neat-freak 清理
- neat-freak 产生的文档变更被正确 commit
- 原有 archive + journal 流程不受影响
