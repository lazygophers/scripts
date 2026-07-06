# PRD：批量操作并行异步化

## 背景

`lib/batch_git.run_batch`（batch_git.py:103-178）现**串行** for 循环逐仓库执行 operation（push/merge/sync）。批量场景几十个仓库，git push/pull 网络等待累积，耗时长。

git 操作是 IO 密集（网络），各仓库间无依赖 → 可安全并行。

## 目标

1. `run_batch` 改并行执行（ThreadPoolExecutor，git 操作 IO 密集，asyncio 过度）。
2. 可配置并发上限（默认 4，env `BATCH_CONCURRENCY` 覆盖）。
3. 进度反馈适配并行：Rich Progress 或完成即打印（避免交错）。
4. 中断（Ctrl-C）正确传播，cancel pending。
5. 汇总（print_summary）不变，接并行收集的 BatchResult。

## 需求规格

### 1. 并行执行（lib/batch_git.py:run_batch）

替换串行 for 循环（行 145-170）为 ThreadPoolExecutor：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

concurrency = int(os.environ.get("BATCH_CONCURRENCY", "4"))
results: list[RepoResult] = [None] * len(repos)
with ThreadPoolExecutor(max_workers=concurrency) as pool:
    futures = {pool.submit(operation, repo, r_per, root): idx for idx, repo in enumerate(repos)}
    try:
        for fut in as_completed(futures):
            idx = futures[fut]
            repo = repos[idx]
            rel = repo.relative_to(root)
            try:
                status, detail = fut.result()
            except Exception as e:
                status, detail = "fail", str(e)
            results[idx] = RepoResult(name=str(rel), path=str(repo), status=status, detail=detail)
            # 完成即打印状态行（避免交错）
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        r.warn("\n用户中断，停止执行")
```

### 2. 线程安全的输出（per-repo buffer 方案）

调研结论（batch_git.py:202-286,317-391,405-469）：三个 `_one_factory` **重度用 `r` 打实时进度**（step/info/ok/warn/err），用户依赖看仓库逐步执行状态。方案 A（线程内不打印）**不可行** — 闭麦后失败时无法看 gitc 子进程上下文。

**采用 per-repo buffer**：每个仓库操作在线程内用独立 `io.StringIO` + Rich Console（专属），完成后主线程统一 flush 该仓库整段输出。这样：
- 线程内仍用 `r.step/r.info/...` 打（行为不变，只是 Console 重定向到 buffer）
- 完成序非提交序，每仓库整段不交错（线程内顺序完整）
- 失败时 gitc 子进程完整输出仍可见（在 buffer 里）

实现：`Reporter` 加 `from_buffer(buf)` 工厂或 `run_batch` 内构造 per-thread `Console(file=buf)` + Reporter。主线程 flush 后追一行状态（✔/✖/⏭ + name）。

### 3. 并发上限

`BATCH_CONCURRENCY` 环境变量，默认 4。文档（README/docs）注明。

### 4. 中断处理

`KeyboardInterrupt` → `cancel_futures=True`，已提交的尽量取消，running 的等完或强制。汇总只含已完成。

### 5. 进度反馈

并行下 `[i/N]` 无意义。改：
- 开始：`共 N 个仓库，并发 K`。
- 每完成一个：`✔ <name>` / `✖ <name> — detail` / `⏭ <name>`（完成即打印，顺序为完成序非提交序）。
- 可选：Rich Progress bar（完成数/总数）。MVP 先逐行打印。

## 改动范围

- `lib/batch_git.py`：run_batch 并行化 + 线程安全输出。
- 可能 `lib/ui.py`：若需线程安全辅助（否则不动，Rich Console 自带 lock）。
- `tests/test_batch_git.py`：并行模式测试（顺序无关断言、并发上限、中断）。
- `docs/` + `README.md`：注明 `BATCH_CONCURRENCY`。

## 依赖

- **依赖 push task 完结 + rich-beautify 完结**（同改 batch_git.py，三方冲突）。串行：push（在 check）→ rich-beautify → parallel-batch。

## 验收标准

- run_batch 并行执行多仓库（默认并发 4）。
- `BATCH_CONCURRENCY=N` 可调。
- 输出不交错（无线程竞态导致 Rich 表格断裂）。
- Ctrl-C 正确取消，汇总已完成的。
- 批量 push/merge/sync 总耗时显著低于串行（实测 N=10 仓库并发 4 vs 串行）。
- `python3 -m unittest discover -s tests -q` 全绿。
- 现有汇总格式（print_summary 列表式）不变。

## 待确认（用户离场，按最低惊讶 + 实用性定）

- [x] 并行模型：ThreadPoolExecutor（IO 密集，非 asyncio）。
- [x] 默认并发：4（git 网络打满平衡点）。
- [x] 进度：完成即打印逐行（MVP，非 Progress bar）。
- [x] 中断：cancel_futures，汇总已完成。

## 调研点（已闭合）

- `_push_one_factory`/`_merge_one_factory`/`_sync_one_factory` 对 `r`（Reporter）的依赖：**重度依赖**（每步 step/info/ok/warn/err），方案 A 不可行 → 改 per-repo buffer。详见第 2 节。
