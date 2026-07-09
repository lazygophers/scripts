# PRD: batch_git 失败时输出具体错误原因

## 背景

`switch_branch` 对 go/pay-dao-public 失败，输出仅「✗ 切换失败」+ 汇总「失败 切换/创建失败」，丢弃 git 命令 stderr，用户无法定位原因（需手动进仓库重跑）。

## 目标

所有 batch_git operation 失败时，提取 git 命令关键错误行输出到 log（实时 r.err）+ detail（汇总表）。

## 修复点

| 行 | 函数 | 现状 | 修复 |
| --- | --- | --- | --- |
| 398/412/421 | `_switch_one_factory` | `r.err("切换失败")` 固定文案 | `_extract_error(sw.stderr)` 输出 |
| 426 | 同上 | `return "fail", "切换/创建失败"` | detail 带提取的错误行 |
| 462 | `_sync_branch_all` | `return "fail", "fetch 失败"` | 带提取错误 |
| 514 | 同上 | `return "fail", f"checkout {target} 失败"` | 带提取错误 |
| 565 | `_sync_master` 等 | `return "fail", "fetch 失败"` | 带提取错误 |

复用已有 `_extract_error(out, code, label)`。

## 验收

1. switch 失败时 log 显示 git stderr 关键行（如 "error: Your local changes... would be overwritten"）
2. 汇总表 detail 列显示同错误（截断 200 字符）
3. 现有测试全过
4. ruff clean

## 范围

仅 `lib/batch_git.py`。复用 `_extract_error`，无新逻辑。
