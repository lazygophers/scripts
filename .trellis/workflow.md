# 开发工作流

---

## 核心原则

1. **先规划后编码** — 开始前先明确要做什么
2. **规范注入, 非记忆** — 指南通过 hook/skill 注入, 而非凭记忆回忆
3. **持久化一切** — 调研、决策、经验都写入文件; 会话会被压缩, 文件不会
4. **增量开发** — 一次一个任务
5. **捕获经验** — 每个任务结束后回顾并将新知识写回规范

---

## Trellis 系统

### 开发者身份

首次使用时初始化你的身份:

```bash
python3 ./.trellis/scripts/init_developer.py <your-name>
```

创建 `.trellis/.developer` (gitignored) + `.trellis/workspace/<your-name>/`.

### 规范系统

`.trellis/spec/` 按包和层组织编码指南。

- `.trellis/spec/<package>/<layer>/index.md` — 入口, 含**开发前检查清单** + **质量检查**。实际指南在其指向的 `.md` 文件中。
- `.trellis/spec/guides/index.md` — 跨包思维指南。

```bash
python3 ./.trellis/scripts/get_context.py --mode packages   # 列出包 / 层
```

**何时更新规范**: 发现新模式/约定 · 需要固化的 bug 修复预防 · 新技术决策。

### 任务系统

每个任务在 `.trellis/tasks/{MM-DD-name}/` 下有独立目录, 包含 `prd.md`、`implement.jsonl`、`check.jsonl`、`task.json`、可选 `research/`、`info.md`。

```bash
# 任务生命周期
python3 ./.trellis/scripts/task.py create "<title>" [--slug <name>] [--parent <dir>]
python3 ./.trellis/scripts/task.py start <name>          # 设置活跃任务 (会话范围)
python3 ./.trellis/scripts/task.py current --source      # 显示活跃任务及来源
python3 ./.trellis/scripts/task.py finish                # 清除活跃任务 (触发 after_finish hooks)
python3 ./.trellis/scripts/task.py archive <name>        # 移动到 archive/{year-month}/
python3 ./.trellis/scripts/task.py list [--mine] [--status <s>]
python3 ./.trellis/scripts/task.py list-archive

# 代码规范上下文 (通过 JSONL 注入到 implement/check agent)
# `implement.jsonl` / `check.jsonl` 在 `task create` 时为支持 sub-agent 的
# 平台自动播种; AI 在 Phase 1.3 期间整理真正的规范 + 调研条目。
python3 ./.trellis/scripts/task.py add-context <name> <action> <file> <reason>
python3 ./.trellis/scripts/task.py list-context <name> [action]
python3 ./.trellis/scripts/task.py validate <name>

# 任务元数据
python3 ./.trellis/scripts/task.py set-branch <name> <branch>
python3 ./.trellis/scripts/task.py set-base-branch <name> <branch>    # PR 目标分支
python3 ./.trellis/scripts/task.py set-scope <name> <scope>

# 层级 (parent/child)
python3 ./.trellis/scripts/task.py add-subtask <parent> <child>
python3 ./.trellis/scripts/task.py remove-subtask <parent> <child>

# PR 创建
python3 ./.trellis/scripts/task.py create-pr [name] [--dry-run]
```

> 运行 `python3 ./.trellis/scripts/task.py --help` 查看权威、最新的命令列表。

**当前任务机制**: `task.py create` 创建任务目录, 并在有会话标识时自动设置会话级活跃任务指针, 使 planning 面包屑立即生效。`task.py start` 写入相同指针 (已设置时幂等), 并将 `task.json.status` 从 `planning` 翻转为 `in_progress`。状态存储在 `.trellis/.runtime/sessions/` 下。如果没有来自 hook 输入的 context key、`TRELLIS_CONTEXT_ID` 或平台原生会话环境变量, 则没有活跃任务, `task.py start` 会报错并提示会话标识设置方式。`task.py finish` 删除当前会话文件 (status 不变)。`task.py archive <task>` 写入 `status=completed`, 将目录移至 `archive/`, 并删除仍指向已归档任务的运行时会话文件。

### 工作空间系统

记录每个 AI 会话用于跨会话追踪, 存储在 `.trellis/workspace/<developer>/`。

- `journal-N.md` — 会话日志。**每个文件最多 2000 行**; 超出时自动创建 `journal-(N+1).md`。
- `index.md` — 个人索引 (总会话数、最近活跃时间)。

```bash
python3 ./.trellis/scripts/add_session.py --title "Title" --commit "hash" --summary "Summary"
```

### 上下文脚本

```bash
python3 ./.trellis/scripts/get_context.py                            # 完整会话运行时
python3 ./.trellis/scripts/get_context.py --mode packages            # 可用包 + 规范层
python3 ./.trellis/scripts/get_context.py --mode phase --step <X.Y>  # 工作流步骤详细指南
```

---

## 阶段索引

```
Phase 1: Plan    → 明确要做什么 (brainstorm + 调研 → prd.md)
Phase 2: Execute → 编写代码并通过质量检查
Phase 3: Finish  → 提炼经验 + 收尾
```

[workflow-state:no_task]
没有活跃任务。**A 直接回答** — 纯问答 / 解释 / 查找 / 闲聊; 无文件写入 + 单行回答 + 仓库读取 ≤ 2 个文件 → AI 自行判断, 无需覆盖。
**B 创建任务** — 任何实现 / 代码修改 / 构建 / 重构工作。入口序列: (1) `python3 ./.trellis/scripts/task.py create "<title>"` 创建任务 (status=planning, 面包屑切换到 [workflow-state:planning] 进入 brainstorm + jsonl 阶段指导) → (2) 加载 `trellis-brainstorm` skill 与用户讨论需求并迭代 prd.md → (3) prd 完成且 jsonl 整理好后, 运行 `task.py start <task-dir>` 进入 [workflow-state:in_progress] 进入实现骨架。对于调研密集型工作, 派遣 `trellis-research` 子代理 — 主代理不得内联做 3 次以上 WebFetch / WebSearch / `gh api` 调用。**"看起来很小"不是将 B 降级为 A 或 C 的理由**。
**C 内联修改** (仅限当轮, B 的逃生通道) — 用户当前消息**必须**包含以下之一: "skip trellis" / "no task" / "just do it" / "don't create a task" / "跳过 trellis" / "别走流程" / "小修一下" / "直接改" / "先别建任务" → 简要确认 ("ok, 本轮跳过 trellis 流程"), 然后内联执行。**未看到这些短语时绝对不能自行内联**; 不要编造用户从未说过的覆盖指令。

<!-- trellisx:start:no_task -->
trellisx 规约 (强化原生判定, 不切断建 task 路径): 本项目愿景 = 除极简任务外, 一律走 Trellis task 执行。

**作用域边界 — 何时建 task (执行规约的触发前提)**:

| 特征 | 判定 |
| --- | --- |
| 纯查询 / 文档阅读 / 问答 (无改动) | 豁免, 不建 task |
| 单文件单处改, ≤20 行且位置已知 | 豁免 |
| 跨 ≥2 文件 / 单文件多处 / 多步骤改 | **必建 task** |
| 需外部调研 (库选型/方案对比) 或产出文档交付 | **必建 task** (调研为 research subtask) |
| 边界模糊 | **MUST AskUserQuestion 由用户裁定** |

**无 task → 不进执行编排 → 执行规约豁免**。Planning 阶段不派执行 subagent: brainstorm 主线由 main 同步前台 (subagent 不能 AskUserQuestion); 纯信息调研可派 trellis-research 并行, 但设计决策由 main 汇总裁定。

- 表中"必建 task"的请求 → **默认创建 task** (不是"倾向建", 是默认就建; 仅用户明确拒绝才 inline)。建议直接用 AskUserQuestion 征得同意并进入 planning, 而非自行 inline。
- "边界模糊"行 → **MUST 主动用 AskUserQuestion 问用户** "本轮是否创建 Trellis task?", 禁默认跳过 / 禁自行替用户决定。
- 判断"新建 task"还是"并入现有 task"时, 读 `.trellis/task.md` 看板对照现有任务 (id/名称/描述/状态) 辅助判断。
原生的「先分类 + 征得同意才建」不变 — 但默认倾向从"可建可不建"上调为"默认建, 除非极简或用户拒绝"。
<!-- trellisx:end:no_task -->
[/workflow-state:no_task]

### Phase 1: 规划
- 1.0 创建任务 `[required · once]` (仅 `task.py create`; 状态进入 planning)
- 1.1 需求探索 `[required · repeatable]`
- 1.2 调研 `[optional · repeatable]`
- 1.3 配置上下文 `[required · once]`
- 1.4 激活任务 `[required · once]` (运行 `task.py start`; status → in_progress)
- 1.5 完成标准

[workflow-state:planning]
加载 `trellis-brainstorm` skill 并与用户迭代 prd.md。
Phase 1.3 (必做, 一次): 在 `task.py start` 之前, 你必须整理 `implement.jsonl` 和 `check.jsonl` — 列出子代理需要的规范/调研文件, 以便注入正确的上下文。仅当 jsonl 已有 agent 整理的条目时可跳过 (仅有播种的 `_example` 行不算)。
然后运行 `task.py start <task-dir>` 将状态翻转为 in_progress。
调研输出**必须**落在 `{task_dir}/research/*.md`, 由 `trellis-research` 子代理撰写。主代理不应内联 WebFetch / WebSearch — PRD 仅链接到调研文件。

<!-- trellisx:start:planning -->
trellisx 规划规约 (启用判定跟随 trellis 原生 parent/child 语义, 不看数量):

⚙️ **规划同步前台 (交互式, 禁自行凭空设计)**: planning 含 `trellis-brainstorm` **逐问用户**的交互, subagent 不能与用户对话, 故 **planning 由 main 同步前台执行**, 不派 subagent。分工: **`trellis-brainstorm` 为主导** (main 同步逐问用户做需求探索 + 方案设计 + 边界, 产出 prd/design); **`trellisx-orchestrate` 仅管执行层编排** (实际执行的 subagent 职责划分、并行/依赖、资源互斥, 产出 implement.md), **不用它做需求/方案设计**。产物评审 (`AskUserQuestion`) 由 main 亲做。注: exec/check 等非交互实质工作走 subagent 执行 (异步与否按需自定, 不强制)。

判定: 本请求是否含**多个独立可验收交付** (各自可独立 plan/implement/check/archive)?
- **是 (多交付)** → 拆为 parent + child tasks (trellis 原生 `task.py create --parent`)。每个 child 独立 worktree; PRD MUST 含 mermaid 调度图显式标并行组 + 依赖箭头; child 间依赖写进 child 自己的 prd.md/implement.md (非树位置隐含)。**执行统一由 `trellis-implement` 入口调度** (main 派 trellis-implement, 由其对各 subtask 派专用 subagent 并行执行), main 不直接派 subtask agent。
- **否 (单一交付)** → 轻量单 task inline, **不强制拆 subtask**。仍走单 worktree 隔离。

拆分目的 = 让独立可验收交付各自隔离 + 最大化并行, 缩短关键路径; 不是为凑数量。详见 trellisx-orchestrate skill。

task 创建后, 用 `trellisx-workspace` 及时更新 `.trellis/task.md` 看板表 (新增/更新该任务行)。
<!-- trellisx:end:planning -->
[/workflow-state:planning]

### Phase 2: 执行
- 2.1 实现 `[required · repeatable]`
- 2.2 质量检查 `[required · repeatable]`
- 2.3 回滚 `[on demand]`

[workflow-state:in_progress]
**流程**: trellis-implement → trellis-check → trellis-update-spec → commit (Phase 3.4) → `/trellis:finish-work`。
**主会话默认 (无覆盖)**: 派遣 `trellis-implement` / `trellis-check` 子代理 — 主代理默认不直接编辑代码。Phase 3.4 commit (必做, 一次): trellis-update-spec 之后, 或实现可验证完成后, 主代理**驱动 commit** — 在面向用户的文本中说明 commit 计划, 然后运行 `git commit` — 在建议 `/trellis:finish-work` 之前。`/finish-work` 拒绝在工作树脏时运行 (`.trellis/workspace/` 和 `.trellis/tasks/` 之外的路径)。
**子代理自豁免**: 如果你已作为 `trellis-implement` 运行, 直接从加载的任务上下文实现, 不要再派 `trellis-implement`; 如果你已作为 `trellis-check` 运行, 直接审查/修复, 不要再派 `trellis-check`。默认派遣规则仅适用于主会话。
**子代理派遣协议 (所有平台, 除 trellis-research 外的所有子代理)**: 派遣 `trellis-implement` / `trellis-check` 时, 你的派遣 prompt **必须**以一行开头: `Active task: <来自 \`task.py current\` 的任务路径>`。无例外。在 class-2 平台 (codex / copilot / gemini / qoder) 上, 子代理依赖此行, 因为没有 hook 注入任务上下文。在 class-1 平台 (claude / cursor / opencode / kiro / codebuddy / droid) 上, 该行通常是冗余的 — hook 直接注入上下文 — 但在 hook 失败时 (Windows + Claude Code PreToolUse 静默跳过、`--continue` 恢复、fork 分发、hooks 禁用等) 作为关键后备。`trellis-research` 不需要此行, 因为它无需任务绑定即可运行。
**内联覆盖** (仅限当轮, 子代理派遣的逃生通道): 用户当前消息**必须**明确包含以下之一: "do it inline" / "no sub-agent" / "你直接改" / "别派 sub-agent" / "main session 写就行" / "不用 sub-agent"。**未看到这些短语时绝对不能自行内联**; 不要编造用户从未说过的覆盖指令。

<!-- trellisx:start:in_progress -->
⛔ trellisx 执行硬规 (本 task 必守, 违反即流程错误):

0. **实质工作走 subagent 执行 (最高优先级)**: **概念分清** —— **task** = 任务记录 (`task.py create/start/finish/archive` 脚本), **main 同步跑**; **实质工作** (改源码、跑 check) **由 subagent 执行**。main **禁直接落地实质工作** —— 一律派 subagent (`Agent` 工具); main 只做编排 + task.py 脚本同步调用 + 用户交互决策 (`AskUserQuestion` subagent 不能做) + 完成即时回传 + 看板维护。是否 `run_in_background` 异步 / 并行 **按需自定, 不强制** (用户需要异步会自处理, 只需确保走 subagent)。每个 dispatch prompt 须 6 字段自包含 (目标/已知含 `Active task:`/范围/输出/验收/失败处理)。commit→merge→archive 由 `task.py finish` + `after_finish` hook 自动 (见 #4), 非派 agent。**🔴🛑 "派 agent" = 真实调用 `Agent` 工具, 不是叙述 (最易踩)**: 每个派 agent 动作 MUST 在**同一回复**产生真实 `Agent` tool_use; **严禁本回复无 `Agent` 工具调用就回传"已派出/agent 在做"** —— 宣称 ≠ 调用 = 幻觉跳步; 同理 "已建 task/看板已登记/worktree 已建" 必须是真实跑过 `task.py`/`trellisx-workspace`/hook 的结果。回传前自检本回复确有对应 tool_use, 无则先调用再回传。
1. **task 在 worktree 内执行 (task 级隔离)**: `task.py start` 后 hook 自动建 worktree (`.worktrees/<name>`); 本 task 全部执行在 worktree 内进行, 主工作区保持干净。默认一个 task 一个 worktree; 仅冲突型并行 subtask 才各开子 worktree。
2. **实施派发模型 (main → trellis-implement → subagent)**: 进入实施, main **派一个 `trellis-implement` 子代理**执行实施阶段, **main 禁直接派 subtask agent、禁直接写源码**。
3. **轻量模式** (单 subtask): main 仍**派 `trellis-implement`**, 由其内联直做。
4. **强制自动收尾**: `trellis-check` 通过后, AI **强制收尾**: ① AI 层: TaskList 查悬挂 Workflow/agent, TaskStop 关闭 ② git 层: `python3 .trellis/scripts/task.py finish` → after_finish hook 自动跑 `trellisx-finish.py` (commit→merge --no-ff→archive→销 worktree)。合并冲突 → 脚本 abort + 报冲突, AI MUST 检 finish 输出有无冲突告警。未 archive = 流程未闭环, 禁宣告 Done。
5. **及时维护 task.md 看板**: start / 阶段推进 / archive 后, MUST 用 `trellisx-workspace` 更新看板行。
6. 收每个 agent 返回立即回传用户进度。
7. **任务中途修正路由**: 属当前任务 → 先改真值文档 → SendMessage 通知在跑 agent 纠偏; 独立新任务 → 走强推 task; 判不准 → AskUserQuestion。
<!-- trellisx:end:in_progress -->
[/workflow-state:in_progress]

### Phase 3: 收尾
- 3.1 质量验证 `[required · repeatable]`
- 3.2 调试回顾 `[on demand]`
- 3.3 规范更新 `[required · once]`
- 3.4 提交变更 `[required · once]`
- 3.5 收尾提醒

### 规则

1. 确定当前所处阶段, 然后从该阶段的下一步继续
2. 在每个阶段内按顺序执行; `[required]` 步骤不可跳过
3. 阶段可以回滚 (例如执行阶段发现 prd 缺陷 → 返回规划修复, 再重新进入执行)
4. 标记 `[once]` 的步骤, 如果输出已存在则跳过; 不要重复运行

### Skill 路由

当用户请求匹配以下意图之一时, 先加载对应 skill (或派遣对应子代理) — 不要跳过 skill。

[Claude Code]

| 用户意图 | 路由 |
|---|---|
| 需要新功能 / 需求不明确 | `trellis-brainstorm` |
| 即将编写代码 / 开始实现 | 按 Phase 2.1 派遣 `trellis-implement` 子代理 |
| 完成编写 / 想要验证 | 按 Phase 2.2 派遣 `trellis-check` 子代理 |
| 卡住了 / 同一个 bug 修了多次 | `trellis-break-loop` |
| 规范需要更新 | `trellis-update-spec` |

**为什么 `trellis-before-dev` 不在此表中:** 编写代码的不是你 — 而是 `trellis-implement` 子代理。子代理平台通过 `implement.jsonl` 注入 / prelude 获取规范上下文, 而非在主线程加载 `trellis-before-dev`。

[/Claude Code]

### 不要跳过 skill

[Claude Code]

| 你在想什么 | 为什么是错的 |
|---|---|
| "这很简单, 我直接在主线程写" | 派遣 `trellis-implement` 是低成本路径; 跳过它会让你在主线程写代码并丢失规范上下文 — 子代理会注入 `implement.jsonl`, 你不会 |
| "我在规划模式已经想清楚了" | 规划模式的输出存在于内存中 — 子代理看不到; 必须持久化到 prd.md |
| "我已经知道规范了" | 规范可能在你上次阅读后已更新; 子代理获得的是最新版本, 你可能不是 |
| "先写代码, 后检查" | `trellis-check` 能发现你自己注意不到的问题; 越早越便宜 |

[/Claude Code]

### 加载步骤详情

在每个步骤, 运行以下命令获取详细指导:

```bash
python3 ./.trellis/scripts/get_context.py --mode phase --step <step>
# 例如: python3 ./.trellis/scripts/get_context.py --mode phase --step 1.1
```

---

## Phase 1: 规划

目标: 明确要构建什么, 产出清晰的需求文档和实现所需的上下文。

#### 1.0 创建任务 `[required · once]`

创建任务目录 (状态进入 `planning`, 有会话标识时会话活跃任务指针自动指向新任务):

```bash
python3 ./.trellis/scripts/task.py create "<task title>" --slug <name>
```

`--slug` 仅是人类可读名称。**不要**包含 `MM-DD-` 日期前缀; `task.py create` 会自动添加该前缀。

此命令成功后, 每轮面包屑自动切换到 `[workflow-state:planning]`, 告知 AI 进入 brainstorm + jsonl 整理阶段。

⚠️ **这里只运行 `create` — 不要同时运行 `start`**。`start` 会将状态翻转为 `in_progress`, 在 brainstorm + jsonl 完成之前就切换面包屑到实现阶段 — AI 会静默跳过它们。将 `start` 留到步骤 1.4, 在 jsonl 整理完成后执行。

当 `python3 ./.trellis/scripts/task.py current --source` 已指向一个任务时跳过。

#### 1.1 需求探索 `[required · repeatable]`

加载 `trellis-brainstorm` skill 并按该 skill 的指导与用户交互式探索需求。

brainstorm skill 将指导你:
- 一次问一个问题
- 优先调研而非提问用户
- 优先提供选项而非开放式问题
- 每次用户回答后立即更新 `prd.md`

需求变更时返回此步骤并修改 `prd.md`。

#### 1.2 调研 `[optional · repeatable]`

调研可以在需求探索期间的任何时间进行。不限于本地代码 — 你可以使用任何可用工具 (MCP 服务器、skill、网络搜索等) 查找外部信息, 包括第三方库文档、行业实践、API 参考等。

[Claude Code]

派遣调研子代理:

- **Agent 类型**: `trellis-research`
- **任务描述**: Research <具体问题>
- **关键要求**: 调研输出**必须**持久化到 `{TASK_DIR}/research/`

[/Claude Code]

**调研产出约定**:
- 每个调研主题一个文件 (例如 `research/auth-library-comparison.md`)
- 在文件中记录第三方库使用示例、API 参考、版本约束
- 记录你发现的相关规范文件路径以便后续参考

brainstorm 和调研可以自由穿插 — 暂停调研一个技术问题, 然后返回与用户对话。

**核心原则**: 调研输出必须写入文件, 不能只留在聊天中。会话会被压缩; 文件不会。

#### 1.3 配置上下文 `[required · once]`

[Claude Code]

整理 `implement.jsonl` 和 `check.jsonl`, 以便 Phase 2 子代理获得正确的规范上下文。这些文件在 `task create` 时已用一行自描述 `_example` 行播种; 你的任务是填入真正的条目。

**位置**: `{TASK_DIR}/implement.jsonl` 和 `{TASK_DIR}/check.jsonl` (已存在)。

**格式**: 每行一个 JSON 对象 — `{"file": "<path>", "reason": "<why>"}`。路径相对于仓库根。

**填入什么**:
- **规范文件** — `.trellis/spec/<package>/<layer>/index.md` 及与此任务相关的具体指南文件 (`error-handling.md`、`conventions.md` 等)
- **调研文件** — 子代理需要查阅的 `{TASK_DIR}/research/*.md`

**不要填入什么**:
- 代码文件 (`src/**`、`packages/**/*.ts` 等) — 这些由子代理在实现期间读取, 不在此预注册
- 你即将修改的文件 — 同理

**两个文件的分工**:
- `implement.jsonl` → 实现子代理编写正确代码所需的规范 + 调研
- `check.jsonl` → 检查子代理的规范 (质量指南、检查约定, 需要时包含相同调研)

**如何发现相关规范**:

```bash
python3 ./.trellis/scripts/get_context.py --mode packages
```

列出每个包及其规范层及路径。选择与此任务领域匹配的条目。

**如何追加条目**:

直接在编辑器中编辑 jsonl 文件, 或使用:

```bash
python3 ./.trellis/scripts/task.py add-context "$TASK_DIR" implement "<path>" "<reason>"
python3 ./.trellis/scripts/task.py add-context "$TASK_DIR" check "<path>" "<reason>"
```

有真正的条目后可删除播种的 `_example` 行 (可选 — 消费方会自动跳过)。

跳过条件: `implement.jsonl` 已有 agent 整理的条目 (仅播种行不算)。

[/Claude Code]

#### 1.4 激活任务 `[required · once]`

prd.md 完成且 1.3 jsonl 整理完成后, 将任务状态翻转为 `in_progress`:

```bash
python3 ./.trellis/scripts/task.py start <task-dir>
```

此命令成功后, 面包屑自动切换到 `[workflow-state:in_progress]`, Phase 2 / 3 随之继续。

如果 `task.py start` 报会话标识错误 (没有来自 hook 输入的 context key、`TRELLIS_CONTEXT_ID` 或平台原生会话环境变量), 按错误提示设置会话标识后重试。

#### 1.5 完成标准

| 条件 | 必需 |
|------|:---:|
| `prd.md` 存在 | ✅ |
| 用户确认需求 | ✅ |
| `task.py start` 已运行 (status = in_progress) | ✅ |
| `research/` 有产出 (复杂任务) | 推荐 |
| `info.md` 技术设计 (复杂任务) | 可选 |

[Claude Code]

| `implement.jsonl` 有 agent 整理的条目 (不仅仅是播种行) | ✅ |

[/Claude Code]

---

## Phase 2: 执行

目标: 将 prd 转化为通过质量检查的代码。

#### 2.1 实现 `[required · repeatable]`

[Claude Code]

派遣实现子代理:

- **Agent 类型**: `trellis-implement`
- **任务描述**: 按 prd.md 实现需求, 参考 `{TASK_DIR}/research/` 下的资料; 最后运行项目的 lint 和 type-check
- **派遣 prompt 保护**: 告知被派遣的 agent 它已是 `trellis-implement` 子代理, 必须直接实现, 不要再派 `trellis-implement` / `trellis-check`。

平台 hook/plugin 自动处理:
- 读取 `implement.jsonl` 并将引用的规范文件注入 agent prompt
- 注入 prd.md 内容

[/Claude Code]

#### 2.2 质量检查 `[required · repeatable]`

[Claude Code]

派遣检查子代理:

- **Agent 类型**: `trellis-check`
- **任务描述**: 按规范和 prd 审查所有代码变更; 直接修复发现的问题; 确保 lint 和 type-check 通过
- **派遣 prompt 保护**: 告知被派遣的 agent 它已是 `trellis-check` 子代理, 必须直接审查/修复, 不要再派 `trellis-check` / `trellis-implement`。

检查 agent 的职责:
- 按规范审查代码变更
- 自动修复发现的问题
- 运行 lint 和 typecheck 验证

[/Claude Code]

#### 2.3 回滚 `[on demand]`

- `check` 发现 prd 缺陷 → 返回 Phase 1, 修复 `prd.md`, 然后重新执行 2.1
- 实现出错 → 回退代码, 重新执行 2.1
- 需要更多调研 → 调研 (同 Phase 1.2), 将发现写入 `research/`

---

## Phase 3: 收尾

目标: 确保代码质量, 捕获经验, 记录工作。

#### 3.1 质量验证 `[required · repeatable]`

加载 `trellis-check` skill 并进行最终验证:
- 规范合规性
- lint / type-check / tests
- 跨层一致性 (变更跨层时)

发现问题 → 修复 → 重新检查, 直到通过。

#### 3.2 调试回顾 `[on demand]`

如果此任务涉及反复调试 (同一个问题修了多次), 加载 `trellis-break-loop` skill 来:
- 分类根本原因
- 解释为什么早期修复失败
- 提出预防措施

目标是捕获调试经验, 避免同类问题再次发生。

#### 3.3 规范更新 `[required · once]`

加载 `trellis-update-spec` skill 并审查此任务是否产生了值得记录的新知识:
- 新发现的模式或约定
- 遇到的坑
- 新的技术决策

相应更新 `.trellis/spec/` 下的文档。即使结论是"无需更新", 也要走一遍判断过程。

#### 3.4 提交变更 `[required · once]`

AI 驱动此任务代码变更的批量提交, 以便 `/finish-work` 能顺利运行。目标: 先产出工作 commit, 然后记账 (archive + journal) commit 在之后 — 永不交错。

**逐步执行**:

1. **检查脏状态**:
   ```bash
   git status --porcelain
   ```
   快照每个脏路径。如果工作树干净, 跳到 3.5。

2. **学习 commit 风格** 从最近历史 (使草拟的消息风格匹配):
   ```bash
   git log --oneline -5
   ```
   注意前缀约定 (`feat:` / `fix:` / `chore:` / `docs:` ...)、语言 (中文/English) 和长度风格。

3. **将脏文件分为两组**:
   - **本次会话 AI 编辑的** — 你在本会话通过 Edit/Write/Bash 工具调用写入/编辑的文件。你知道改了什么和为什么。
   - **未识别的** — 你本会话未触碰的脏文件 (可能是用户的手动编辑、上一会话残留的 WIP、或无关工作)。不要静默包含这些文件。

4. **草拟 commit 计划**。将 AI 编辑的文件分组为逻辑 commit (每个连贯变更单元 1 个 commit, 不是每个文件 1 个 commit)。每个条目: `<commit message>` + 文件列表。在底部单独列出未识别的文件。

5. **展示计划一次, 一次性请求确认**。格式:
   ```
   Proposed commits (in order):
     1. <message>
        - <file>
        - <file>
     2. <message>
        - <file>

   Unrecognized dirty files (NOT in any commit — confirm include/exclude):
     - <file>
     - <file>

   Reply 'ok' / '行' to execute. Reply with edits, or '我自己来' / 'manual' to abort.
   ```

6. **确认后**: 按顺序为每批运行 `git add <files>` + `git commit -m "<msg>"`。不要 amend。不要 push。

7. **拒绝后** (用户回复 "不行" / "我自己来" / "manual" / 任何对计划的反对): 停止。不要尝试第二次计划。用户将手动提交; 他们确认后你跳到 3.5。

**规则**:
- 任何地方不要 `git commit --amend` — 三阶段三 commit 流程 (工作 commit → archive commit → journal commit)。
- 此步骤永远不要推送到远程。
- 如果用户想要不同的消息措辞但接受文件分组, 编辑消息并再次确认一次 — 但如果他们拒绝分组, 退出到手动模式。
- 批量计划是一次 prompt; 不要每个 commit 单独提示。

#### 3.5 收尾提醒

完成上述步骤后, 提醒用户可以运行 `/finish-work` 来收尾 (归档任务、记录会话)。

---

<!-- trellisx:start:finish_force -->
⛔ **强制自动收尾 (不是提醒)**: check 通过后, AI **必须**运行
`python3 .trellis/scripts/task.py finish` (或 `/trellis:finish-work`)。
`after_finish` hook **自动**执行: 提交 worktree → 合并 --no-ff 回主分支 → archive → 销毁 worktree。
**worktree 删除与合并是必须的, 非可选, 非「提醒用户去做」。**
- **收尾两层, 责任不同 (step⓪ AI 层先于 ① git 层)**:
  - **⓪ AI 层**: 跑收尾脚本前, 先确认本 task 的 Workflow / 后台 agent 任务已全部终止 (TaskList + TaskStop)。**`trellisx-finish.py` 只销 worktree, 不关 Workflow/Task**。
  - **① git 层**: commit → merge --no-ff → 销毁 worktree → archive。冲突则 abort + 报清单, 不强解。
  - 顺序: 先 ⓪ AI 层清悬挂 → 再 ① git 层 finish。
- 合并冲突 → 收尾脚本 abort + 报冲突 + 非 0 退出。AI MUST 检 finish 输出有无冲突告警。
- check 未过禁跑 finish; 未 archive = 流程未闭环, 禁宣告 Done。
- commit 为 owner 授权的强制动作。
- 手动兜底: `python3 .trellis/scripts/trellisx-finish.py [--task <tid>]` (幂等可重入)。
<!-- trellisx:end:finish_force -->
