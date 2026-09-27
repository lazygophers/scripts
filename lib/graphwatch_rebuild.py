"""graphwatch 重建管线：进程内按 /graphify <path> --mode deep --wiki --update
的 runbook 跑一次增量重建（graphify 库源码调用，不起 CLI 子进程）。

runbook 出处：graphify skill（SKILL.md + references/update.md）；库函数
签名以安装的 graphifyy 包为准（graphify.detect / extract / build / cluster /
export / wiki / llm）。所有函数都收显式 root，不需要 chdir 到项目目录。

流程：detect_incremental（无变更直接返回）→ AST 抽取（code）+ 语义抽取
（doc/paper/image，配置了 backend 才跑，deep 模式）→ build_merge 并入
现有 graph.json（删除文件 prune）→ cluster → 社区命名（只补没名字的，见
graphwatch_artifacts）→ to_json（force=True，绕开 #479 收缩护栏）→ to_wiki → 其余导出物
（GRAPH_REPORT.md / graph.html / graph.graphml / GRAPH_TREE.html /
obsidian，见 graphwatch_artifacts.write_artifacts）→ save_manifest。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from lib import log as _log

# 语义抽取一轮全空（配额用尽 429、网关挂了）后，这个目录多久内不再调 LLM。
# graphify 对失败 chunk 只打 stderr 不抛异常，不退避的话每轮重建都把同一批文档
# 再发一遍（2026-09-27 实测：Token Plan 用量上限，scripts 10 篇 / zhibao 69 篇轮轮重发）。
# 进程内记忆：daemon 重启或前台 graphwatch rebuild 立即重试。
SEMANTIC_BACKOFF_SECS = 3600.0
_semantic_backoff: dict[Path, float] = {}

_EMPTY = {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}


def _acquire_rebuild_lock(root: Path):
    """非阻塞获取目录级重建锁；正在构建时返回 None。"""
    path = root / "graphify-out" / ".graphwatch-rebuild.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def _release_rebuild_lock(fd) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _semantic(files: list[Path], root: Path) -> dict:
    """语义抽取（deep 模式）：先读 graphify 语义缓存，只把没命中的文件交给 LLM。

    缓存读法照抄 graphify CLI（cli.py 的 check_semantic_cache 调用）：mode / prompt /
    cache_root 必须和 extract_corpus_parallel 写缓存时一致，否则永远读不到。
    内容回退、切分支再切回来时直接命中，不重复花 token。返回值多一个 cache_hits。
    backend 没配或调用失败 → 只剩缓存命中部分（其余 manifest 不盖章，下次变更重试）。
    """
    from graphify.cache import check_semantic_cache
    from graphify.llm import _extraction_system

    from lib.graphwatch_artifacts import backend_env
    from lib.graphwatch_config import load_config

    nodes, edges, hyperedges, uncached = check_semantic_cache(
        [str(f) for f in files], root=root, mode="deep",
        prompt=_extraction_system(deep=True), cache_root=root,
    )
    result = {"nodes": nodes, "edges": edges, "hyperedges": hyperedges,
              "input_tokens": 0, "output_tokens": 0, "cache_hits": len(files) - len(uncached)}
    cfg = load_config()
    if not uncached or not str(cfg.get("backend") or ""):
        return result
    if time.time() < _semantic_backoff.get(root, 0.0):
        return result

    from graphify.llm import extract_corpus_parallel

    try:
        with backend_env(cfg) as backend:
            fresh = extract_corpus_parallel(
                [Path(f) for f in uncached],
                backend=backend,
                api_key=str(cfg.get("api_key") or "") or None,
                model=str(cfg.get("model") or "") or None,
                root=root,
                deep_mode=True,
                cache_root=root,
            )
    except Exception as e:  # noqa: BLE001
        from lib.ui import Reporter

        Reporter().warn(f"graphwatch 语义抽取失败（{type(e).__name__}: {e}），本轮只用缓存 + AST")
        _semantic_backoff[root] = time.time() + SEMANTIC_BACKOFF_SECS
        _log.record("graphwatch.semantic_fail", logger="graphwatch-daemon", level="warning",
                    folder=str(root), files=len(uncached), error=f"{type(e).__name__}: {e}")
        return result
    if not fresh.get("nodes"):
        _semantic_backoff[root] = time.time() + SEMANTIC_BACKOFF_SECS
        _log.record("graphwatch.semantic_empty", logger="graphwatch-daemon", level="warning",
                    folder=str(root), files=len(uncached), backoff_secs=SEMANTIC_BACKOFF_SECS)
        return result
    for key in ("nodes", "edges", "hyperedges"):
        result[key] = result[key] + list(fresh.get(key) or [])
    for key in ("input_tokens", "output_tokens"):
        result[key] = fresh.get(key, 0)
    return result


def _oldest_first(files: list[Path]) -> list[Path]:
    """处理顺序：最久没改的目录先、目录内最久没改的文件先；最近在改的排最后。

    目录的「新旧」取它下面本轮变更文件里最新的 mtime。读不到 mtime（已删）当最旧。
    """
    def mtime(f: Path) -> float:
        try:
            return f.stat().st_mtime
        except OSError:
            return 0.0

    own = {f: mtime(f) for f in files}
    newest_in_dir: dict[Path, float] = {}
    for f, t in own.items():
        newest_in_dir[f.parent] = max(t, newest_in_dir.get(f.parent, 0.0))
    return sorted(files, key=lambda f: (newest_in_dir[f.parent], own[f]))


def rebuild(folder: str) -> int:
    """立即重建一个目录；已在构建则不等待，返回失败。"""
    root = Path(folder).expanduser().resolve()
    lock = _acquire_rebuild_lock(root)
    if lock is None:
        print(f"[graphwatch] {root}: 已在构建，不排队等待", file=sys.stderr)
        return 1
    try:
        return _rebuild_unlocked(root)
    finally:
        _release_rebuild_lock(lock)


def _rebuild_unlocked(root: Path) -> int:
    """按 /graphify --update --mode deep --wiki 的 runbook 重建一个目录。"""
    from graphify.build import build_merge
    from graphify.cluster import cluster
    from graphify.detect import detect_incremental, save_manifest
    from graphify.extract import extract

    from lib.graphwatch_config import load_config

    out = root / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    graph_path = out / "graph.json"
    manifest_path = out / "manifest.json"  # graphify 默认 manifest 路径相对 cwd，必须显式传绝对

    def _stage(msg: str, since: list) -> None:
        """阶段进度行：消息 + 上一阶段耗时，重建卡在哪一步一眼可见。"""
        now = time.time()
        print(f"[graphwatch] {root.name}: {msg}（{now - since[0]:.0f}s）", file=sys.stderr, flush=True)
        since[0] = now

    t0 = [time.time()]
    _stage("扫描变更", t0)
    # 被排除的旧文件会出现在 deleted_files 里，随后被 prune 出图
    inc = detect_incremental(root, str(manifest_path), extra_excludes=load_config()["excludes"] or None)
    new_files = inc.get("new_files") or {}
    deleted = list(inc.get("deleted_files") or [])
    changed = [f for fl in new_files.values() for f in fl]
    if not changed and not deleted:
        print(f"[graphwatch] {root}: 无变更，跳过", file=sys.stderr)
        return 0

    code = _oldest_first([Path(f) for f in new_files.get("code", [])])
    sem_files = _oldest_first([Path(f) for t in ("document", "paper", "image") for f in new_files.get(t, [])])
    _stage(f"{len(code)} code / {len(sem_files)} doc+ / {len(deleted)} deleted", t0)
    if code:
        # parallel=False：AST 进程池在 macOS spawn 下会重新执行入口脚本（bin/graphwatch），
        # 子进程撞单例锁 → BrokenProcessPool 降级。daemon 本来就串行重建，直接关池。
        ast = extract(code, cache_root=root, root=root, parallel=False)
        _stage(f"AST 抽取 {len(code)} 文件", t0)
    else:
        ast = dict(_EMPTY)
    sem = _semantic(sem_files, root) if sem_files else dict(_EMPTY)
    if sem_files:
        _stage(f"语义抽取 {len(sem_files)} 文件（deep）", t0)

    # Part C 合并（SKILL.md：AST 节点在前，语义按 id 去重）
    seen = {n["id"] for n in ast["nodes"]}
    nodes = list(ast["nodes"])
    for n in sem["nodes"]:
        if n["id"] not in seen:
            nodes.append(n)
            seen.add(n["id"])
    new_extraction = {
        "nodes": nodes,
        "edges": ast["edges"] + sem["edges"],
        "hyperedges": sem.get("hyperedges", []),
        "input_tokens": sem.get("input_tokens", 0),
        "output_tokens": sem.get("output_tokens", 0),
    }

    G = build_merge(
        [new_extraction],
        graph_path=graph_path,
        prune_sources=deleted or None,
        root=root,
    )
    if G.number_of_nodes() == 0:
        print(f"[graphwatch] {root}: 图为空，拒绝写出（防覆盖已有图）", file=sys.stderr)
        return 1
    _stage(f"合并图谱 {G.number_of_nodes()} 节点", t0)
    communities = cluster(G)
    _stage(f"聚类 {len(communities)} 社区", t0)
    _publish(G, communities, root, stage=lambda msg: _stage(msg, t0), daily=False,
             changed=len(changed) + len(deleted),
             tokens={"input": new_extraction["input_tokens"], "output": new_extraction["output_tokens"]})

    # manifest：只盖章真产出语义输出的文件（#2015），失败的下轮重试（#1948）
    from graphify.cli import _stamped_manifest_files

    stamped = _stamped_manifest_files(inc.get("files") or {}, new_extraction, root)
    sem_types = ("document", "paper", "image")
    dispatched = {f for t, fl in new_files.items() if t in sem_types for f in fl}
    stamped_all = {f for fl in stamped.values() for f in fl}
    scan = {f for fl in (inc.get("files") or {}).values() for f in fl}
    save_manifest(stamped, str(manifest_path), root=root, scan_corpus=scan,
                  clear_semantic=(dispatched - stamped_all) or None)
    # 每轮账目落统一日志：验证排除 / 缓存到底省了多少 token 只能靠它。
    # 字段名不能带 token：lib/log.py 会把含 token 的键脱敏成 <REDACTED>
    _log.record("graphwatch.rebuild", logger="graphwatch-daemon", folder=str(root),
                code=len(code), docs=len(sem_files), deleted=len(deleted),
                cache_hits=sem.get("cache_hits", 0),
                llm_input=new_extraction["input_tokens"], llm_output=new_extraction["output_tokens"])
    return 0


REGRAPH_STAMP = ".graphwatch-regraph"
BACKUP_KEEP_DAYS = 7  # 用户 2026-09-27 选定；graphify 的日期备份自己从不清理（约 +127 MB/天）


def prune_backups(out: Path, today=None) -> list[str]:
    """删掉 graphify-out/ 下超过 BACKUP_KEEP_DAYS 天的日期备份目录（YYYY-MM-DD），返回删了哪些。"""
    import datetime
    import shutil

    today = today or datetime.date.today()
    cutoff = today - datetime.timedelta(days=BACKUP_KEEP_DAYS - 1)
    removed = []
    for d in sorted(out.iterdir()):
        try:
            day = datetime.date.fromisoformat(d.name)
        except ValueError:
            continue
        if d.is_dir() and not d.is_symlink() and day < cutoff:
            shutil.rmtree(d)
            removed.append(d.name)
    return removed


def regraph(folder: str) -> int:
    """每日重算图：读现有 graph.json 重新聚类、重写报告和网页图，不调 LLM（0 token）。

    上次重算之后图没变过（graph.json 不比戳文件新）就直接跳过，不花 CPU。
    已在构建则跳过，明天再来。
    """
    root = Path(folder).expanduser().resolve()
    if not (root / "graphify-out" / "graph.json").is_file():
        return 0  # 还没建过图：没东西可重算，也别为拿锁建出 graphify-out
    lock = _acquire_rebuild_lock(root)
    if lock is None:
        print(f"[graphwatch] {root}: 已在构建，跳过本次重算", file=sys.stderr)
        return 0
    try:
        return _regraph_unlocked(root)
    finally:
        _release_rebuild_lock(lock)


def _regraph_unlocked(root: Path) -> int:
    from graphify.build import build_from_json
    from graphify.cluster import cluster, remap_communities_to_previous

    out = root / "graphify-out"
    graph_path = out / "graph.json"
    stamp = out / REGRAPH_STAMP
    removed = prune_backups(out)
    if removed:
        _log.record("graphwatch.prune_backups", logger="graphwatch-daemon", folder=str(root), removed=removed)
    if stamp.is_file() and stamp.stat().st_mtime >= graph_path.stat().st_mtime:
        print(f"[graphwatch] {root}: 上次重算后图没变，跳过", file=sys.stderr)
        return 0

    t0 = time.time()

    def stage(msg: str) -> None:
        print(f"[graphwatch] {root.name}: 重算 {msg}", file=sys.stderr, flush=True)

    raw = json.loads(graph_path.read_text(encoding="utf-8"))
    G = build_from_json(raw, directed=bool(raw.get("directed", False)))
    # 新社区编号按节点重叠映射回上一版，已有社区名才挂得回原来那群节点（graphify cluster-only 同款）
    previous = {n["id"]: n["community"] for n in raw.get("nodes", [])
                if n.get("id") is not None and n.get("community") is not None}
    communities = cluster(G)
    if previous:
        communities = remap_communities_to_previous(communities, previous)
    _publish(G, communities, root, stage=stage, daily=True, changed=0, tokens={"input": 0, "output": 0})
    stamp.touch()
    _log.record("graphwatch.regraph", logger="graphwatch-daemon", folder=str(root),
                nodes=G.number_of_nodes(), communities=len(communities), secs=round(time.time() - t0, 1))
    return 0


def _publish(G, communities: dict, root: Path, *, stage, daily: bool, changed: int, tokens: dict) -> None:
    """聚类之后的共用尾段：社区命名 → 备份 → graph.json → wiki → 其余产出物。

    daily=True（每日重算图）：新社区只用 hub 名、整段 0 token，并写 obsidian；
    daily=False（变更重建）：新社区可让 LLM 起名，不写 obsidian（太重，一天一次够了）。
    """
    from graphify.cluster import score_all
    from graphify.export import backup_if_protected, existing_graph_node_count, to_json
    from graphify.wiki import to_wiki

    from lib.graphwatch_artifacts import resolve_labels, save_labels, write_artifacts

    out = root / "graphify-out"
    graph_path = out / "graph.json"
    cohesion = score_all(G, communities)

    # 社区名保留上次的（labels 文件是社区重命名的人工产物，daemon 不丢），只给
    # 没名字的新社区补名——LLM 命名会参考项目根 CONTEXT.md 的术语表。
    saved: dict[int, str] = {}
    labels_path = out / ".graphify_labels.json"
    if labels_path.is_file():
        try:
            saved = {int(k): v for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()}
        except (ValueError, OSError):
            saved = {}
    labels = resolve_labels(G, communities, saved, root, llm=not daily)
    # 覆盖任何产出物之前先快照（graphify 的日期备份，自带触发条件：花过 LLM token
    # 或社区名被策展过；GRAPHIFY_NO_BACKUP=1 可关）。以前 rebuild 只写 graph.json
    # 和 wiki 没调它，现在连 GRAPH_REPORT.md 和 labels 一起覆盖，必须补上。
    backup_if_protected(out)
    if labels != saved:
        save_labels(out, communities, labels)
        stage(f"社区命名（新增 {len(set(labels) - set(saved))} 个）")

    # force=True：graphify 的 #479 收缩护栏（新图节点数变少就拒写）对 daemon 是死路——
    # 删掉文件后每一轮都撞同一堵墙、每一轮报一次失败，图永远停在删除之前。这里的收缩
    # 是有据可查的：prune_sources 明确列出了被删的文件。覆盖前已经 backup_if_protected，
    # 写坏了能从日期目录回滚。收缩多少照样打出来，不静默。
    before = existing_graph_node_count(graph_path)
    to_json(G, communities, str(graph_path), community_labels=labels or None, force=True)
    if isinstance(before, int) and G.number_of_nodes() < before:
        print(f"[graphwatch] {root.name}: 图收缩 {before} → {G.number_of_nodes()} 节点，已强制写入",
              file=sys.stderr)

    try:
        from graphify.analyze import god_nodes

        gods = god_nodes(G)
    except Exception:  # noqa: BLE001
        gods = []
    n = to_wiki(G, communities, out / "wiki", community_labels=labels or None,
                cohesion=cohesion, god_nodes_data=gods)
    stage(f"写 graph.json + wiki {n} 篇")

    # 其余产出物：报告 / 网页图 / graphml / 折叠树 / obsidian（用户 2026-09-10 选定，不含 svg）
    write_artifacts(G, communities, cohesion, labels, gods, out, root,
                    changed=changed, tokens=tokens, stage=stage, obsidian=daily)
