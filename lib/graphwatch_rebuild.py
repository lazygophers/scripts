"""graphwatch 重建管线：进程内按 /graphify <path> --mode deep --wiki --update
的 runbook 跑一次增量重建（graphify 库源码调用，不起 CLI 子进程）。

runbook 出处：graphify skill（SKILL.md + references/update.md）；库函数
签名以安装的 graphifyy 包为准（graphify.detect / extract / build / cluster /
export / wiki / llm）。所有函数都收显式 root，不需要 chdir 到项目目录。

流程：detect_incremental（无变更直接返回）→ AST 抽取（code）+ 语义抽取
（doc/paper/image，配置了 backend 才跑，deep 模式）→ build_merge 并入
现有 graph.json（删除文件 prune）→ cluster → to_json（#479 收缩护栏）
→ to_wiki → save_manifest。GRAPH_REPORT.md 不重生成（人工报告，要刷新跑
`graphify cluster-only <dir>`）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# graphwatch 配置里的 base_url 怎么喂给 graphify：每个后端只认自己的环境变量
# （graphify/llm.py BACKENDS）。azure/bedrock/claude-cli 无此参数。
BASE_URL_ENV: dict[str, str] = {
    "claude": "ANTHROPIC_BASE_URL",
    "kimi": "KIMI_BASE_URL",
    "gemini": "GEMINI_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "ollama": "OLLAMA_BASE_URL",
}

_EMPTY = {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}


def _semantic(files: list[Path], root: Path) -> dict:
    """语义抽取（deep 模式）。backend 没配或调用失败 → 空（manifest 不盖章，下次变更重试）。"""
    import os

    from lib.graphwatch_config import load_config

    cfg = load_config()
    backend = str(cfg.get("backend") or "")
    if not backend:
        return dict(_EMPTY)

    from graphify.llm import extract_corpus_parallel

    env_var = BASE_URL_ENV.get(backend)
    base_url = str(cfg.get("base_url") or "")
    restore = None
    if env_var and base_url:
        restore = (env_var, os.environ.get(env_var), base_url)
        os.environ[env_var] = base_url
    # 后台重建不许被慢后端拖死：单请求 180s、SDK 不重试（graphify 默认 600s × 6 次
    # 重试，代理卡住时一次语义抽取能挂一小时）。setdefault：用户显式设过的不覆盖。
    os.environ.setdefault("GRAPHIFY_API_TIMEOUT", "180")
    os.environ.setdefault("GRAPHIFY_MAX_RETRIES", "1")
    # BACKENDS 的 base_url 在 graphify.llm import 时定格，daemon 进程里该 import
    # 可能早于上面这行 env 注入，只设环境变量不生效——必须直接改 dict。
    # ponytail: 用户清空 base_url 后不还原（重启 daemon 才回官方默认）
    import graphify.llm as _gllm

    if base_url and backend in _gllm.BACKENDS:
        _gllm.BACKENDS[backend]["base_url"] = base_url
    try:
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
    finally:
        if restore is not None:
            var, old, _ = restore
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old


def rebuild(folder: str) -> int:
    """按 /graphify --update --mode deep --wiki 的 runbook 重建一个目录。返回 0/1。"""
    from graphify.build import build_merge
    from graphify.cluster import cluster, score_all
    from graphify.detect import detect_incremental, save_manifest
    from graphify.export import to_json
    from graphify.extract import extract
    from graphify.wiki import to_wiki

    root = Path(folder)
    out = root / "graphify-out"
    out.mkdir(parents=True, exist_ok=True)
    graph_path = out / "graph.json"
    manifest_path = out / "manifest.json"  # graphify 默认 manifest 路径相对 cwd，必须显式传绝对

    inc = detect_incremental(root, str(manifest_path))
    new_files = inc.get("new_files") or {}
    deleted = list(inc.get("deleted_files") or [])
    changed = [f for fl in new_files.values() for f in fl]
    if not changed and not deleted:
        print(f"[graphwatch] {root}: 无变更，跳过", file=sys.stderr)
        return 0

    code = [Path(f) for f in new_files.get("code", [])]
    sem_files = [Path(f) for t in ("document", "paper", "image") for f in new_files.get(t, [])]
    print(f"[graphwatch] {root}: {len(code)} code / {len(sem_files)} doc+ / {len(deleted)} deleted",
          file=sys.stderr)
    # parallel=False：AST 进程池在 macOS spawn 下会重新执行入口脚本（bin/graphwatch），
    # 子进程撞单例锁 → BrokenProcessPool 降级。daemon 本来就串行重建，直接关池。
    ast = extract(code, cache_root=root, root=root, parallel=False) if code else dict(_EMPTY)
    sem = _semantic(sem_files, root) if sem_files else dict(_EMPTY)

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
    communities = cluster(G)
    cohesion = score_all(G, communities)

    # 社区名保留上次的（labels 文件是社区重命名的人工产物，daemon 不丢）
    labels: dict[int, str] = {}
    labels_path = out / ".graphify_labels.json"
    if labels_path.is_file():
        try:
            labels = {int(k): v for k, v in json.loads(labels_path.read_text(encoding="utf-8")).items()}
        except (ValueError, OSError):
            labels = {}

    wrote = to_json(G, communities, str(graph_path), community_labels=labels or None)
    if not wrote:
        # #479 收缩护栏：新图节点数比现有 graph.json 少则拒写。
        print(f"[graphwatch] {root}: graphify 拒绝收缩 graph.json（删除代码请手动 --force）", file=sys.stderr)
        return 1

    try:
        from graphify.analyze import god_nodes

        gods = god_nodes(G)
    except Exception:  # noqa: BLE001
        gods = []
    n = to_wiki(G, communities, out / "wiki", community_labels=labels or None,
                cohesion=cohesion, god_nodes_data=gods)
    print(f"[graphwatch] {root}: 图 {G.number_of_nodes()} 节点 / {len(communities)} 社区，wiki {n} 篇",
          file=sys.stderr)

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
