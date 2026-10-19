# 0005. claude_session --all：扩现有命令而不是新建

- 状态：已接受（2026-10-19）
- 范围：lib/claude_session.py、lib/cli/claude_session.py、bin/claude_session、tests/

## 背景

用户要"扫描所有 Claude session，输出时间/标题/做的事情/session"。

需求涉及两类 Claude Code 持久化产物：

1. `~/.claude/sessions/<pid>.json`（14 个进程索引）—— 现有 `bin/claude_session` 读这个，输出 name/sessionId/cwd/status/start/active。
2. `~/.claude/projects/<hash>/<uuid>.jsonl`（2578 个、36 个项目）—— 真实会话日志，含 aiTitle/customTitle、type=user/assistant 消息流、cwd/gitBranch/version/...。

新需求本质上要扫的是 (2)。两个候选：

A. 全新 `bin/claude_all_session`，独立 `lib/cli/claude_all_session.py` + `lib/claude_all_session.py`。
B. 扩 `bin/claude_session`，加 `--all` flag 切到 jsonl 路径。

## 决策

选 **B**。两条路径（进程索引 vs jsonl 日志）字段、源、性能完全不同，但都属于"读 Claude Code 本机持久化数据并列表"这个真值；新建并列命令把同一真值的两个视角拆到两处 bin，调用方需要记得哪个管哪个。扩现有命令让 `--all` 做开关，行为集中在 `lib/cli/claude_session.py` 一处。

## 后果

- `lib/claude_session.py` 加一组新导出：`list_session_logs()` 扫 jsonl 头部 / `filter_session_logs()` 跑 grep+project；老 API（`list_sessions` / `filter_sessions`）不动。
- `lib/cli/claude_session.py` 加 `--all` / `--grep` / `--tail` 三 flag；现有 `-p` / `--project` / `-s` / `--session` / `-f` / `--format` 沿用，但 `-p` 在 `--all` 下的语义改为"仅当前 cwd 映射的那个 project-hash 目录"。
- 默认无 limit（不挡 `--all` 走 jsonl 全量 2578 的诉求）；未来加 `--limit N` 不破坏现在的不传即全量约定。
- AI shell 自动分流：人类环境默认 `table`，AI 环境默认 `tsv`（继承 `lib/ai_env.is_ai_shell_env`）。
- `--all` 路径必须保证启动预算不退化：实测扫 2578 个 jsonl 头部 < 几秒即够，不引入缓存层（缓存的失效/迁移成本 > 重扫开销）。

## 落选方案

- 全新 `bin/claude_all_session`：调用方需要切两个命令记两条路径；同一真值的两个视角不该被强制拆。
- `claude_session logs` 子命令：增加子命令层把"路径选择"埋在更深的 namespace，flag 形态（一层 `--all`）够用就比两层简单。