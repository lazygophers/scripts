# CLI 启动性能优化：判据、归因、否掉重审

2026-10-05 ~ 10-06 四轮优化（92.5~95.2s → 79.0~81.1s user CPU，-14%）踩出来的。
A/B 跑法（交错轮次、user CPU、区间不重叠）见 testing.md「性能测试不许掐表」「交错轮次」两条。

## 目标函数是套件 user CPU，单进程毫秒不算数

「整体提升才算提升」落在指标上就是：全量 `tests/run.py` 的 user CPU 是唯一裁判，
`-X importtime` 的单进程毫秒只用来出候选，不用来下结论。json 惰性代理那轮：单进程
实测省 6ms，3 对交错 A/B 全反向（A 比 B 慢 0.25~2.45s），当场回滚——`lib/log.py`
顶层 import json 而每条 CLI 的 `timed()` 都写 `cli.start`，json 是必经件，桩掉只剩
代理开销，套件反而变慢。局部赢全局输的改动直接回滚，别留着换「真实 CLI 启动省 Xms」。

## importtime 归因错觉：树上的数和顶行都会骗人

- 累计树（cumulative）含整条父链，大数常挂在别人头上。
- 单独 import 后看顶行也不保险：fire.formatting_windows 曾被「单独 import 顶行
  7µs」判为非优化点，实际它拖 ctypes/platform/subprocess/colorama，桩掉后
  A/B 实测 -4%。
- 结论：importtime 只筛候选，真伪一律由「桩掉 → 干净 A/B」裁决。桩是零成本的
  （`sys.modules[...] = types.ModuleType(...)` 几行），比争论归因便宜。

## 可桩的画像：仓库内不可达的功能件

fire 的 REPL asyncio、win32 副作用的 formatting_windows——顶导但本仓 CLI 带参
跑完即退、POSIX 零引用，这类才桩得掉。反例也是功能件：console_io 的 More、
completion 的 MemberVisible 在正常路径被调用，json 是 log.py 必经——桩它们等于
砍功能或只剩代理开销。

## 否掉的方案要重审，先看否掉理由是哪类

- 以**可错的估计**否掉的（「改动面 ~40 处」）→ 重审：rich 惰性化上轮以此放弃，
  重审发现 PEP 562 `__getattr__` 能兜住外部消费面（`from lib.ui import Table`），
  真改动面只有 7 个入口，落盘后 -7.4%。估计类否掉理由在前提变化（新工具、新理解）
  时应重新核对。
- 以**语义/安全**否掉的（`sys.modules` 塞 None 会让后续真 import 拿到假 asyncio，
  browse 全挂）→ 不重审，理由不随前提变。

## 判停：地板长什么样

importtime 只剩三类就到地板，别再挖：解释器 + site（~11ms，stdlib 固有）、功能件
依赖（fire 的 inspect/console_io/completion ~13ms，去掉 = 重写 fire）、必经件
（json 进 log、rich 已按需）。再挖一轮 A/B 反向即确认。
