# PRD: batch_git 脏仓库 fail 而非 skip/stash

## 背景

用户反馈：批量操作遇仓库未提交变更应 **fail**（提示用户处理），而非悄悄 skip（sync/push）或自动 stash（switch）。skip 让用户误以为成功，stash 可能丢失上下文。

## 目标

三个批量脚本遇 dirty 工作区 → 单仓 fail（继续跑其他仓库），detail 带文件提示。

## 修复点

| 脚本 | 现行 | 改为 |
| --- | --- | --- |
| `switch_branch`（377-384） | stash → switch → pop | dirty = fail，detail 列出脏文件数 |
| `sync_branch`/`sync_master`（488-491） | `return "skip", "工作区有未提交改动"` | `return "fail", ...` |
| `push_branch`（590-593） | `return "skip", "工作区有未提交改动"` | `return "fail", ...` |

## 实现

- 新增 helper `_dirty_detail(repo)`: 跑 `git status --porcelain` 取前 N 个文件名拼 detail（≤200 字符）
- 三处 dirty 检测改 `return "fail", _dirty_detail(repo)`
- switch_branch 删除 stash 逻辑（381-384 + 428-438 stash pop 恢复）

## 验收

1. switch_branch 遇脏仓 → fail（不 stash），detail 显示脏文件
2. sync_branch/sync_master 遇脏仓 → fail
3. push_branch 遇脏仓 → fail
4. 单仓 fail 不中断其他仓库（run_batch 现有行为）
5. 现有测试全过（可能需更新 dirty 相关断言）
6. ruff clean

## 范围

仅 `lib/batch_git.py`。
