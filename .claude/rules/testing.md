# 测试：怎么写、怎么跑、怎么别写慢

2026-09-25 做全仓测试完善时踩出来的几条，写下来是为了下次靠文件防住。

## 跑法

```bash
python3 tests/run.py                 # 默认：并行，全套约 100s
python3 tests/run.py test_git_core   # 单模块
python3 tests/run.py --timings       # 每个调度单元的耗时，找慢用例就看它
python3 -m unittest discover -s tests -q   # 串行，调试单个用例时更直观
```

`tests/run.py` 把测试切成「模块」或「模块.测试类」的调度单元并发跑。切到类粒度的门槛是
一个模块里有 ≥4 个 `TestCase` 类（`SPLIT_MIN_CLASSES`）：拆得太碎的话，每个子进程都要重付
一次解释器 + import 的钱，实测全量拆成 536 个单元后墙钟降了但 CPU 从 74s 涨到 134s。

## 铁律

- **测试类必须继承 `unittest.TestCase`。** pytest 风格的裸类会被 `unittest` 静默收集到 0 个：
  `tests/test_kk.py` 的 18 个用例就这样躺了很久没人跑。`tests/test_meta.py` 现在守着这条。
- **测试不许依赖磁盘残留。** `test_lazyhelp_cli` 的 idea 用例曾依赖磁盘上残留的 zip
  才能过，换台机器或清了缓存就红（2026-10-08 全量回归时炸出来，改为 mock `Path.glob`）。
  夹具自建或 mock，不假设本机文件系统的历史状态。
- **测试名要兑现承诺。** `test_bad_line_ignored` 只发 ping 就断言——名字说的"坏行"从未出现。
  下次真有人弄坏分帧逻辑，这条测试照样绿。测什么名就写什么，或改名。
- **新增 `bin/<name>` 必须同时写进 `pyproject.toml` 的 `[project.scripts]`**，否则 uvx 用户
  用不到。同样由 `tests/test_meta.py` 守着。
- **性能测试不许掐表。** 绝对秒数在满载机器上假失败（本套件并行跑时 `list_branch --help`
  3.01s 撞线 3.0s），换成「相对裸解释器的倍数」后同一台空载机上实测在 5.8~13.6 倍之间飘，
  阈值只能放到没有拦截力的位置。改量确定性的东西：`tests/test_perf.py` 量的是启动要 import
  多少个模块（`list_branch` 258 个，加一行 `import requests` 直接变 443）和导入图里有没有
  HTTP 栈。
- **整套套件的 A/B 对比用交错轮次 + user CPU，不看单次墙钟。** 2026-10-05 实测同一台机器
  连跑三次墙钟 48s/53s/45s（±10% 噪声），单次对比能把真提升读成退步；改后跑
  `time python3 tests/run.py` 取 user CPU，改前改后各 ≥3 轮交错（A B A B A B），
  只有两边区间不重叠且方向一致才算数（实测 88.1~90.4s vs 92.5~95.2s 才下结论）。
- **临时目录路径要短。** browse daemon 的 unix socket 全路径在 macOS 上限 104 字节，系统
  `TMPDIR`（`/var/folders/…`）再套一层 `mkdtemp` 就超，报出来的还是 `[Errno None] None:`
  这种看不懂的错。并行跑测器因此把 worker 的 `TMPDIR` 建在 `/tmp` 下。
- **graphwatch 的集成/e2e 测试要用装了 graphify 的解释器跑。** `tests/test_graphwatch_e2e.py`、
  `tests/test_graphwatch_artifacts.py` 在 `import graphify, watchdog` 失败时整类 `skipUnless` 跳过，
  结果照样是 OK。`python3` 不指向 conda 时（子 agent、干净 shell）就会静默跳过，改用
  `/Users/luoxin/miniconda3/bin/python tests/run.py test_graphwatch_e2e`（实测 15 用例 24s）。`tests/run.py`
  不报 skipped 数，先跑 `<同一解释器> -c "import graphify, watchdog"` 确认能导入。
  同理：`graphify query`（pipx 装的 `graphifyy` CLI）跑在 python3.14 下 jieba 报
  `NameError: xrange`（2026-10-08 实测），知识图谱查询改用 conda 解释器，或直接 grep。
- **跑完就把临时目录收掉。** 678 个调度单元 = 678 个临时目录，不收就堆在 `/tmp` 里。
- **browse 扩展：新 Chrome API 依赖先扩 `mock.ts`，再改调用方。** `resolveContext` 加入 `storage.session`/`tabGroups` 后 21 个测试文件需改——`ownWorld()`/`ownSession()` 助手是事后补的。正确顺序：先在 `browser-extension/browse/test/mock.ts` 扩好夹具，再逐一迁移用到这个函数的测试。
- **改完 TypeScript 文件先做单文件冒烟，再跑全套 typecheck。** `node --experimental-strip-types <file.ts>` 在 Node 22+ 上秒级捕获 parse 级语法错（如 filter 回调漏 `async`），比等 `npm run typecheck` 省一轮子进程往返。仅在改完本文件、交付给 `npm test` 前作为快速检查，不替代 typecheck。
- **改既有组件结构（DOM 结构/选择器/事件挂载点）时，先 grep 类名把旧测试找全。** 同一组件的用例散落在多个测试文件，改最顺手那个文件 ≠ 改完。2026-10-09 viewer 悬停框 `.lfv-preview` 从裸 `pre` 变 `div>(tools+pre)`、事件从 link 挪到 cell，波及 `listing.test.ts` / `branch-gaps.test.ts` / `prettify.test.ts` / `entry-gaps.test.ts` 四个文件，`grep -rn ".lfv-preview" browser-extension/viewer/test/` 才找全（settings.test.ts 的命中是反向断言，不用动）。DOM 一动就跑这条 grep，别等 typecheck——DOM 字符串选择器不是类型错误，测不到就静默漏。
- **mock 回调的参数签名照真实 API 全量声明。** viewer `entry-gaps.test.ts` 的 `onMessage` mock 最初只声明 2 参，background 用上第 3 参 `sendResponse` 后测试里被迫 `as unknown as` 加宽再调。mock 签名照 chrome 类型抄全（连 sendResponse），后续加参只动实现不动测试。与上面 browse 的「先扩 mock.ts 再改调用方」同一条原则：mock 落后于真实 API，债就在测试里。
- **派子 agent 跑测试前确认它带本地执行工具。** Explore 类只读 agent 没有 Bash，`npm --prefix browser-extension/*/ test` 跑不了，白等一轮。跑测试派 general-purpose，或主对话自己跑。

## 别写慢测试

慢的来源几乎只有两个：**等子进程**和 **sleep**。串行跑全套 838s，其中 CPU 只占 131s。

- 一个测试里要起几十上百个子进程（薄壳冒烟一次 180 个）：用 `ThreadPoolExecutor` 并发，
  进程启动是 IO 等待，线程池就够。`test_shells_blackbox` 这样从 100s 降到 26s。
- 每个用例都从零建同一份夹具（`git init` + 十几次 git 子进程）：`setUpClass` 建一份模板，
  用例只做 `shutil.copytree`。拷出来的 git 仓库 commit SHA 和模板完全一致，算好的
  merge-base 可以跟着缓存。`test_squash_pr` 这样从 61s 降到 26s。拷完记得
  `git remote set-url origin <新路径>`——远端 URL 是模板里的绝对路径。
- 子进程超时阈值按「整套并行跑、机器满载」来定，不按空载。10s 在满载时会假失败，给到 30s。

## 覆盖率

`python3 -m coverage run --include="<被测文件>" -m unittest -q <相关测试模块...>` 再
`python3 -m coverage report -m`，先看清缺的是哪些行再动手写，别盲写。

平台专属分支（Linux 的 `systemd-inhibit`、Windows 的 `msvcrt`）在本机永远走不到：换掉
`sys.platform` 和 `subprocess.Popen` 就能在 macOS 上跑完，不必留成空白（见
`tests/test_system_linux.py`）。真正跑不到的只有需要 TTY 的那种（`lib/ui.py` 的
`_read_tty_key`）。

## 提交信息不要用 `git commit -m "…"`

2026-09-25 踩到：双引号里的反引号会被 shell 当成命令替换执行。一条讲 bug 的提交
信息里写了 `` `ovpn route remove 10.8` `` 这样的例子，提交时 shell 真的去跑了
`route remove 10.8` 和 `ovpn _need_root`（两条都在参数校验处报错退出，没造成改动，
但这是运气）。

写法只有一种：消息落文件再 `git commit -F <file>`。单行、确定不含反引号的消息才
可以用 `-m`。

## 覆盖率口径：分母决定一切（2026-10-06 五包 95% 门槛踩出来的）

- **Node 默认报告不可直接引用**：`--experimental-test-coverage` 不带 `--test-coverage-include`
  会把测试文件计入分母（虚高），且**未加载的源码不进分母**（browse 曾有 5 个入口文件、
  shared 的 build.mjs 完全漏出）。正确命令：`--test-coverage-include='src/**/*.ts'` +
  `--test-coverage-lines/branches/functions=95` 三门槛，并核 `find src -name '*.ts'` 与报告
  文件数一致。同进程对带 query 的重复 import 只有最后一个实例进覆盖率——每份夹具独立
  测试文件（panel-boot/settings-boot 先例）。
- **Python 并行收集**：`tests/run.py` 每单元一个子进程，外层 `coverage run` 只测到调度器
  本身（TOTAL 1%）。正确做法：`COVERAGE_PROCESS_START` + sitecustomize
  （`coverage.process_startup()`）+ `--parallel-mode` + `combine`，`--source=<repo>/lib`
  才能把未被 import 的源码（如 lib/cli/live_server.py 曾 0% 漏出）纳入分母。跑完记得
  `--keep`。mise python 3.14 缺 bs4/curl_cffi/graphify/watchdog，用 conda 3.13。
- **IntelliJ 插件的 JaCoCo 恒 0% 是机制问题**：插件类经 IDE 类加载体系加载，绕过
  javaagent transformer。offline instrumentation（构建期插桩 sandbox 测试 jar）是唯一
  可行路径，见 idea-plugins/lazy-git 的 `instrumentTestSandboxJar`；发布产物必须验证无
  探针。BRANCH counter 对 Kotlin 编译器生成的 null 检查/when 兜底过度计数，门禁用
  INSTRUCTION+LINE 双 counter，branch 如实报告不硬凑。
- **测量伪影要隔离**：coverage 的 sitecustomize 会给被测子进程注入 coverage 模块链，
  `test_perf` 的启动模块数预算必爆（kk 290>236）——该预算测试检测
  `COVERAGE_PROCESS_START` 跳过，裸跑不受影响。
