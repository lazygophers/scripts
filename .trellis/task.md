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
| spr-squash-pr | spr squash PR 脚本 | — | 已完成 | 收尾 | 100 | — |

## Worktree ↔ Task 映射

> 每个活跃 worktree 登记映射到的 task (一对多: 同 task 拆多 subagent 各占一行);
> 无映射的 worktree 由 WorktreeCreate hook 提醒补登。

| worktree | task | 创建源 |
| --- | --- | --- |

## ⚠ 待人工修正 (无法自动归类的行)

> 下列行 lint 不合规且无法机械归类 (列数异常且非主表/映射行形态), 已停泊于此防丢失;
> 请人工核对后改回主表或映射区, 或删除。修正后本块应清空。

