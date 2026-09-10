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
import sys
import time
from pathlib import Path

_EMPTY = {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}


def _semantic(files: list[Path], root: Path) -> dict:
    """语义抽取（deep 模式）。backend 没配或调用失败 → 空（manifest 不盖章，下次变更重试）。"""
    from lib.graphwatch_artifacts import backend_env
    from lib.graphwatch_config import load_config

    cfg = load_config()
    if not str(cfg.get("backend") or ""):
        return dict(_EMPTY)

    from graphify.llm import extract_corpus_parallel

    try:
        with backend_env(cfg) as backend:
            return extract_corpus_parallel(
                files,
                backend=backend,
                api_key=str(cfg.get("api_key") or "") or None,
                model=str(cfg.get("model") or "") or None,
                root=root,
                deep_mode=True,
                cache_root=root,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[graphwatch] 语义抽取失败（{type(e).__name__}: {e}），本轮只做 AST", file=sys.stderr)
        return dict(_EMPTY)


def rebuild(folder: str) -> int:
    """按 /graphify --update --mode deep --wiki 的 runbook 重建一个目录。返回 0/1。"""
    from graphify.build import build_merge
    from graphify.cluster import cluster, score_all
    from graphify.detect import detect_incremental, save_manifest
    from graphify.export import backup_if_protected, existing_graph_node_count, to_json
    from graphify.extract import extract
    from graphify.wiki import to_wiki

    from lib.graphwatch_artifacts import resolve_labels, save_labels, write_artifacts

    root = Path(folder)
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
    inc = detect_incremental(root, str(manifest_path))
    new_files = inc.get("new_files") or {}
    deleted = list(inc.get("deleted_files") or [])
    changed = [f for fl in new_files.values() for f in fl]
    if not changed and not deleted:
        print(f"[graphwatch] {root}: 无变更，跳过", file=sys.stderr)
        return 0

    code = [Path(f) for f in new_files.get("code", [])]
    sem_files = [Path(f) for t in ("document", "paper", "image") for f in new_files.get(t, [])]
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
    cohesion = score_all(G, communities)
    _stage(f"聚类 {len(communities)} 社区", t0)

    # 社区名保留上次的（labels 文件是社区重命名的人工产物，daemon 不丢），只给
    # 没名字的新社区补名——LLM 命名会参考项目根 CONTEXT.md 的术语表。
    saved: dict[int, str] = {}
    labels_path = out / ".graphify_labels.json"
    if labels_path.is_file():
        try:
            saved = {int(k): v for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()}
        except (ValueError, OSError):
            saved = {}
    labels = resolve_labels(G, communities, saved, root)
    # 覆盖任何产出物之前先快照（graphify 的日期备份，自带触发条件：花过 LLM token
    # 或社区名被策展过；GRAPHIFY_NO_BACKUP=1 可关）。以前 rebuild 只写 graph.json
    # 和 wiki 没调它，现在连 GRAPH_REPORT.md 和 labels 一起覆盖，必须补上。
    backup_if_protected(out)
    if labels != saved:
        save_labels(out, communities, labels)
        _stage(f"社区命名（新增 {len(set(labels) - set(saved))} 个）", t0)

    # force=True：graphify 的 #479 收缩护栏（新图节点数变少就拒写）对 daemon 是死路——
    # 删掉文件后每一轮都撞同一堵墙、每一轮报一次失败，图永远停在删除之前。这里的收缩
    # 是有据可查的：prune_sources 明确列出了被删的文件。覆盖前已经 backup_if_protected，
    # 写坏了能从日期目录回滚。收缩多少照样打出来，不静默。
    before = existing_graph_node_count(graph_path)
    to_json(G, communities, str(graph_path), community_labels=labels or None, force=True)
    if isinstance(before, int) and G.number_of_nodes() < before:
        print(f"[graphwatch] {root.name}: 图收缩 {before} → {G.number_of_nodes()} 节点"
              f"（{len(deleted)} 个文件被删），已强制写入", file=sys.stderr)

    try:
        from graphify.analyze import god_nodes

        gods = god_nodes(G)
    except Exception:  # noqa: BLE001
        gods = []
    n = to_wiki(G, communities, out / "wiki", community_labels=labels or None,
                cohesion=cohesion, god_nodes_data=gods)
    _stage(f"写 graph.json + wiki {n} 篇", t0)

    # 其余产出物：报告 / 网页图 / graphml / 折叠树 / obsidian（用户 2026-09-10 选定，不含 svg）
    write_artifacts(
        G, communities, cohesion, labels, gods, out, root,
        changed=len(changed) + len(deleted),
        tokens={"input": new_extraction["input_tokens"], "output": new_extraction["output_tokens"]},
        stage=lambda msg: _stage(msg, t0),
    )

    # manifest：只盖章真产出语义输出的文件（#2015），失败的下轮重试（#1948）
    from graphify.cli import _stamped_manifest_files

    stamped = _stamped_manifest_files(inc.get("files") or {}, new_extraction, root)
    sem_types = ("document", "paper", "image")
    dispatched = {f for t, fl in new_files.items() if t in sem_types for f in fl}
    stamped_all = {f for fl in stamped.values() for f in fl}
    scan = {f for fl in (inc.get("files") or {}).values() for f in fl}
    save_manifest(stamped, str(manifest_path), root=root, scan_corpus=scan,
                  clear_semantic=(dispatched - stamped_all) or None)
    return 0
