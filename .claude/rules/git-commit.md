# git commit 陷阱

- **`git commit -- <paths>` 不含未跟踪新文件**：pathspec 只匹配已跟踪文件，遇到
  新文件整条失败——`error: pathspec 'new.txt' did not match any file(s) known to
  git`，rc=1，同条命令里已跟踪文件的改动也一起没提交（2026-10-25 提交扩展遥测
  新文件时踩过，隔离仓库实测复现）。新文件先 `git add` 再 `git commit -- <paths>`。
- **`&&` 链尾再接 `;` + 下一条会吞掉 commit 的退出码**：失败只剩 stderr 一行，
  「有输出 = 提交成功」的眼看判断就是错判来源。commit 后独立跑
  `git log --oneline -1` 或 `git status --short` 核对落没落盘，别信拼接链的静默。
