# AI shell 环境自动极简输出（目标是省 token）

当前进程由 AI 工具（Claude Code / Codex / Cursor / Gemini CLI）派生时
（`lib/ai_env.py` 的 `is_ai_shell_env()`，判据 = 标记环境变量，调研见
`.scratch/research/detect-ai-shell-env.md`），所有输出出口自动切极简，
判定标准只有一条：**这段输出喂给模型是不是纯耗 token**。

## 核心语义（2026-09-24 第 5 轮：状态保留，原因分流）

- **过程成功静默**：`ok()` / `step()` / `info()` / `rule()` 不输出；退出码 0
  已含成功信息。`cmd_result()` 成功（rc 0/None）同样静默。
- **汇总状态保留**：`status_table()` 保留每个仓库的名称和 `ok` / `skip` /
  `fail` 状态；`ok` / `skip` 详情清空，`fail` 详情保留。
- **成功面板压缩**：`panel()` 只留标题行（如「提交完成 7a72e45」），内容
  （commit message、分支详情）丢弃。
- **失败/警告保留**：`err` / `status(fail)` 输出 `ERROR: <原文>`，`warn`
  输出 `WARN: <原文>`；原文即关键诊断，不截断。`err` 仍写
  `slog.record("cli.error")`（统一 JSONL，失败细节只落一次）。
- **过程原文默认丢弃**：`output()` 默认丢弃成功路径的子进程回放
  （`Switched to branch` 等）。失败详情、用户点名的数据、`--debug` 由调用方
  传 `force=True` 放行。
- **耗时行只在失败时出**：`timed()` 成功路径不打印；失败时一行
  `<label>: <耗时>` 纯文本（`lib/ui.py` / `lib/fire_base.py`）。

### 两类输出出口

- **过程出口**：`info` / `step` / `rule` / `panel` / `output`。AI 环境只留
  失败诊断、明确点名的数据和调试输出。
- **状态出口**：`status` / `status_table`。AI 环境保留状态覆盖范围；只隐藏
  `ok` / `skip` 的解释，保留 `fail` 的原因。

## 批量子进程（lib/batch_git.py::_run_exec）

execute 阶段的 `push_*` / `merge_*` 子进程在 AI 环境改为**捕获**（人类
环境保持直吐实时流）：rc=0 静默；rc≠0 把捕获原文整段（≤2000 字符）经
`r.err` 吐出——子进程内部自己的 ERROR 行只在失败时值得看。并发回放路径
不变（`r.output` 在 AI 环境本身已被门住）。

## Reporter（lib/ui.py，全部 CLI 的输出汇聚点）

- `status_table` 是状态出口：AI 环境保留每行身份和状态，只按状态过滤详情。
- `panel` / `output` 是过程出口：AI 环境按上节规则压缩或丢弃。
- `kv` / `summary` 渲染成 `key: value`，所有极简输出 `no_color=True`。

## 数据出口（关键数据一字不丢，空白全砍）

- JSON 一律走 `lib/ai_env.py` 的 `json_dumps()`：AI 环境无缩进无空格
  （`{"a":1}`），人类环境保持 `indent=2`。archery / grafana / browse /
  websearch `--json` / commit_wf debug 全部已接
- `archery --table`：AI 环境强制降级为 TSV
- `browse --table` / `print_result(table=True)`：AI 环境强制 JSON
- fire help / INFO 提示：不 `force_terminal`——强制 ANSI 会把转义码喂给
  模型（`lib/fire_base.py`）

## 约定

新 CLI 不用做任何事，走 Reporter 即继承；自建 Console 时禁止
`force_terminal=True` 无条件开启，必须 `force_terminal=not is_ai_shell_env()`。
出 JSON 时必须用 `json_dumps()` 而非裸 `json.dumps(..., indent=2)`。
新增 AI 工具标记时改 `lib/ai_env.py` 的 `_MARKERS`，不动 ui。测试里
`tests/__init__.py` 已清掉标记变量保证断言确定性；测极简模式本身用
`patch.dict` 注入（见 `tests/test_ui_minimal.py`）。

## 数据格式分流（2026-09-23 第 3 轮，用户确认）

- **所有输出允许语义改写**（不只人类输出）：允许改变字段呈现方式，
  关键数据一字不丢
- **表格类数据**（多行同构记录：archery query / browse list / websearch
  结果 / email 列表 / kk 进程表）：TSV——首行列名、制表符分列、单元格内
  `\t` `\n` `\\` 转义。共享实现 `lib/ui.py::print_tsv()`
- **嵌套/非表格数据**（browse api / grafana api 原始响应）：压缩 JSON
  单行（`json_dumps`），拍平会丢层级
- **CLI 列表/状态命令**（list_branch）：竖线列行
  `[*]name | upstream | track`，`*` = 当前分支；每仓先一行
  `repo | clean|M2 ??1`（工作区状态）
- **websearch**：AI 环境默认格式从 plain（一条三行）降级 TSV；
  `[websearch]` 过程性 stderr（引擎计数/提示/实例命中/缓存预告）全丢
- **`status_footer`**：AI 环境静默（统计数可从数据行数推出）
