# shell 陷阱（本仓脚本在 zsh 下开发）

- **zsh 把以 `=` 开头的词当 `=command` 路径展开**：裸 `echo ===` 报 `zsh: = not found`，
  `echo =ls` 变成打印 `/bin/ls` 的路径。分隔符/示例里带 `=` 的输出一律加引号
  （`echo '==='`），或改用 `printf`。2026-10-05 在性能基准循环里踩过。
