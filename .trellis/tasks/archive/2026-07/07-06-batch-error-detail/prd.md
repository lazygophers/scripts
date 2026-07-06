# PRD：修复批量失败 detail 提取关键错误行

## 背景

`_push_one_factory`（lib/batch_git.py:245-247）失败时：
```python
out = (p.stdout or "") + (p.stderr or "")
return "fail", out.strip()[:200]
```

`push_{target}` 子进程 stdout 是**整段 gitc 工作流日志**（rule 标题 + 任务概览 + 逐步操作）。`[:200]` 截取头部 = 无信息量的开头（"Git 自动化工作流 / 当前分支 / 任务概览"），**真正错误原因（merge conflict / push rejected / network）在末尾，被丢弃**。

`run_batch` 把 detail 塞进：
1. 实时失败行：`r.err(f"✖ 失败 — {detail}")`（batch_git.py:166）— 被污染。
2. print_summary 失败段：`  • <name> — <detail>`（batch_git.py:80）— 每个失败项重复整段，可读性崩塌。

实测 push_canary 批量 10 仓库 3 失败：汇总区被 3 段子进程全量输出污染。

## 目标

1. 失败 detail = **一行关键错误摘要**（非全量子进程输出，非头部截断）。
2. 子进程全量输出在失败时流式打印（让用户看到完整上下文），detail 仅取精简原因。
3. 汇总区失败明细每项一行，可读。

## 需求规格

### 1. 提取关键错误行（lib/batch_git.py:_push_one_factory 约 245）

子进程失败输出（stdout+stderr）解析，按优先级取关键行：
- grep 匹配错误关键词：`conflict|rejected|fatal|error|denied|timeout|unresolved|diverged`（git/lfs 常见错误）
- 取**最后一个匹配行**（错误总结通常在末尾）
- 无匹配 → 取 stdout/stderr **最后非空行**
- 仍无 → fallback `"push_{target} 失败 (exit {code})"`

实现示例：
```python
import re
def _extract_error(out: str, code: int, target: str) -> str:
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # 优先匹配关键词
    pattern = re.compile(r"conflict|rejected|fatal|error:|denied|timeout|unresolved|diverged|non-fast-forward", re.I)
    matched = [ln for ln in lines if pattern.search(ln)]
    if matched:
        return matched[-1][:200]
    # 否则取最后非空行
    if lines:
        return lines[-1][:200]
    return f"push_{target} 失败 (exit {code})"
```

detail 长度上限 200 字符（单行）。

### 2. 失败时流式打印子进程全量（lib/batch_git.py:_push_one_factory）

失败时把子进程 stdout+stderr 全量打到 Reporter（让用户看完整 gitc 日志），再 return 精简 detail：
```python
if p.returncode == 0:
    return "ok", ""
out = (p.stdout or "") + (p.stderr or "")
r.warn("子进程输出：")
for ln in out.splitlines():
    r.dim(f"  {ln}")  # 或 r.info，dim 表示降级
return "fail", _extract_error(out, p.returncode, target)
```

这样：实时日志有完整上下文，汇总 detail 仅精简原因。

### 3. 其他 factory 检查

- `_switch_one_factory` / `_sync_master_factory` 现返回短文案（"fetch 失败"/"checkout 失败"）— 可接受，但若也吞了子进程输出，同样改用 `_extract_error`。grep 确认有无类似 `out[:200]` 模式。

### 4. print_summary 失败段不变

print_summary 列表式（push task 已定）保留。detail 精简后自然单行可读。

## 改动范围

- `lib/batch_git.py`：
  - 加 `_extract_error` 辅助函数。
  - `_push_one_factory` 失败路径改：流式打印全量 + return 精简 detail。
  - 检查 `_switch_one_factory`/`_sync_master_factory` 同类问题（若有 `out[:200]` 同改）。
- `tests/test_batch_git.py`：新增 `_extract_error` 单测（关键词匹配/无匹配 fallback/空输出）+ factory 失败 detail 精简断言。

## 验收标准

- push_canary 批量失败时，detail = 单行关键错误（如 "CONFLICT (content): Merge conflict in ..."），非整段 gitc 日志。
- 子进程完整输出在失败时仍可见（流式打印到 Reporter）。
- print_summary 失败段每项一行，无多行污染。
- `_extract_error` 单测覆盖：关键词命中取末次、无命中取末行、空输出 fallback。
- `python3 -m unittest discover -s tests -q` 全绿。
- 实测：构造一个失败仓库（merge conflict），验证 detail 精简 + 全量可见。

## 待确认（用户离场，按最低惊讶 + 实用性定）

- [x] detail 策略：关键错误行（末次匹配），非全量、非头部截断。
- [x] 全量输出：失败时流式打印（dim 降级），不进 detail。
- [x] 关键词集：git 常见错误（conflict/rejected/fatal/error/denied/timeout/unresolved/diverged/non-fast-forward）。
