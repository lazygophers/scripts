# fire CLI 陷阱

- **fire 把全数字 argv 转成 int**：`kk 63447` 到手是 `int`，炸在字符串正则
  （`lib/cli/kk.py` 的 `_NAME_RE`，TypeError: expected string）。两类接法
  （commit c204236）：参数先 `str(p)` 回转再做任何字符串判定；全数字参数不走
  名字模糊匹配，改走精确路径（kk 的 `kill_by_pids`）——顺带躲开 pgrep -f 数字
  模糊命中，见 shell.md 的 pgrep 条目。新 CLI 接数字形态参数（pid、端口、
  时间戳）时按这两步走。
