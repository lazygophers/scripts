# graphify 调研

调研对象：<https://github.com/Graphify-Labs/graphify>
调研时间：2026-09-10
证据基础：`gh repo clone Graphify-Labs/graphify /tmp/graphify-research`（commit `3f82bf7`，release 0.9.57）+ 本机已安装的 `graphify 0.9.57`（`/Users/luoxin/.local/bin/graphify`）真实输出 + 本机真实 `graphify-out/` 目录。

> 说明：本文里 `Graphify-Labs/graphify:path:line` 形式的引用，指的是上面那次 clone 出来的源码文件和行号。

---

## 一、概述

graphify 是一个把「一个项目里的所有东西」变成**知识图谱**的命令行工具。它读代码、文档、PDF、图片、音视频，产出一个 `graph.json`（图数据）、一个 `graph.html`（可点击的网页图）、一个 `GRAPH_REPORT.md`（重点摘要）。之后 AI 助手不再逐个读文件，而是直接查这张图。

三条关键事实：

1. **代码解析完全在本地**，用 tree-sitter 做 AST（抽象语法树 = 把代码解析成结构化的树），不调用任何 LLM、不联网。只有文档/图片/音视频的「语义 pass」才需要配置一个 LLM backend。出处：`Graphify-Labs/graphify:README.md:115`（"Local-first ... Code is parsed locally with tree-sitter (no LLM, nothing leaves your machine)"）
2. **覆盖约 37 种 tree-sitter 语法**，跨文件解析 `calls` / `imports` / `inherits` / `mixes_in` 边。出处：`Graphify-Labs/graphify:README.md:110`、`README.md:341`
3. **`graphify-out/` 设计成要提交进 git**，让团队每个人 clone 下来就有一张地图。出处：`Graphify-Labs/graphify:README.md:432`

架构上分阶段，各阶段之间只传普通 Python dict 和 NetworkX 图，**除了 `graphify-out/` 之外没有任何副作用**。出处：`Graphify-Labs/graphify:ARCHITECTURE.md:11`

---

## 二、功能清单

### 2.1 CLI 子命令（证据：本机 `graphify --help` 全量输出，版本 0.9.57）

**图的构建与更新**

| 命令 | 作用 |
|---|---|
| `extract <path>` | 无人值守的完整抽取（AST + 语义 LLM），给 CI / 脚本用。支持 `--backend gemini\|kimi\|claude\|openai\|deepseek\|ollama`、`--mode deep`、`--code-only`（只索引代码，不需要 API key）、`--postgres DSN`（抽取活的 PostgreSQL schema）、`--cargo`（Cargo.toml 的 crate 依赖）、`--global`（顺带并进全局图） |
| `update <path>` | 只重新抽取代码文件并更新图，**不需要 LLM** |
| `watch <path>` | 盯着一个目录，代码变了就重建图 |
| `cluster-only <path>` | 在已有 `graph.json` 上重跑聚类并重新生成报告 |
| `label <path>` | 用 LLM 给社区（community = 图里自动分出的子系统）重新命名 |
| `check-update <path>` | 检查 `needs_update` 标志，提醒有待做的语义重抽取（cron 安全） |
| `add <url>` | 抓一个 URL 存进 `./raw` 并更新图 |
| `clone <github-url>` | clone 一个 GitHub 仓库到本地并打印路径 |

**查询（这是日常用得最多的一组）**

| 命令 | 作用 |
|---|---|
| `query "<question>"` | 对 `graph.json` 做 BFS 遍历回答问题（`--dfs` 换深度优先，`--budget N` 限制输出 token，默认 2000） |
| `path "A" "B"` | 两个节点之间的最短路径 |
| `explain "X"` | 用大白话解释一个节点和它的邻居 |
| `affected "X"` | 反向遍历，找出会被 X 影响到的节点（`--depth N`，默认 2） |
| `god-nodes` | 列出连接数最多的节点（架构枢纽），`--top N` |
| `prs` | PR 看板：CI 状态、review 状态、worktree 映射 |
| `benchmark [graph.json]` | 相对「把整个语料全喂进去」的朴素做法，测量 token 削减比 |
| `diagnose multigraph` | 报告 `graph.json` 里同端点边被折叠的风险 |

**反馈闭环（图会因为你的提问而变好）**

| 命令 | 作用 |
|---|---|
| `save-result` | 把一次问答存进 `graphify-out/memory/`，`--outcome useful\|dead_end\|corrected` 记录这答案有没有用 |
| `reflect` | 把 `graphify-out/memory/` 里的 outcome 聚合成一份确定性的经验文档 `reflections/LESSONS.md`（`--half-life-days N` 让旧信号权重衰减，默认 30 天减半） |

**导出**

`export html`（交互式 `graph.html`）、`export callflow-html`（基于 Mermaid 的架构/调用流 HTML）、`export obsidian`（Obsidian vault + canvas）、`export wiki`（wiki markdown）、`export svg`、`export graphml`、`export neo4j`（Cypher 或直接 push）、`export falkordb`，以及独立的 `tree`（D3 v7 可折叠树 HTML）。

**跨仓库 / 全局图**

`merge-graphs <g1> <g2>`（多个 `graph.json` 合成一张跨仓图）、`global add/remove/list/path`（维护 `~/.graphify/global-graph.json`）。

**集成安装**

`install` / `uninstall`（`--purge` 顺带删掉 `graphify-out/`）、`hook install|uninstall|status`（post-commit / post-checkout git hook + 一个 `graph.json` 的 merge driver，让它永远不出冲突标记）、`merge-driver`、`provider`（管理自定义 LLM provider），以及 20+ 个平台各自的 `<platform> install|uninstall`：claude、codebuddy、codex、opencode、kilo、aider、copilot、vscode、cursor、gemini、claw、droid、trae、trae-cn、antigravity、hermes、kiro、pi、devin。

### 2.2 MCP tools（11 个）

证据：`Graphify-Labs/graphify:graphify/serve.py:1693-1845`（`async def list_tools()`）。服务启动方式：`python -m graphify.serve graphify-out/graph.json`，支持 `--transport http --port 8080 --api-key`（`README.md:476-484`）。

| tool | 作用 | 关键参数 |
|---|---|---|
| `query_graph` | BFS/DFS 搜图，返回相关节点和边 | `question`（必填）、`mode` bfs/dfs、`depth` 默认 3、`token_budget` 默认 2000、`context_filter` |
| `get_node` | 按 label 或 ID 取一个节点的完整信息 | `label`（必填） |
| `get_neighbors` | 取一个节点的全部直接邻居及边细节 | `label`（必填）、`relation_filter` |
| `get_community` | 取某个社区的全部节点 | `community_id`（必填，按大小 0 起编号） |
| `god_nodes` | 连接数最多的节点 | `top_n` 默认 10、`exclude_hubs_percentile` |
| `graph_stats` | 汇总统计：节点数、边数、社区数、confidence 分布 | 无 |
| `shortest_path` | 两个概念间最短路径 | `source`、`target`（必填）、`max_hops` 默认 8、`undirected` 默认 false |
| `list_prs` | 列出开着的 GitHub PR，带 CI 状态、review 状态、图影响面 | `base`、`repo` |
| `get_pr_impact` | 某个 PR 的详细图影响：改了哪些文件、影响哪些社区、碰了多少节点 | `pr_number`（必填）、`repo` |
| `triage_prs` | 所有可处理的开着的 PR + 完整图影响数据，用来排 review 优先级和合并顺序 | `base`、`repo` |

另外**每个 tool 都自动注入一个可选的 `project_path` 参数**（多项目支持），在 `serve.py:1827-1845` 统一加上，而不是在 11 份 schema 里各写一遍。

### 2.3 能力边界

**能处理的文件类型**（出处：`Graphify-Labs/graphify:README.md:338-368`）：

- 代码：37 种 tree-sitter 语法，`.py .ts .js .go .rs .java .c .cpp .rb .cs .kt .scala .php .swift .lua .zig .ps1 .ex .m .ml .jl .vue .svelte .astro .dart .sql .f90 .pas .sh .json` 等
- 额外语言（需装 extra）：Salesforce Apex `.cls .trigger`（正则实现，非 tree-sitter）、Terraform `[terraform]`、OCaml `[ocaml]`、Common Lisp `[commonlisp]`、Robot Framework `[robot]`
- MCP 配置：`.mcp.json` / `claude_desktop_config.json` → 抽出 server 节点、包引用、环境变量需求
- 包清单：`apm.yml` `pyproject.toml` `go.mod` `pom.xml` → 每个包一个规范节点 + `depends_on` 边
- 文档：`.md .mdx .qmd .html .txt .rst .yaml .yml`（markdown 链接和 `[[wikilinks]]` 变成 `references` 边）
- Office `[office]`、Google Workspace `[google]`、PDF、图片 `.png .jpg .webp .gif`、音视频 `[video]`、YouTube/URL

**明确的限制**：

- `--postgres DSN` 抽 PostgreSQL schema 时，映射 tables / views / functions + 外键关系，但**列级细节不进图**。出处：本机 `graphify --help` 输出：`column-level detail is not represented in the graph`
- `.dm/.dme` 需 `graphifyy[dm]`，`.ml/.mli` 需 `graphifyy[ocaml]`——不装 extra 就解析不了
- Google Drive 的 `.gdoc/.gsheet/.gslides` 只是快捷方式指针不是内容，必须装并登录 `gws` CLI 加 `--google-workspace` 才行
- 除代码以外的一切都要走 LLM API（`README.md:370`）
- 图文件路径有安全校验：必须解析到 `graphify-out/` 里面（`Graphify-Labs/graphify:ARCHITECTURE.md:89`，`validate_graph_path()`）

---

## 三、`graphify-out` 目录结构

目录名可用环境变量 `GRAPHIFY_OUT` 覆盖，接受相对名（`"graphify-out-feature"`）或绝对路径（`"/shared/graphify-out"`），用于 worktree 或共享输出场景。出处：`Graphify-Labs/graphify:graphify/paths.py:26`

本机真实目录（`/Users/luoxin/persons/scripts/graphify-out/`，2026-09-10）：

```
graphify-out/
├── graph.json                    5.6 MB   全量图数据
├── graph.html                    4.7 MB   可交互网页图
├── GRAPH_REPORT.md               72 KB    人读的重点摘要
├── manifest.json                 45 KB    增量重建用的文件指纹表
├── .graphify_analysis.json       227 KB   聚类分析结果
├── .graphify_labels.json         7.4 KB   社区 ID → 名字
├── .graphify_labels.json.sig     7.4 KB   社区成员签名，用来判断标签是否过期
├── .graphify_root                29 B     项目根目录的绝对路径
├── .graphify_semantic_marker     23 B     语义抽取花了多少 token
├── cache/                                 缓存
│   ├── ast/v0.9.57-s2/                    AST 缓存，按版本+schema 分命名空间
│   ├── semantic-deep/p<fingerprint>/      --mode deep 的语义缓存
│   ├── stat-index.json                    文件 stat/字数缓存
│   └── last_query_stamp                   上次查询时间戳
└── 2026-09-01/ 2026-09-02/ ... 2026-09-10/   自动备份快照（每个是当天覆盖前的完整副本）
```

逐项说明：

### 核心三件套（README 开门见山承诺的「三个文件」，`README.md:59-66`）

- **`graph.json`** — 全图。JSON 顶层 key 实测为 `['directed', 'multigraph', 'graph', 'nodes', 'links', 'hyperedges', 'built_at_commit']`（NetworkX node-link 格式）。本机这份 4878 个节点 / 9876 条边。
  - 节点样例：`{"id": "lib_archery_archeryerror", "label": "ArcheryError", "_callable": true, "_origin": "ast", "community": 0, "community_name": "test_archery.py", "file_type": "code", "source_file": "lib/archery.py", "source_location": "L53"}`
  - 边样例：`{"source": "...", "target": "...", "relation": "calls", "_origin": "ast", "confidence": "EXTRACTED", "confidence_score": 1.0, "context": "call", "source_file": "lib/ai_workflow.py", "source_location": "L74", "weight": 1.0}`
  - `confidence` 三档：`EXTRACTED`（真找到的）/ `INFERRED`（推出来的）/ `AMBIGUOUS`（不确定）。出处：`Graphify-Labs/graphify:README.md:335`
  - 路径常量：`graphify/paths.py:311` `return str(out_path("graph.json"))`
- **`graph.html`** — 浏览器直接打开，节点可点、可筛选、可搜索。写入点：`graphify/cli.py:2354`
- **`GRAPH_REPORT.md`** — 报告内容为：god nodes（最连通的概念）、surprising connections（跨文件/跨模块的意外关联，按意外程度排序）、the "why"（`# NOTE:` `# WHY:` `# HACK:` 注释和设计文档理由被抽成独立节点）、suggested questions（4-5 个这张图特别擅长回答的问题）、confidence tags。出处：`Graphify-Labs/graphify:README.md:328-336`；写入点 `graphify/cli.py:2324`

### 元数据 / 状态文件

- **`manifest.json`** — 增量重建的指纹表。实测每个 key 是相对路径，value 形如 `{"mtime": ..., "seen": ..., "ast_hash": "aa8085...", "semantic_hash": ""}`。README 明说它现在是可移植的（key 存相对路径、加载时重新锚定），所以可以放心提交，首次 checkout 不必全量重建。出处：`Graphify-Labs/graphify:README.md:439`
- **`.graphify_analysis.json`** — 聚类分析结果，实测顶层 key `['communities', 'cohesion', 'gods', 'surprises', 'tokens']`。写入点：`graphify/cli.py:2333`
- **`.graphify_labels.json`** — 社区 ID → 名字的映射（实测 `{"0": "test_archery.py", "1": "ProviderInfo", ...}`）。占位名形如 `Community N`，只有非占位名才算「被策展过」。
- **`.graphify_labels.json.sig`** — 社区成员签名。图被重新聚类后，同一个 community id 可能已经指向完全不同的一批节点，旧的 LLM 名字就错了；graphify 拿签名逐个社区比对，变了的用当前 hub 名字确定性地重命名，并提示你跑 `graphify label` 拿新的 LLM 名字。出处：`Graphify-Labs/graphify:graphify/cli.py:2170-2190`
- **`.graphify_root`** — 项目根目录的绝对路径。本机内容就是一行 `/Users/luoxin/persons/scripts`。它是权威来源，优先于「graph.json 的祖父目录」这个启发式推断。出处：`Graphify-Labs/graphify:graphify/build.py:427-430`
- **`.graphify_semantic_marker`** — 存在即代表这张图花过真实 LLM token。本机内容 `{"output_tokens": 7373}`。写入点：`graphify/cli.py:4475`
- **`needs_update`** — 标志文件，表示有待做的语义重抽取。出处：`graphify/cli.py:924`、`graphify/watch.py:2118`
- **`.graphify_python`** — 记录运行时该用哪个 Python 解释器，由 skill 写入、hook 读取。出处：`Graphify-Labs/graphify:graphify/hooks.py:37-40`；README 也警告：如果它指向的环境和 `pip install` 装包的环境不一致，会 `ModuleNotFoundError`，所以推荐 `uv tool install` / `pipx`（`README.md:202`）

### `cache/`

- **`cache/ast/v{version}-s{schema}/`** — AST 缓存，按 graphify 版本 + 缓存 key schema 分命名空间，因为它依赖 extractor 代码而不只是文件内容。本机是 `v0.9.57-s2`。
- **`cache/semantic/`** 和 **`cache/semantic-deep/`** — 语义缓存。**故意不按版本分命名空间**（重抽取要花 LLM 钱）。加了 `prompt_fp` 时会再分一层 `p{fingerprint}/`（本机存在 `semantic-deep/pd68e17f4cee0`），把条目归因到产生它的那个 prompt。出处：`Graphify-Labs/graphify:graphify/cache.py:930-960`
- **`cache/stat-index.json`** — 文件 stat / 字数缓存。出处：`graphify/cache.py:321-325`
- **`cache/last_query_stamp`** — 记录 agent「最近被 graphify 定向过」的时间戳。严格模式的 hook 在这个戳还新鲜时（`GRAPHIFY_HOOK_STRICT_TTL`，默认 1800 秒）不拦截文件读取。出处：`graphify/cli.py:687-708`
- **`cache/hook_sessions/<sid>.denied`** — 每个 session 最多被严格模式拦一次的标记文件，用 `O_EXCL` 原子创建，超过 24 小时的自动清理。出处：`graphify/cli.py:712-730`

### 日期子目录（`2026-09-01/` 这种）

**自动备份快照**，不是什么增量归档。触发条件：`graph.json` 存在，且满足以下任一条——`.graphify_semantic_marker` 在（这张图花过真金白银的 LLM token），或 `.graphify_labels.json` 里至少有一个非占位的社区名（被人或 skill 策展过）。备份目录名就是 `date.today().isoformat()`。设 `GRAPHIFY_NO_BACKUP=1` 可关掉；备份失败只打警告，绝不阻塞写入。

备份的文件清单硬编码在 `_BACKUP_ARTIFACTS`：`graph.json`、`GRAPH_REPORT.md`、`.graphify_labels.json`、`.graphify_analysis.json`、`manifest.json`、`.graphify_semantic_marker`、`cost.json`。出处：`Graphify-Labs/graphify:graphify/export.py:24-70`

### 按需出现的其它子目录

本机这份没有，但源码里明确会写：

- **`memory/`** — `graphify save-result` 存的问答记录，文件名 `query_<YYYYMMDD_HHMMSS>_<uuid8>_<slug>.md`，YAML frontmatter 里有 `type` / `date` / `question` / `contributor` / 可选的 `outcome` 和 `correction`。下次 `--update` 时会被抽进图，形成反馈闭环。出处：`Graphify-Labs/graphify:graphify/ingest.py:276-320`
- **`reflections/LESSONS.md`** — `graphify reflect` 把 `memory/` 里的 outcome 聚合出来的经验文档，默认路径见 `graphify/cli.py:1499`；git hook 也会自动跑（`graphify/hooks.py:192`）
- **`.graphify_learning.json`** — work-memory 叠加层，记录「优先来源」，报告生成时以 display-only 方式合并进来。由 `graphify reflect --graph ...` 写出。出处：`graphify/report.py:43`、`graphify/cli.py:1765`、`README.md:703`
- **`converted/`** — Google Workspace 快捷方式导出成的 Markdown 边车文件。出处：`README.md:372`
- **`transcripts/`** — 音视频转出来的文本。出处：`Graphify-Labs/graphify:graphify/transcribe.py:15`
- **`cost.json`** — LLM 花费记录，README 明确建议 **gitignore 掉，只留本地**。出处：`README.md:436`
- **`merged-graph.json`** — `graphify merge-graphs` 的默认输出。出处：`graphify/cli.py:2607`
- **`GRAPH_TREE.html`** — `graphify tree` 的默认输出（D3 v7 可折叠树）。出处：`graphify/cli.py:2531`

---

## 四、产出物清单

按「常驻 / 按需 / 本地专属 / 仓库外」四类分。

### 4.1 常驻产出物（每次构建都写，应提交进 git）

| 产出物 | 类型 | 格式 |
|---|---|---|
| `graph.json` | 图数据 | JSON，NetworkX node-link |
| `graph.html` | 可视化 | 自包含 HTML |
| `GRAPH_REPORT.md` | 报告 | Markdown |
| `manifest.json` | 索引 | JSON，相对路径 → mtime/hash |
| `.graphify_analysis.json` | 索引 | JSON，communities/cohesion/gods/surprises/tokens |
| `.graphify_labels.json` + `.sig` | 索引 | JSON |
| `.graphify_root` / `.graphify_semantic_marker` | 状态标记 | 纯文本 / 小 JSON |

### 4.2 按需产出物（跑对应命令才有）

- 导出类：`graph.svg`（`export svg`）、GraphML（`export graphml`）、Cypher 脚本或直接推进 Neo4j / FalkorDB（`export neo4j` / `export falkordb`）、Obsidian vault + canvas（`export obsidian`）、wiki markdown（`export wiki`）、`<project>-callflow.html`（`export callflow-html`，Mermaid 架构/调用流，支持 `--lang auto|zh-CN|en`）、`GRAPH_TREE.html`（`tree`）
- 反馈闭环类：`memory/*.md`、`reflections/LESSONS.md`、`.graphify_learning.json`
- 输入转换类：`converted/*.md`、`transcripts/`、`./raw`（`graphify add <url>` 的落点）
- 跨仓库：`merged-graph.json`

### 4.3 本地专属（建议 gitignore）

- `cost.json` — README 明确点名（`README.md:436`）
- `cache/` — README 说可选：提交换速度，不提交保持仓库小（`README.md:437`）

### 4.4 写在 `graphify-out/` 之外的东西

- `~/.graphify/global-graph.json` — 全局跨仓图，`graphify global add/remove/list/path` 维护。出处：本机 `graphify --help`
- `~/.graphify/repos/<owner>/<repo>/` — `graphify clone` 的默认落点
- `~/.cache/graphify-queries.log` — 查询日志，**默认关闭**（no-telemetry 姿态），只有设了 `GRAPHIFY_QUERY_LOG=<path>` 或 `GRAPHIFY_QUERY_LOG_ENABLE=1` 才写；`GRAPHIFY_QUERY_LOG_DISABLE=1` 永远优先关掉。JSONL 格式。出处：`Graphify-Labs/graphify:graphify/querylog.py:20-60`
- 各平台的集成文件：`CLAUDE.md` 段落 + PreToolUse hook、`CODEBUDDY.md` + `.codebuddy/settings.json`、`AGENTS.md` 段落、`.cursor/rules/graphify.mdc`、`GEMINI.md` + BeforeTool hook、`~/.copilot/skills/`、`.github/copilot-instructions.md`、`.kiro/skills/`、`~/.pi/agent/skills/`、`~/.config/devin/skills/` 等。出处：本机 `graphify --help` + `README.md:316`
- git hooks：post-commit / post-checkout + 一个 `graph.json` 的 union merge driver。出处：`README.md:442-450`

---

## 五、未确认项

1. `需要:` **`cache/semantic/`（非 deep）目录本机没有实例**。源码 `graphify/cache.py:944` 明确说它存在（`graphify-out/cache/semantic/`），但本机只有 `semantic-deep/`。推断是本机这份图用的是 `--mode deep`；没有实测证据能确认非 deep 目录的内容格式。
2. `需要:` **`cost.json` 的字段结构未确认**。全仓 grep 只在 `graphify/export.py:32` 的备份清单里出现，没找到写入它的代码位置。可能在 `llm.py`（159 KB，未逐行读）里，也可能是企业版功能。
3. `推测:` **`hyperedges` 字段的语义**。`graph.json` 顶层实测有这个 key，但本次没有去 `build.py`（110 KB）里确认它记录的是什么（超边 = 一条边连接两个以上节点）。
4. `需要:` **benchmark 数字（LOCOMO recall@10 0.497 等）来自项目自己的 `BENCHMARKS.md`**，属于自测自评，没有第三方复现证据。原文：`Graphify-Labs/graphify:README.md:118-129` + `BENCHMARKS.md`。
5. `需要:` **`graphify prs` / MCP 的 PR 系列 tool 需要什么 GitHub 认证**未确认，本次没跑（会真的打 GitHub API）。源码在 `graphify/prs.py`（29 KB），未细读。
6. `推测:` **`.graphify_labels.json.sig` 里签名的具体算法**（`community_member_sigs`）没读实现，只确认了它的用途是判断社区成员是否变化。定义在 `graphify/cluster.py`。

---

## 附：本机现状

- 已安装版本：`graphify 0.9.57`（`/Users/luoxin/.local/bin/graphify`）
- **本机 Claude skill 版本落后**：`warning: skill at /Users/luoxin/.claude/skills/graphify is from graphify 0.9.55, package is 0.9.57. Run 'graphify install --platform claude' to update it`（每次跑 graphify 都会打这条警告）
- 本机有 12 个 `graphify-out/` 目录：`/Users/luoxin/bcpay`、`/Users/luoxin/starpago`、`/Users/luoxin/zhibao`、`/Users/luoxin/persons/scripts`、`/Users/luoxin/cometapidev/code`、`/Users/luoxin/zhibao/tmtc_bg`、`/Users/luoxin/cometapidev/code/comet-api-backend`、`/Users/luoxin/persons/lyxamour/{ccplugin,aidog,linky,hotarubi,pamphlet}`
