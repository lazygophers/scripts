"""graphwatch 产出物：社区命名（参考项目根 CONTEXT.md）+ 全套导出。

daemon 每轮重建在 graph.json 落盘后调 `write_artifacts()`，把 graphify 的
html / cluster-only（GRAPH_REPORT.md）/ graphml / tree / obsidian 一次刷新。
wiki 留在 graphwatch_rebuild 里（它是 rebuild 的既有行为，且要跟 to_json 同批）。
不做 svg——用户 2026-09-10 明确没选。

命名策略（同一次决定）：**只给没名字的社区起名**，已有名字不管是 LLM 起的还是
人工改的一律不动。新社区先用 hub 名兜底（免费、确定性），配了 backend 再让 LLM
参考项目根 `CONTEXT.md` 的术语表覆写，让社区名和项目自己的词汇对齐。

库函数签名以安装的 graphifyy 包为准（graphify.cluster / llm / analyze /
report / export / exporters.html / tree_html / paths）。
"""
from __future__ import annotations

import contextlib
import json
import os
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

# CONTEXT.md 只取开头这些字符喂进命名 prompt：术语表都在文件前部，整篇塞进去
# 会把 batch 的 16k 上下文挤爆（graphify/llm.py _LABEL_BATCH_SIZE 按 16k 定的）。
CONTEXT_MAX_CHARS = 6000

_LABEL_TOP_K = 12  # 每个社区采样几个代表节点名进 prompt（对齐 graphify._LABEL_TOP_K）


@contextlib.contextmanager
def backend_env(cfg: dict):
    """把 graphwatch 配置里的 backend 设置临时注入 graphify 的读取位置。

    graphify 从环境变量拿 api_key（BACKENDS[b]["env_key"]）、从 BACKENDS dict 拿
    base_url。base_url 在 graphify.llm import 时定格，daemon 进程里该 import 往往
    早于注入，所以只设环境变量不够——必须直接改 dict。

    顺带压两个超时默认值：后台重建不许被慢后端拖死（graphify 默认 600s × 6 次
    重试，代理卡住时一次调用能挂一小时）。setdefault：用户显式设过的不覆盖。
    """
    import graphify.llm as gllm

    backend = str(cfg.get("backend") or "")
    base_url = str(cfg.get("base_url") or "")
    api_key = str(cfg.get("api_key") or "")
    restore: list[tuple[str, str | None]] = []

    def _set(var: str, value: str) -> None:
        restore.append((var, os.environ.get(var)))
        os.environ[var] = value

    spec = gllm.BACKENDS.get(backend) or {}
    if api_key:
        key_var = spec.get("env_key") or (spec.get("env_keys") or [None])[0]
        if key_var:
            _set(key_var, api_key)
    saved_base = None
    if base_url:
        env_var = BASE_URL_ENV.get(backend)
        if env_var:
            _set(env_var, base_url)
        if backend in gllm.BACKENDS:
            # ponytail: 用户清空 base_url 后不还原（重启 daemon 才回官方默认）
            saved_base = gllm.BACKENDS[backend].get("base_url")
            gllm.BACKENDS[backend]["base_url"] = base_url
    os.environ.setdefault("GRAPHIFY_API_TIMEOUT", "180")
    os.environ.setdefault("GRAPHIFY_MAX_RETRIES", "1")
    try:
        yield backend
    finally:
        if saved_base is not None and backend in gllm.BACKENDS:
            gllm.BACKENDS[backend]["base_url"] = saved_base
        for var, old in reversed(restore):
            if old is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = old


def read_glossary(root: Path) -> str:
    """项目根 CONTEXT.md 的开头若干字符（项目术语表）。没有这个文件就返回空串。"""
    path = Path(root) / "CONTEXT.md"
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:CONTEXT_MAX_CHARS].strip()
    except OSError:
        return ""


def _llm_labels(G, communities: dict, root: Path) -> dict[int, str]:
    """让 LLM 给这批社区起名，prompt 里带上 CONTEXT.md 的术语表。

    没配 backend、prompt 建不出来、或调用/解析失败都返回空 dict——调用方已经
    有 hub 名兜底，命名失败绝不能让整轮重建失败。
    """
    from lib.graphwatch_config import load_config

    cfg = load_config()
    if not str(cfg.get("backend") or ""):
        return {}

    from graphify.analyze import god_nodes
    from graphify.llm import _call_llm, _community_label_lines, _parse_label_response

    lines, cids = _community_label_lines(G, communities, god_nodes(G), None, _LABEL_TOP_K)
    if not lines:
        return {}

    glossary = read_glossary(root)
    preface = (
        "The project maintains its own glossary in CONTEXT.md. Reuse its terms and "
        "spelling verbatim whenever a community matches one of them; only invent a "
        "name when nothing in the glossary fits.\n\n"
        f"<CONTEXT.md>\n{glossary}\n</CONTEXT.md>\n\n"
        if glossary
        else ""
    )
    prompt = (
        "You are naming clusters in a knowledge graph. For each community below, "
        "return a concise 2-5 word plain-language name describing what it is about "
        '(e.g. "Order Management", "Payment Flow", "Auth Middleware"). '
        "Each input line is '<community id>: <representative member names>'. "
        "Respond ONLY with a JSON object mapping the community id (as a string) to "
        "its name - no prose, no markdown fences.\n\n" + preface + "\n".join(lines)
    )
    model = str(cfg.get("model") or "") or None
    try:
        with backend_env(cfg) as backend:
            text = _call_llm(
                prompt,
                backend=backend,
                model=model,
                max_tokens=min(256 + 48 * len(cids), 8192),
            )
        return _parse_label_response(text, cids)
    except Exception as e:  # noqa: BLE001
        print(f"[graphwatch] 社区命名失败（{type(e).__name__}: {e}），沿用 hub 名", file=sys.stderr)
        return {}


def resolve_labels(G, communities: dict, saved: dict[int, str], root: Path) -> dict[int, str]:
    """只给没名字的社区起名，已有名字原样保留。

    `Community N` 视同没名字（那是 graphify 的占位名，不是谁起的），所以它会被
    hub 名和 LLM 名替换掉，不会永远卡在占位符上。
    """
    labels: dict[int, str] = {}
    missing: list[int] = []
    for cid in communities:
        name = saved.get(cid)
        if name and name != f"Community {cid}":
            labels[cid] = name
        else:
            missing.append(cid)
    if not missing:
        return labels

    from graphify.cluster import label_communities_by_hub

    hub = label_communities_by_hub(G, communities)
    for cid in missing:
        labels[cid] = hub[cid]

    fresh = _llm_labels(G, {cid: communities[cid] for cid in missing}, root)
    # LLM 没起出真名字时会回 "Community N" 占位符，或把 id 原样吐回来（graphify #2534）；
    # 这两种都不许盖掉确定性的 hub 名。
    labels.update({
        cid: v for cid, v in fresh.items()
        if v and v != f"Community {cid}" and v != str(cid) and cid in set(missing)
    })
    return labels


def save_labels(out: Path, communities: dict, labels: dict[int, str]) -> None:
    """写 .graphify_labels.json 和它的成员签名边车 .sig。

    ponytail: 签名按当前聚类全量写。语义上它宣称「这些名字对得上这批成员」，而按
    用户的规则已有名字本来就不重算，所以这里不区分「这轮起的」和「上轮留的」——
    代价是手工跑 `graphify cluster-only` 时不会再提示重命名。要恢复 graphify 的
    失效检测，就只给本轮真正命名过的 cid 写签名。
    """
    from graphify.cluster import community_member_sigs
    from graphify.paths import write_json_atomic

    labels_path = out / ".graphify_labels.json"
    write_json_atomic(labels_path, {str(k): v for k, v in labels.items()}, ensure_ascii=False)
    sigs = community_member_sigs(communities)
    (out / (labels_path.name + ".sig")).write_text(
        json.dumps({str(k): v for k, v in sigs.items()}), encoding="utf-8"
    )


def _write_report(G, communities, cohesion, labels, gods, out: Path, root: Path,
                  changed: int, tokens: dict) -> None:
    """GRAPH_REPORT.md，等价 `graphify cluster-only`。"""
    from graphify.analyze import suggest_questions, surprising_connections
    from graphify.report import generate, load_learning_for_report
    from graphify.watch import _git_head

    surprises = surprising_connections(G, communities)
    questions = suggest_questions(G, communities, labels)
    report = generate(
        G, communities, cohesion, labels, gods, surprises,
        # 增量重建只看变更文件，拿不到全语料的 files/words 统计——报告里如实说明，
        # 而不是编一个数（generate 走 warning 分支就不再索引 total_files）。
        {"warning": f"graphwatch 增量重建 — 本轮 {changed} 个文件变更，全语料统计未重新扫描"},
        tokens, str(root),
        suggested_questions=questions,
        built_at_commit=_git_head(cwd=root),
        learning=load_learning_for_report(out / "graph.json"),
    )
    (out / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")


def _write_html(G, communities, labels, out: Path) -> None:
    """graph.html。图太大时 node_limit 让它降级成社区聚合图而不是抛错。"""
    from graphify.exporters.html import _viz_node_limit, to_html

    limit = _viz_node_limit()
    if limit <= 0:  # GRAPHIFY_VIZ_NODE_LIMIT=0 明确关掉可视化
        (out / "graph.html").unlink(missing_ok=True)
        return
    to_html(G, communities, str(out / "graph.html"),
            community_labels=labels or None, node_limit=limit)


def _write_obsidian(G, communities, cohesion, labels, out: Path) -> None:
    """obsidian vault + canvas，落在 graphify-out/obsidian/（同 graphify 默认）。"""
    from graphify.export import to_canvas, to_obsidian

    vault = out / "obsidian"
    to_obsidian(G, communities, str(vault),
                community_labels=labels or None, cohesion=cohesion or None)
    to_canvas(G, communities, str(vault / "graph.canvas"), community_labels=labels or None)


def write_artifacts(G, communities: dict, cohesion: dict, labels: dict[int, str],
                    gods: list, out: Path, root: Path, changed: int,
                    tokens: dict, stage=None) -> list[str]:
    """刷新 daemon 每轮要产的全部导出物，返回失败的产出物名。

    每个产出物独立 try：graph.json 已经落盘，一个导出挂掉不该带走其它的。
    `stage` 是 rebuild 的阶段打点函数，传进来就每步报一次耗时。
    """
    from graphify.export import to_graphml
    from graphify.tree_html import write_tree_html

    steps = [
        ("GRAPH_REPORT.md", lambda: _write_report(G, communities, cohesion, labels, gods,
                                                  out, root, changed, tokens)),
        ("graph.html", lambda: _write_html(G, communities, labels, out)),
        ("graph.graphml", lambda: to_graphml(G, communities, str(out / "graph.graphml"))),
        # write_tree_html 的 root= 是「只保留这个前缀下的 source_file」的子树过滤器，
        # 而 source_file 存的是仓库相对路径——传绝对路径会匹配 0 个文件直接 ValueError。
        ("GRAPH_TREE.html", lambda: write_tree_html(out / "graph.json", out / "GRAPH_TREE.html",
                                                    project_label=root.name)),
        ("obsidian", lambda: _write_obsidian(G, communities, cohesion, labels, out)),
    ]
    failed: list[str] = []
    for name, fn in steps:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"[graphwatch] {name} 导出失败（{type(e).__name__}: {e}），其余产出物继续",
                  file=sys.stderr)
        else:
            if stage is not None:
                stage(f"写 {name}")
    return failed
