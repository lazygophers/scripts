# shell 陷阱（本仓脚本在 zsh 下开发）

- **zsh 把以 `=` 开头的词当 `=command` 路径展开**：裸 `echo ===` 报 `zsh: = not found`，
  `echo =ls` 变成打印 `/bin/ls` 的路径。分隔符/示例里带 `=` 的输出一律加引号
  （`echo '==='`），或改用 `printf`。2026-10-05 在性能基准循环里踩过；
  **2026-10-08 又踩一次（`echo ====`）**——展开不看等号个数，规则没防住的原因是
  被记成了「`===` 特例」。改成机械判据：凡是 echo/printf 参数以 `=` 开头，
  无关长度，一律加引号。分隔线用 `printf '=%.0s' {1..20}` 或直接不画。
- **`pgrep -f` 数字模式是模糊匹配**：`pgrep -f 63447` 会命中命令行里恰好含这个
  数字的无关进程（`--port 63447`、时间戳都算）。要按进程号终止/查看就走精确
  pid 路径（本仓 `lib/process.py::kill_by_pids`，kk 全数字参数已接，commit
  c204236），拿到 pid 后不再 pgrep。lsof 反查 pid 同理只信唯一侧：`lsof -iTCP`
  对每条连接给**两端各一条** n 行，bridge 自己那条「9330->peerPort」的右侧也含
  peerPort，两侧都解析会把 bridge pid 贴到所有连接上——只解析箭头左侧（本仓
  `lib/cli/browse.py::_conn_pids`，commit f510da5）。判据一句话：**标识符匹配
  要么精确（pid、instanceId），要么锚定唯一侧；两侧都可能含目标值就别取两侧。**
