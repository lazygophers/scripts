"""graphwatch 产出物：社区命名（参考 CONTEXT.md）+ 全套导出。

read_glossary 不依赖 graphify，其余用例在没装 graphifyy 的环境自动 skip。
跑法: python3 -m unittest tests.test_graphwatch_artifacts
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import graphify  # noqa: F401
    import networkx as nx

    HAS_GRAPHIFY = True
except ImportError:
    HAS_GRAPHIFY = False

from lib import graphwatch_artifacts as art


class TestReadGlossary(unittest.TestCase):
    """CONTEXT.md 读取：缺文件不炸，超长截断。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_file_returns_empty(self):
        self.assertEqual(art.read_glossary(self.root), "")

    def test_directory_in_place_of_file_returns_empty(self):
        (self.root / "CONTEXT.md").mkdir()
        self.assertEqual(art.read_glossary(self.root), "")

    def test_reads_and_strips(self):
        (self.root / "CONTEXT.md").write_text("\n# 术语\n\n蜂群 = 多 agent 并行\n", encoding="utf-8")
        self.assertEqual(art.read_glossary(self.root), "# 术语\n\n蜂群 = 多 agent 并行")

    def test_truncated_to_cap(self):
        (self.root / "CONTEXT.md").write_text("x" * (art.CONTEXT_MAX_CHARS + 500), encoding="utf-8")
        self.assertEqual(len(art.read_glossary(self.root)), art.CONTEXT_MAX_CHARS)


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，跳过")
class TestBackendEnv(unittest.TestCase):
    """配置里的 api_key / base_url 必须进 graphify 的读取位置，退出时原样还原。"""

    def setUp(self):
        import graphify.llm as gllm

        self.gllm = gllm
        self._saved_base = gllm.BACKENDS["openai"]["base_url"]
        self.addCleanup(
            gllm.BACKENDS["openai"].__setitem__, "base_url", self._saved_base
        )
        for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            old = os.environ.pop(var, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, var, old)
            else:
                self.addCleanup(os.environ.pop, var, None)

    def test_injects_key_and_base_url_then_restores(self):
        cfg = {"backend": "openai", "api_key": "sk-test", "base_url": "http://proxy.invalid/v1"}
        with art.backend_env(cfg) as backend:
            self.assertEqual(backend, "openai")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test")
            self.assertEqual(os.environ["OPENAI_BASE_URL"], "http://proxy.invalid/v1")
            self.assertEqual(
                self.gllm.BACKENDS["openai"]["base_url"], "http://proxy.invalid/v1",
                "BACKENDS 在 import 时定格，只设环境变量不生效",
            )
        self.assertNotIn("OPENAI_API_KEY", os.environ)
        self.assertEqual(self.gllm.BACKENDS["openai"]["base_url"], self._saved_base)

    def test_restores_even_on_exception(self):
        cfg = {"backend": "openai", "api_key": "sk-test", "base_url": "http://proxy.invalid/v1"}
        with self.assertRaises(RuntimeError):
            with art.backend_env(cfg):
                raise RuntimeError("boom")
        self.assertNotIn("OPENAI_API_KEY", os.environ)
        self.assertEqual(self.gllm.BACKENDS["openai"]["base_url"], self._saved_base)

    def test_empty_config_touches_nothing(self):
        with art.backend_env({}) as backend:
            self.assertEqual(backend, "")
            self.assertNotIn("OPENAI_API_KEY", os.environ)

    def test_gemini_uses_first_of_env_keys(self):
        old = os.environ.pop("GEMINI_API_KEY", None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, "GEMINI_API_KEY", old)
        with art.backend_env({"backend": "gemini", "api_key": "g-key"}):
            self.assertEqual(os.environ["GEMINI_API_KEY"], "g-key")
        self.assertNotIn("GEMINI_API_KEY", os.environ)


def _toy_graph():
    """两个社区的小图：alpha/beta 一组，gamma/delta 一组。"""
    G = nx.Graph()
    for nid in ("alpha", "beta", "gamma", "delta"):
        G.add_node(nid, label=nid)
    G.add_edge("alpha", "beta", relation="calls", confidence="EXTRACTED")
    G.add_edge("gamma", "delta", relation="calls", confidence="EXTRACTED")
    return G, {0: ["alpha", "beta"], 1: ["gamma", "delta"]}


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，跳过")
class TestResolveLabels(unittest.TestCase):
    """只给没名字的社区起名，已有名字一律不动（用户 2026-09-10 决定）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.G, self.communities = _toy_graph()
        self._saved_llm = art._llm_labels
        self.addCleanup(setattr, art, "_llm_labels", self._saved_llm)
        art._llm_labels = lambda *a, **k: {}  # 默认不起 LLM，单测不打网络

    def test_existing_names_untouched(self):
        saved = {0: "认证中间件", 1: "订单管理"}
        self.assertEqual(art.resolve_labels(self.G, self.communities, saved, self.root), saved)

    def test_no_llm_call_when_nothing_missing(self):
        called = []
        art._llm_labels = lambda *a, **k: called.append(1) or {}
        art.resolve_labels(self.G, self.communities, {0: "甲", 1: "乙"}, self.root)
        self.assertEqual(called, [], "全都有名字时不该花 LLM 的钱")

    def test_placeholder_treated_as_missing(self):
        labels = art.resolve_labels(self.G, self.communities, {0: "Community 0", 1: "订单管理"}, self.root)
        self.assertNotEqual(labels[0], "Community 0", "占位名必须被 hub 名替换")
        self.assertEqual(labels[1], "订单管理")

    def test_hub_label_fills_missing_without_backend(self):
        labels = art.resolve_labels(self.G, self.communities, {}, self.root)
        self.assertEqual(set(labels), {0, 1})
        for cid, name in labels.items():
            self.assertTrue(name, f"社区 {cid} 应有确定性 hub 名")
            self.assertNotEqual(name, f"Community {cid}")

    def test_llm_name_overrides_hub_name(self):
        art._llm_labels = lambda G, comms, root: {0: "认证中间件"}
        labels = art.resolve_labels(self.G, self.communities, {}, self.root)
        self.assertEqual(labels[0], "认证中间件")
        self.assertTrue(labels[1], "LLM 没给的那个仍要有 hub 名")

    def test_llm_placeholder_and_id_echo_rejected(self):
        art._llm_labels = lambda G, comms, root: {0: "Community 0", 1: "1"}
        labels = art.resolve_labels(self.G, self.communities, {}, self.root)
        self.assertNotEqual(labels[0], "Community 0")
        self.assertNotEqual(labels[1], "1")

    def test_llm_cannot_rename_an_already_named_community(self):
        art._llm_labels = lambda G, comms, root: {0: "偷改的名字", 1: "新社区"}
        labels = art.resolve_labels(self.G, self.communities, {0: "人工命名"}, self.root)
        self.assertEqual(labels[0], "人工命名", "已有名字不许被 LLM 覆盖")
        self.assertEqual(labels[1], "新社区")


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，跳过")
class TestLlmLabels(unittest.TestCase):
    """命名 prompt 必须带上项目根 CONTEXT.md 的术语表。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.G, self.communities = _toy_graph()
        import graphify.llm as gllm

        self.gllm = gllm
        self._saved_call = gllm._call_llm
        self.addCleanup(setattr, gllm, "_call_llm", self._saved_call)
        self._saved_cfg = art.__dict__.get("_cfg_override")
        self._patch_config({"backend": "openai", "api_key": "k", "model": "", "base_url": ""})

    def _patch_config(self, cfg):
        import lib.graphwatch_config as gc

        saved = gc.load_config
        gc.load_config = lambda: dict(cfg)
        self.addCleanup(setattr, gc, "load_config", saved)

    def test_prompt_carries_context_md(self):
        (self.root / "CONTEXT.md").write_text("蜂群 = 多 agent 并行协作", encoding="utf-8")
        seen = {}

        def fake_call(prompt, **kw):
            seen["prompt"] = prompt
            return json.dumps({"0": "蜂群调度"})

        self.gllm._call_llm = fake_call
        out = art._llm_labels(self.G, self.communities, self.root)
        self.assertEqual(out.get(0), "蜂群调度")
        self.assertIn("蜂群 = 多 agent 并行协作", seen["prompt"])
        self.assertIn("<CONTEXT.md>", seen["prompt"])

    def test_prompt_omits_section_when_no_context_md(self):
        self.gllm._call_llm = lambda prompt, **kw: (
            self.assertNotIn("<CONTEXT.md>", prompt) or json.dumps({"0": "甲"})
        )
        self.assertEqual(art._llm_labels(self.G, self.communities, self.root).get(0), "甲")

    def test_no_backend_skips_call(self):
        self._patch_config({"backend": "", "api_key": "", "model": "", "base_url": ""})

        def boom(*a, **k):
            raise AssertionError("没配 backend 不该调 LLM")

        self.gllm._call_llm = boom
        self.assertEqual(art._llm_labels(self.G, self.communities, self.root), {})

    def test_call_failure_degrades_to_empty(self):
        def boom(*a, **k):
            raise RuntimeError("网络挂了")

        self.gllm._call_llm = boom
        self.assertEqual(art._llm_labels(self.G, self.communities, self.root), {},
                         "命名失败不许炸掉整轮重建")

    def test_empty_communities_skip_call(self):
        def boom(*a, **k):
            raise AssertionError("没有可命名的社区不该调 LLM")

        self.gllm._call_llm = boom
        self.assertEqual(art._llm_labels(self.G, {}, self.root), {})


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，跳过")
class TestSaveLabels(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_writes_labels_and_sig(self):
        G, communities = _toy_graph()
        art.save_labels(self.out, communities, {0: "认证", 1: "订单"})
        labels_path = self.out / ".graphify_labels.json"
        sig_path = self.out / ".graphify_labels.json.sig"
        self.assertEqual(
            json.loads(labels_path.read_text(encoding="utf-8")), {"0": "认证", "1": "订单"}
        )
        self.assertEqual(set(json.loads(sig_path.read_text(encoding="utf-8"))), {"0", "1"})


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，跳过")
class TestWriteArtifacts(unittest.TestCase):
    """全套导出：产出物落盘；单个失败不带走其它。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "graphify-out"
        self.out.mkdir(parents=True)
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.G, self.communities = _toy_graph()
        self.labels = {0: "认证", 1: "订单"}
        from graphify.export import to_json

        to_json(self.G, self.communities, str(self.out / "graph.json"),
                community_labels=self.labels)

    def _run(self):
        return art.write_artifacts(
            self.G, self.communities, {0: 1.0, 1: 1.0}, self.labels, [],
            self.out, self.root, changed=1, tokens={"input": 0, "output": 0},
        )

    def test_all_artifacts_written(self):
        self.assertEqual(self._run(), [])
        for name in ("GRAPH_REPORT.md", "graph.html", "graph.graphml", "GRAPH_TREE.html"):
            self.assertTrue((self.out / name).is_file(), f"{name} 未产出")
        self.assertTrue((self.out / "obsidian" / "graph.canvas").is_file(), "canvas 未产出")
        self.assertFalse((self.out / "graph.svg").exists(), "用户没选 svg，不该产出")

    def test_one_failure_does_not_block_the_rest(self):
        saved = art._write_html
        self.addCleanup(setattr, art, "_write_html", saved)

        def boom(*a, **k):
            raise RuntimeError("viz 挂了")

        art._write_html = boom
        self.assertEqual(self._run(), ["graph.html"])
        self.assertTrue((self.out / "GRAPH_REPORT.md").is_file(), "报告仍应产出")
        self.assertTrue((self.out / "graph.graphml").is_file(), "graphml 仍应产出")

    def test_stage_callback_reports_each_success(self):
        seen: list[str] = []
        art.write_artifacts(
            self.G, self.communities, {0: 1.0, 1: 1.0}, self.labels, [],
            self.out, self.root, changed=1, tokens={"input": 0, "output": 0},
            stage=seen.append,
        )
        self.assertEqual(len(seen), 5, "五个产出物各报一次进度")

    def test_viz_limit_zero_removes_html(self):
        (self.out / "graph.html").write_text("stale", encoding="utf-8")
        os.environ["GRAPHIFY_VIZ_NODE_LIMIT"] = "0"
        self.addCleanup(os.environ.pop, "GRAPHIFY_VIZ_NODE_LIMIT", None)
        self._run()
        self.assertFalse((self.out / "graph.html").exists(),
                         "GRAPHIFY_VIZ_NODE_LIMIT=0 应删掉过期的 graph.html")


if __name__ == "__main__":
    unittest.main()
