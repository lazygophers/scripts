# PRD：Rich 美化批量脚本输出

## 背景

`lib/ui.py` 已有 `Reporter`（封装 Rich Console，带 fallback）。多数 lib/ 已用。但批量类脚本输出风格不一：
- `print_summary`（batch_git.py:61）刚改成列表式（push task），用 Reporter.rule/info/ok/err。
- `print_repo_list`（batch_git.py:55）用 `r.info`。
- 批量执行过程（每仓库 push/merge）无进度反馈，无统一状态图标。
- 部分入口（bin/inject 等）裸 print，非本 task scope。

用户要"使用 rich 美化所有的输出"——本 task 收窄到**批量类脚本**（push_*/merge_*/git_fetch_all/find_git_repos），统一 rich 风格。

## 目标

1. 批量类脚本输出统一 rich 风格：色（绿成功/红失败/灰跳过/青信息）+ rule 标题 + 图标（✔✖⏭•）。
2. 批量长操作（push/merge 各仓库）加进度反馈（spinner 或逐项状态行）。
3. 汇总区可用表格呈现（总计/成功/跳过/失败计数）+ 列表明细（项目名）。
4. 复用 `lib/ui.py` Reporter，不引入新依赖。

## 需求规格

### 1. 统一色 + 图标规范

| 状态 | 色 | 图标 | 用途 |
|---|---|---|---|
| 成功 | green | ✔ | 操作成功、成功段标题 |
| 失败 | red | ✖ | 操作失败、失败段标题、错误 |
| 跳过 | dim | ⏭ | 跳过（汇总计数用，不列明细） |
| 信息 | cyan | ℹ/• | 进度、普通信息、bullet |
| 标题 | blue | — | rule 分隔线 |

### 2. 批量执行过程进度

`run_batch`（batch_git.py）执行每仓库操作时，输出逐项状态行：
- 开始：`• <repo_name> ...`（dim 或 spinner）
- 完成：`✔ <repo_name>` / `✖ <repo_name> — <detail>` / `⏭ <repo_name> — <detail>`

或用 Rich Progress（spinner + 文本）包裹批量循环，逐项 print 状态。读 `lib/ui.py` 确认 Reporter 是否暴露 Progress；无则逐项 print（简单优先）。

### 3. 汇总区

保留 push task 的列表式（rule + 总计单行 + 成功段 + 失败段）。**可选**增强：总计行改用 Rich 表格（`仓库总数/✔成功/⏭跳过/✖失败` 4 行 2 列）——若用户要表格。当前 PRD 定：**总计单行保留**（push task 已定，不改），仅美化色与图标一致性。

### 4. find_git_repos / git_fetch_all

- `find_git_repos`：仓库列表用 Reporter 列表（`r.info` + bullet），标题 rule。
- `git_fetch_all`：逐仓库 fetch 状态行（✔/✖/⏭），结束汇总。

## 改动范围

- `lib/batch_git.py`：run_batch 进度反馈 + print_summary 色/图标对齐规范。
- `lib/ui.py`：若需加 Progress/spinner 辅助方法（否则不动）。
- `bin/find_git_repos` / `bin/git_fetch_all`：入口输出走 Reporter。
- `tests/`：相关测试更新（输出格式断言）。

**不动**：push task 已改的 print_summary 列表结构（仅微调色/图标对齐）。cpd/inject 等非批量脚本。

## 依赖

- **依赖 push task 完结**（同改 batch_git.py，防冲突）。push task finish 后再 start 本 task。

## 验收标准

- 批量类脚本（push_*/merge_*/git_fetch_all/find_git_repos）输出统一 rich 风格（色 + 图标 + rule）。
- 批量执行有逐项进度反馈。
- `python3 -m unittest discover -s tests -q` 全绿。
- 无新增第三方依赖（仅 rich，已在用）。

## 待确认（用户离场，按最低惊讶 + 实用性定）

- [x] scope：仅批量脚本（非全仓）。
- [x] rich 元素：色+rule / 表格（汇总可选）/ 进度 / 图标 全要。
- [x] 总计格式：保留 push task 的列表式（不回退表格），仅对齐色图标。
