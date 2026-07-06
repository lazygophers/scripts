# Implement：重命名 push/merge 入口 + 合并 pushc_all

单一交付，单 worktree，顺序执行（共享文件多，不并行）。

## 步骤

### S1. `lib/batch_git.py` 泛化
- `_pushc_one_factory` → `_push_one_factory(target, dry_run, extra)`：`canary` → `target`，单仓执行调 `push_{target}`。
- `pushc_all` → `push_all(target, *, dry_run=False, yes=False, extra=None)`：argparse 解析 `--dry-run`/`--yes`/`-y`，`run_batch(confirm=not yes)`。
- 验证：`python3 -m unittest tests.test_batch_git -q`（若有）。

### S2. `bin/_gitwf` 改名 + 自动识别
- `_NAME_MAP` key 改新名（design D2）。
- 分派逻辑加 `in_git_repo` 判断（design D3）：merge 非 git 报错；push 非 git 进批量调 `push_all`。
- 验证：`python3 -c "import sys; sys.path.insert(0,'bin'); import _gitwf"` 语法检查。

### S3. symlink 重建
- 删旧 8 个 + `bin/pushc_all`。
- 建新 8 个 symlink → `_gitwf`。
- 验证：`ls -la bin/` 确认；`bin/push_canary --help` 可执行。

### S4. 文档同步
- `README.md`：入口名映射表 + 用法（单仓/批量自动识别 + `--yes`/`--dry-run`）。
- `CLAUDE.md`：grep `pushc`/`pushc_all`/`mergec` 等，有引用则更新。
- 验证：`grep -rn 'pushc\|mergec\|pusht' README.md CLAUDE.md` 无失效。

### S5. 测试
- 新增 `tests/test_gitwf_dispatch.py`（或扩展现有）：覆盖 `_NAME_MAP` 新名分派 + 自动识别判据（mock `.git` 存在性）。
- 扩展 `tests/test_batch_git.py`：`push_all` 多 target + dry-run + yes。
- 验证：`python3 -m unittest discover -s tests -q` 全绿。

## 失败处理

| 触发 | 一线修复 | 兜底 |
| --- | --- | --- |
| S1 测试失败 | 看 factory target 参数化是否完整 | 回退 batch_git.py，重审 |
| S2 _gitwf 语法错 | 本地 `python3 bin/_gitwf` 调试 | 保留旧 _NAME_MAP 注释备查 |
| S5 测试框架不熟 | 看现有 tests/test_batch_git.py 模式 | 标 TODO，不阻塞主功能 |
