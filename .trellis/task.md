# Trellis 任务看板

> 由 trellisx-workspace 维护 (经 trellisx-taskmd.py); task 生命周期节点后及时更新。

| ID | 名称 | 描述 | 状态 | 阶段 | 进度 | worktree |
| --- | --- | --- | --- | --- | --- | --- |
| push-merge-pushc-all | 重命名 push/merge 入口为下划线全称风格并合并 pushc_all | — | 已完成 | 收尾 | 100% | — |
| readme | 精简 README 为纯使用手册 | — | 已完成 | 收尾 | 100% | — |
| rspress-docs | 建 Rspress 文档站 docs/ 支持中英多语言 | — | 已完成 | 收尾 | 100% | — |
| push | push_* 批量结束输出成功失败跳过汇总 | — | 已完成 | 收尾 | 100% | — |
| rich-beautify | rich 美化批量脚本输出 | — | 规划中 | 规划 | 0% | — |

## Worktree ↔ Task 映射

> 每个活跃 worktree 登记映射到的 task (一对多: 同 task 拆多 subagent 各占一行);
> 无映射的 worktree 由 WorktreeCreate hook 提醒补登。

| worktree | task | 创建源 |
| --- | --- | --- |
| parallel-batch | 批量操作并行异步化 | — | 规划中 | 规划 | 0% | — |
