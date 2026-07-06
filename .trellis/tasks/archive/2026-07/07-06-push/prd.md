# PRD：push_* 批量结束输出汇总（列表式）+ 退出码反映失败

## 背景

`lib/batch_git.run_batch` 末尾已调 `print_summary` 输出「总数/成功/跳过/失败」表格 + 三段明细（成功/跳过/失败各一列表）。用户反馈：
1. 汇总非预期格式——要**列表式**（成功项目列表 + 失败项目列表 + 总计单行），非表格。
2. 跳过项目**不需展示**（明细删掉）。
3. 总计数量要展示。
4. `push_all` 永远 `return 0`（batch_git.py:263-264），有失败调用方无法感知。

## 目标

1. `print_summary` 改列表式：成功列表 + 失败列表 + 总计单行，删跳过明细。
2. `push_all`/`switch_branch_all`/`sync_master_all` 退出码：有失败 → 1；否则 → 0。

## 需求规格

### 1. print_summary 格式（lib/batch_git.py:61）

```
────────────────── <title> ──────────────────
总计 12 个（成功 10 / 跳过 0 / 失败 2）
✔ 成功项目：
  • repo-a
  • repo-b
✖ 失败项目：
  • repo-c — push rejected
  • repo-d — network timeout
```

- 总计单行：`总计 N 个（成功 X / 跳过 Y / 失败 Z）`。
- 成功列表：`✔ 成功项目：` 标题 + 每项 `  • <name>`。
- 失败列表：`✖ 失败项目：` 标题 + 每项 `  • <name> — <detail>`（无 detail 则仅 name）。
- **删跳过明细**（跳过数量仍在总计行体现）。
- 成功为空不显成功段；失败为空不显失败段。

### 2. 退出码（lib/batch_git.py）

`push_all`/`switch_branch_all`/`sync_master_all` 末尾：
```python
result = run_batch(...)
return 1 if result.failed else 0
```

### 3. notify_batch_done 保留

语音通知已区分失败/成功，无改动。

## 改动范围

- `lib/batch_git.py`：
  - `print_summary`（行 61-79）改列表式输出。
  - `push_all`/`switch_branch_all`/`sync_master_all` 末尾 return 按 `result.failed` 返回 0/1。
- `tests/`：新增/更新测试覆盖新格式 + 退出码。

## 验收标准

- 批量结束输出：总计单行 + 成功项目列表（按名）+ 失败项目列表（按名 + detail）。
- 跳过项目无明细（仅总数进总计行）。
- 成功/失败为空时对应段不显示。
- push_* 批量有失败 → 退出码 1；全成功/跳过 → 0。
- `python3 -m unittest discover -s tests -q` 全绿。
- 新增测试：print_summary 列表格式断言 + push_all 失败退出码 1。

## 待确认（用户离场，按最低惊讶 + 实用性定）

- [x] 总计格式：单行文字 `总计 N 个（成功 X / 跳过 Y / 失败 Z）`（用户要"列表形式"，单行最贴合）。
- [x] 退出码：一并做（shell `&&`/`||` 感知失败是 push 工具刚需）。
- [x] 跳过：删明细，数量留总计行。
