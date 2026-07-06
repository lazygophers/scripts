# PRD：push_* 批量结束输出汇总 + 退出码反映失败

## 背景

`lib/batch_git.run_batch` 末尾已调 `print_summary` 输出「仓库总数/✔成功/⏭跳过/✖失败」+ 每项明细。但：
1. `push_all` 永远 `return 0`（batch_git.py:263-264），即使有失败仓库，调用方无法感知。
2. 用户反馈「要输出执行结果」—— 汇总已存在，推测核心诉求是**退出码反映失败** + **汇总更显眼**。

## 目标

1. `push_all` 退出码：有失败 → 非 0（1）；全成功/跳过 → 0。让 shell 调用方能 `&&`/`||` 感知。
2. 汇总输出保留并确认显眼（print_summary 已有 rule 包裹）。
3. 语音通知已区分失败/成功（notify_batch_done），保留。

## 需求规格

### 1. push_all 退出码

`lib/batch_git.py` `push_all` 末尾：
```python
result = run_batch(...)
return 1 if result.failed else 0
```

### 2. run_batch 返回值

现 `run_batch` 返回 `BatchResult`。`push_all` 接住转退出码。其他调用者（switch_branch_all/sync_master_all）同样改，保持一致。

### 3. 汇总输出

`print_summary` 现状保留（rule + 表格 + 明细）。无改动。

## 改动范围

- `lib/batch_git.py`：`push_all`/`switch_branch_all`/`sync_master_all` 末尾 return 按 `result.failed` 返回 0/1。

## 验收标准

- push_* 批量有失败仓库 → 退出码 1；全成功/跳过 → 0。
- 汇总输出不变（仓库总数/成功/跳过/失败 + 明细）。
- 语音通知不变。
- `python3 -m unittest discover -s tests -q` 全绿。
- 新增测试：push_all 失败时退出码 1。

## 待确认

- [x] 退出码反映失败（推测核心诉求，用户离场，按最低惊讶 + 实用性定）。
- [ ] 汇总格式是否需更显眼？（推测：现状够，print_summary 已 rule 包裹）
