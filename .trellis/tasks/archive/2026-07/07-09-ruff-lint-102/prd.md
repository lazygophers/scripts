# PRD: 修复全仓 ruff lint (102 项)

## 背景

checkwork 的 Python ruff 检查报 102 项 lint 告警（warn-only 不中止，但污染输出）。排除 `.trellis/`/`.codex/` 后，剩 `lib/`+`tests/` 的真实代码风格债需清零。

## 目标

`ruff check .` 零告警，checkwork 输出干净。

## lint 分布

| 规则 | 数量 | 修复方式 |
| --- | --- | --- |
| UP045/UP006/UP035/UP037 | 61 | `Optional`→`X\|None`、`List`→`list`、`Tuple`→`tuple`、去引号注解，ruff `--fix --unsafe-fixes` 全自动 |
| F401 | 6 | 未用 import，`--fix` 自动 |
| I001 | 3 | import 排序，`--fix` 自动 |
| E402 | 7 | import 不在顶部（cpd_core.py 无 docstring 导致），手看 |
| E731 | 1 | lambda 赋值改 def |
| F541 | 1 | 无占位 f-string 去 `f` |
| F841 | 2 | 未用变量删除 |
| UP015 | 2 | `open(...,"r")` 去 mode |

## 验收

1. `ruff check .` 零告警
2. `python3 -m unittest discover -s tests -q` 全过（348+12）
3. `python3 bin/checkwork` 输出无 ruff 告警
4. 无行为变更（纯风格）

## 范围

`lib/` + `tests/` 全部 .py。不改 `.trellis/`/`.codex/`（已 exclude）。
