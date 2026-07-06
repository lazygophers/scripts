# scripts

开发效率工具集 — 各种快捷脚本的集合。Bash/Python 混合薄壳入口, 核心逻辑沉淀在 `lib/`。

---

## 目录结构

```
scripts/
├── bin/                          # 薄壳入口脚本 (chmod +x)
│   ├── checkwork, cpd, kk, kkp, n, ...
│   ├── mergec, mergedev, mergem, merget   # 调 lib git_workflow.run(<target>)
│   ├── pushc, pushdev, pushm, pusht
│   ├── switch_branch, sync_master, find_git_repos, git_fetch_all
│   ├── pushc_all, loop, unsleep, reindex
│   └── inject                    # 把 bin/ 注入 shell PATH
├── lib/
│   ├── commands/<域>/<命令>.py    # 每个命令的业务逻辑, 暴露 main(argv) -> int
│   │   ├── build/  file/  git/  process/  misc/  system/
│   │   └── git/merge.py + git/push.py 额外暴露 run(target, argv)
│   └── <域>.py                    # 共享库 (git/exec/ui/notify/build/process/...)
├── tests/                        # unittest 套件
├── commit / prc / issue          # bash 脚本, 待重写为 py (临时留根目录)
└── README.md
```

**调用链**: `bin/<脚本>` (3 行 path hack + import) → `lib.commands.<域>.<命令>.main(argv)` → 共享 `lib/<域>.py`。

---

## 安装: 把 bin/ 注入 PATH

```bash
./bin/inject            # 生成 ~/.scripts.sh 并 source 到所有 rc (~/.bashrc / ~/.zshrc / ~/.profile / ~/.bash_profile)
./bin/inject --show     # 预览将写入的内容
./bin/inject --uninstall  # 卸载
```

inject 幂等: 重跑不会重复追加。完成后重启 shell 或 `source ~/.zshrc` 即可在任意目录直接调用 `checkwork` / `mergec` / ...。

---

## 脚本功能

| 脚本             | 功能                                                         | 示例                          |
| :--------------- | :----------------------------------------------------------- | :---------------------------- |
| `checkwork`      | 自动化编译检查 + 语音通知                                    | `checkwork`                   |
| `cpd`            | 深度覆盖复制 (默认只新增/更新; `-f` 删除目标多余文件)        | `cpd src/* dest/`             |
| `kk`             | 按进程名终止进程                                             | `kk nginx`                    |
| `kkp`            | 按端口终止进程                                               | `kkp 8080`                    |
| `n`              | macOS 语音播报 (`say`)                                       | `n "构建完成"`                |
| `loop`           | 循环执行命令, 追踪成功/失败                                  | `loop 10 curl url`            |
| `mergec`         | 合并当前分支 → canary, 留在 canary                           | `mergec [--dry-run]`          |
| `mergedev`       | 合并当前分支 → develop, 留在 develop                         | `mergedev`                    |
| `mergem`         | 合并当前分支 → 远端默认分支, 留在目标                        | `mergem`                      |
| `merget`         | 合并当前分支 → test, 留在 test                               | `merget`                      |
| `pushc`          | 合并当前分支 → canary, 推送后切回原分支                      | `pushc [--stay]`              |
| `pushdev` / `pushm` / `pusht` | 同上, 目标分别为 develop / 远端默认 / test      |                               |
| `pushc_all`      | 批量 pushc: 扫描目录下所有 GitLab 仓库, 逐个 pushc           | `pushc_all [--dry-run]`       |
| `switch_branch`  | 批量切换分支 (不存在则从 origin/master 创建)                 | `switch_branch <branch>`      |
| `sync_master`    | 批量同步 master                                              | `sync_master`                 |
| `find_git_repos` | 列出目录下所有 Git 仓库                                      | `find_git_repos`              |
| `git_fetch_all`  | 批量 fetch 所有 Git 仓库                                     | `git_fetch_all`               |
| `unsleep`        | macOS caffeinate 防休眠                                      | `unsleep -t 3600`             |
| `reindex`        | 项目重新索引 (local-only, .gitignore)                        | `reindex`                     |
| `inject`         | 把 bin/ 注入 shell PATH                                      | `inject`                      |

---

## 环境依赖

- **Python 3.10+** (薄壳与核心逻辑)
- **Git** (merge/push/switch_branch/sync_master/git_fetch_all/find_git_repos/pushc_all)
- **macOS** (`n` 用 `say`, `unsleep` 用 `caffeinate`)
- **rich** (输出美化, `pip install rich`)
- **pgrep / ps / lsof / kill** (kk / kkp)

---

## 加新脚本

两步, 互不干扰:

### 1. 写业务逻辑到 `lib/commands/<域>/<名>.py`

```python
#!/usr/bin/env python3
"""foo - 干啥的。"""
import argparse
import sys


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="foo")
    parser.parse_args(argv[1:])
    # ... 业务逻辑 ...
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

域分类: `build` / `file` / `git` / `process` / `misc` / `system`。新建域记得加 `lib/commands/<域>/__init__.py`。

### 2. 加薄壳 `bin/<名>`

```python
#!/usr/bin/env python3
"""foo 薄壳入口。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from lib.commands.<域>.foo import main

raise SystemExit(main(sys.argv))
```

```bash
chmod +x bin/foo
```

完成。无需注册任何字典或列表。

### 别名脚本 (多个名字同一逻辑不同参数)

参考 `mergec/mergedev/mergem/merget`: 在 `lib/commands/git/merge.py` 暴露 `run(target, argv)`, 每个薄壳传固定 target:

```python
# bin/mergec
from lib.commands.git.merge import run
raise SystemExit(run("canary", sys.argv))
```

---

## 测试

```bash
python3 -m unittest discover -s tests -q
```

---

## 参与贡献

- PR 需含必要单元测试
- 新脚本遵循上文「加新脚本」两步, 保持薄壳与业务分离
