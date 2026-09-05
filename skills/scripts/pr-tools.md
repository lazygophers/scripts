四个薄壳脚本，内部调 `claude` 生成 title/body 文案。

```bash
commit            # 自动提交当前仓库变更（调 claude 生成 message）
commit all        # 批量扫描当前目录所有 git 仓库并提交
commit auto       # 智能判断：cwd 是 git 仓库 → here，否则 → all
issue             # 自动创建 Issue
mr [base]         # 自动创建 PR/MR（默认 draft）
squash_pr [source] <target>  # 压 source 自分叉以来的改动为单 commit → push → 开 PR
```

要点：

- 都支持 `--dry-run` 预览。
- `mr` 支持 `--publish`（非 draft）、`--reviews`、`--labels`、`--assignee`。
- `squash_pr` PR 分支已存在时复用并 force push，保持同一 PR；`--push_only` 只推不开 PR。
