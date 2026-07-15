---
created: 2026-06-25
authored-by: trellisx-apply
---

# trellisx worktree + subtask 约定

何时被读: trellis task 实施时 (sub-agent dispatch 注入)
谁读: main / 执行者 agent
不遵守的代价: 并发 task 互相冲突 / 收尾丢提交

## worktree 隔离 (隔离单位 = task, 目的 = 防并发多 task 互相冲突)

- **隔离单位是 task, 不是单次写盘**: task.py start 后自动建**本 task 的** worktree (trellis 生命周期 hook after_start 自适应 3 布局: .trellis 同级 git / 微服务子目录 sparse / 多子仓读 task package 定位子仓 git); archive 触发 after_archive 销毁
- **默认一个 task 一个 worktree**: 本 task 全部执行 (trellis-implement 及其 subagent) 都在这个 worktree 内进行, 主工作区保持干净
- task.py finish 后 after_finish hook **自动收尾** (commit→merge --no-ff→archive→销 worktree), 无需手动跑收尾脚本; 合并冲突则 abort + finish 打 WARN, 转手动
- 多子仓 (.trellis 非 git 根, 子仓在下层如 go/node): task 须先 task.py set-scope <子仓> 标注, hook 才能定位
- main **不直接写源码**, 实施派 `trellis-implement`; 执行都在 task worktree 内
- subtask 默认**共享本 task 的 worktree** (文件集不相交即可并行); **仅当并行 subtask 会互相冲突时**才给它们各开 `isolation:worktree` 子 worktree, 此时 finish 经 task↔worktree 映射先合并各子分支再 archive (`trellisx-finish.py` 已支持)
- task archive 时 worktree 干净 → 自动销毁; 脏 → 警告先合并

## subtask 拆分 + 异步并行

- 判定跟随 trellis 原生 parent/child 语义: 本请求含**多个独立可验收交付**才拆 child task (`task.py create --parent`), 不看数量; 单一交付 → 轻量单 task inline
- PRD 调度图显式标并行组 (无依赖 subtask 同批)
- 执行: 实施统一经 `trellis-implement` (main 派之, 不直接派/直写)。trellis-implement 在**本 task 的 worktree 内**对无依赖 subtask 一次性并行派 subagent (真并行, 默认共享 task worktree; 仅冲突型并行 subtask 才各开子 worktree), 禁串行; 单 subtask 时 trellis-implement 内联直做
- parent-child 用 trellis 原生 `task.py add-subtask`
