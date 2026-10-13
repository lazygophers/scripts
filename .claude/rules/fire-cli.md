# fire CLI 陷阱

- **fire 把全数字 argv 转成 int**：`kk 63447` 到手是 `int`，炸在字符串正则
  （`lib/cli/kk.py` 的 `_NAME_RE`，TypeError: expected string）。两类接法
  （commit c204236）：参数先 `str(p)` 回转再做任何字符串判定；全数字参数不走
  名字模糊匹配，改走精确路径（kk 的 `kill_by_pids`）——顺带躲开 pgrep -f 数字
  模糊命中，见 shell.md 的 pgrep 条目。新 CLI 接数字形态参数（pid、端口、
  时间戳）时按这两步走。

- **BiDi 的 `context`/`root` 是字符串 id，同一个 int 陷阱的延伸**：线上 BiDi
  规范里 browsingContext id 是 string（`"7"`、`"7.2"`），`browse page text
  --context 123` 经 fire 到手是 int，按字符串匹配会被拒。`lib/cli/browse.py`
  的 `STRING_NUMERIC_PARAMS`（约 406-416 行）在 dispatch 前把 `context`/`root`
  等 id 语义键 `str()` 回转，纯数字 `browse tab adopt 42` 的 42 同样走这条；
  显式 `--context '"123"'` 的老写法不受影响。新接 `lg:` 方法时，参数凡是 id
  语义（context/root/tabId）都登记进 `STRING_NUMERIC_PARAMS`，别靠调用方自己
  加引号；测试传 context 也写字符串（`context: "7"` 不写 `7`）。
