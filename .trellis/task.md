# Trellis 任务看板

> 由 trellisx-workspace 维护 (经 trellisx-taskmd.py); task 生命周期节点后及时更新。

| ID | 名称 | 描述 | 状态 | 阶段 | 进度 | worktree |
| --- | --- | --- | --- | --- | --- | --- |
| push-merge-pushc-all | 重命名 push/merge 入口为下划线全称风格并合并 pushc_all | — | 已完成 | 收尾 | 100% | — |
| readme | 精简 README 为纯使用手册 | — | 已完成 | 收尾 | 100% | — |
| rspress-docs | 建 Rspress 文档站 docs/ 支持中英多语言 | — | 已完成 | 收尾 | 100% | — |
| push | push_* 批量结束输出成功失败跳过汇总 | — | 已完成 | 收尾 | 100% | — |
| rich-beautify | rich 美化批量脚本输出 | — | 已完成 | 收尾 | 100% | — |
| parallel-batch | 批量操作并行异步化 | — | 已完成 | 收尾 | 100% | — |
| batch-error-detail | 修复批量失败 detail 提取关键错误行 | — | 已完成 | 收尾 | 100% | — |
| i18n-un-languages | README+docs 多语言支持(UN 6 官方语言) | — | 已完成 | 收尾 | 100% | — |
| sync-branch-fetch-all | sync_branch + fetch_all 命名统一 | — | 已完成 | 收尾 | 100% | — |
| delete-branch-delete-branch-remote | delete_branch + delete_branch_remote 删分支脚本 | — | 已完成 | 收尾 | 100% | — |
| rich-polish | rich 输出美化升级 + 收编裸 print | — | 已完成 | 收尾 | 100% | — |
| rich-deps-color | rich 依赖缺失修复 + 全脚本颜色深度验证 | — | 规划中 | 规划 | 0% | — |
| push-branch-pull-ff-only-push | push_branch: 批量推送当前分支到远端（先 pull --ff-only 再 push） | — | 已完成 | 收尾 | 100% | — |
| spr-squash-pr | squash_pr squash PR 脚本 | 建 <source>_pr 分支压单 commit 对接 prc 开 PR | 已完成 | 收尾 | 100% | — |
| bin-readme-claude | 同步 bin 脚本到文档(README+CLAUDE) | — | 已完成 | 收尾 | 100% | — |
| checkwork-verbose | checkwork 增强多语言框架检测与 verbose 进度 | — | 已完成 | 收尾 | 100% | — |
| ruff-lint-102 | 修复全仓 ruff lint (102 项) | — | 已完成 | 收尾 | 100% | — |
| exec-run-timeout | exec.run 加默认 timeout 防命令挂起 | — | 已完成 | 收尾 | 100% | — |
| batch-git | batch_git 失败时输出具体错误原因 | — | 已完成 | 收尾 | 100% | — |

## Worktree ↔ Task 映射

> 每个活跃 worktree 登记映射到的 task (一对多: 同 task 拆多 subagent 各占一行);
> 无映射的 worktree 由 WorktreeCreate hook 提醒补登。

| worktree | task | 创建源 |
| --- | --- | --- |
| batch-git-fail-skip-stash | batch_git 脏仓库 fail 而非 skip/stash | — | 进行中 | 规划 | 0% | — |
| /Users/luoxin/persons/scripts/.worktrees/07-09-batch-git-fail-skip-stash | 07-09-batch-git-fail-skip-stash | trellisx-start |
