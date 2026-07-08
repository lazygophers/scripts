# 同步 bin 脚本到文档

## 背景
bin/ 有 19 个脚本（含 merge_*/push_* 符号链接折叠到 `_gitwf`），但 README.md 与 CLAUDE.md 均有缺漏与过时引用。

## 目标
让 README.md 与 CLAUDE.md 的脚本清单与 bin/ 实际一致。

## 缺口清单

### README.md 表格（line 23-46）缺失
| 脚本 | 功能 | 示例 |
|---|---|---|
| `commit` | 自动提交变更（调 claude 生成 message） | `commit` |
| `issue` | 自动创建 Issue（调 claude 生成 title/body） | `issue` |
| `prc` | 自动创建 PR/MR（调 claude 生成 title/body，默认 draft） | `prc [base]` |
| `squash_pr` | 压缩 source 为单 commit → 对接 prc 开 PR | `squash_pr [source] <target>` |

### README.md 过时
- `reindex` 标 local-only 但仍在表 — 核实是否仍存在（CLAUDE.md 标 .gitignore）

### CLAUDE.md 过时/错误（line 15-21, 94-97, 129-132, 160-175）
- `gitc` → 实际是 `merge_*`/`push_*`（经 `_gitwf`），无 `gitc` 脚本
- `work_lib.py` / `py_common.py` → 实际是 `lib/*.py` 模块化
- `bit` command（line 130）→ 实际用标准 `git`，无 `bit` 依赖
- 「Local-only tools」(line 21): `reindex`/`unsleep`/`commit` 标 .gitignore — 但 `commit`/`unsleep` 实际在 bin/ 且 tracked
- 缺：`prc`/`issue`/`squash_pr`/`inject`/`delete_branch*`/`switch_branch`/`sync_branch`/`sync_master`/`fetch_all`

## 核实项（实现前查）
1. `reindex` 是否存在于 bin/？→ 不在 ls 结果，确属 local-only（CLAUDE.md 正确，README 表格应删或标注）
2. `commit`/`unsleep` 是否 tracked？→ `git ls-files bin/` 核实

## 验收
- README 表格含全部 19 个 tracked 脚本（local-only 单独标注或移出）
- CLAUDE.md 脚本清单与实际一致，删除 `gitc`/`work_lib.py`/`py_common.py`/`bit` 过时引用
- 多语言 README（.en/.fr/.es/.ru/.ar）同步主表新增项（至少 .en，其余可后续）

## 非目标
- 不改脚本本身
- 不重构文档结构，仅补齐/纠正清单
