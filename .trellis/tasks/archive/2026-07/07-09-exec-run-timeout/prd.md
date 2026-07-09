# PRD: exec.run 加默认 timeout 防命令挂起

## 背景

`push_canary` 在 go/pay-core 卡死 52 分钟无响应。根因：`lib/exec.py` 的 `run()` 用 `subprocess.run` 无 timeout，git 命令（push/merge/fetch）遇到 ssh credential prompt / 网络阻塞时无限挂起，Ctrl+C 也难救（`start_new_session=True`）。

## 目标

所有经 `exec.run` / `run_logged` / `retry_command` 的命令有超时兜底，网络命令挂起时超时报错而非死等。

## 设计

### `run()` 加 timeout 参数

```python
def run(cmd, *, cwd=None, check=False, capture_output=True, timeout=None) -> CompletedProcess:
```

- 默认 `timeout=None`（保持兼容，但不再推荐裸调）
- 超时抛 `CommandTimeout`（RuntimeError 子类），含命令 + timeout 秒数
- 超时后 kill 整个进程组（`start_new_session=True` 已隔离，用 `os.killpg`）

### 默认兜底 timeout

提供模块常量 `DEFAULT_TIMEOUT = 300`（5 分钟），覆盖绝大多数 git 操作。**不强制注入到 run()**——避免误杀 checkwork 的长 build。

调用方按场景传：
- git 网络命令（push/fetch/pull/clone）：`timeout=120`
- git 本地命令（merge/checkout/status）：`timeout=60`
- build 类（go build/cargo check）：不传（None）或显式大值

### git_workflow / batch_git 适配

- `_git()` 加 timeout 参数，默认 60s
- `update_branch`（pull/push）、`retry_command`（push）传网络超时
- merge 命令传 60s

### run_no_capture

同步加 timeout（checkwork 的 verbose build），超时 kill 进程组。

## 验收

1. `run(cmd, timeout=1)` 跑 `sleep 5` → 1s 后抛 `CommandTimeout`，进程被 kill
2. `run(cmd)`（不传）保持原行为（无超时）
3. git_workflow `_git` 网络命令默认带 timeout
4. 现有测试全过
5. ruff clean

## 范围

- `lib/exec.py`（核心）
- `lib/git_workflow.py`（_git/update_branch 加 timeout）
- `lib/git.py`（update_branch 透传）
- 可选 `tests/test_exec.py` 增超时测试
