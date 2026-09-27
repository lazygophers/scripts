"""graphwatch 集成缝：真跑一轮 graphify 增量重建。

graphifyy 未安装的环境自动 skip，不挡 CI。
跑法（装有 graphifyy 的解释器）:
  PYTHONPATH=. /path/to/graphifyy/venv/bin/python -m unittest tests.test_graphwatch_e2e
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import graphify  # noqa: F401
    import watchdog  # noqa: F401  # graphify watch 的硬依赖，缺了子进程起不来
    HAS_GRAPHIFY = True
except ImportError:
    HAS_GRAPHIFY = False

from lib import graphwatch, graphwatch_daemon, graphwatch_rebuild


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，集成缝跳过")
class TestSemanticBaseUrl(unittest.TestCase):
    """_semantic 必须让配置的 base_url 生效，即使 graphify.llm 在 env 注入前就被 import。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = os.environ.pop("GRAPHWATCH_HOME", None)
        os.environ["GRAPHWATCH_HOME"] = str(self.home)
        self.addCleanup(self._tmp.cleanup)
        if self._env is None:
            self.addCleanup(os.environ.pop, "GRAPHWATCH_HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "GRAPHWATCH_HOME", self._env)
        cfg = graphwatch.load_config()
        cfg.update({"backend": "openai", "api_key": "k", "base_url": "http://proxy.invalid/v1"})
        graphwatch.save_config(cfg)
        import graphify.llm as gllm

        self._saved = (gllm.BACKENDS["openai"]["base_url"], gllm.extract_corpus_parallel)
        self.addCleanup(self._restore, gllm)

    def _restore(self, gllm):
        gllm.BACKENDS["openai"]["base_url"], gllm.extract_corpus_parallel = self._saved

    def test_base_url_patched_even_when_llm_preimported(self):
        import graphify.llm as gllm

        gllm.BACKENDS["openai"]["base_url"] = "https://api.openai.com/v1"  # 模拟 import 早于 env 注入定格
        captured = {}

        def fake_extract(files, **kw):
            captured["base_url"] = gllm.BACKENDS[kw["backend"]]["base_url"]
            return {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0}

        gllm.extract_corpus_parallel = fake_extract
        graphwatch_rebuild._semantic([Path("a.md")], Path("."))
        self.assertEqual(captured["base_url"], "http://proxy.invalid/v1",
                         "BACKENDS 必须在调用前改成配置的 base_url")


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，集成缝跳过")
class TestSemanticCache(unittest.TestCase):
    """_semantic 先读 graphify 语义缓存：命中的文件不交给 LLM。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._env = os.environ.pop("GRAPHWATCH_HOME", None)
        os.environ["GRAPHWATCH_HOME"] = str(self.root / "cfg")
        if self._env is None:
            self.addCleanup(os.environ.pop, "GRAPHWATCH_HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "GRAPHWATCH_HOME", self._env)
        import graphify.llm as gllm

        self.addCleanup(setattr, gllm, "extract_corpus_parallel", gllm.extract_corpus_parallel)
        self.sent: list[Path] = []

        def fake_extract(files, **kw):
            self.sent.extend(files)
            return {"nodes": [{"id": "b_doc", "label": "b", "source_file": "b.md"}], "edges": [],
                    "hyperedges": [], "input_tokens": 7, "output_tokens": 3}

        gllm.extract_corpus_parallel = fake_extract
        (self.root / "a.md").write_text("# doc a\n", encoding="utf-8")
        (self.root / "b.md").write_text("# doc b\n", encoding="utf-8")
        from graphify.cache import save_semantic_cache

        save_semantic_cache([{"id": "a_doc", "label": "cached a", "source_file": "a.md"}], [], [],
                            root=self.root, mode="deep", prompt=gllm._extraction_system(deep=True),
                            cache_root=self.root)

    def _configure_backend(self):
        cfg = graphwatch.load_config()
        cfg["backend"] = "openai"
        graphwatch.save_config(cfg)

    def test_cache_hit_is_not_sent_to_llm(self):
        self._configure_backend()
        res = graphwatch_rebuild._semantic([self.root / "a.md", self.root / "b.md"], self.root)
        self.assertEqual(self.sent, [self.root / "b.md"])
        self.assertEqual(res["cache_hits"], 1)
        self.assertEqual({n["id"] for n in res["nodes"]}, {"a_doc", "b_doc"})
        self.assertEqual((res["input_tokens"], res["output_tokens"]), (7, 3))

    def test_all_cached_skips_llm_entirely(self):
        self._configure_backend()
        res = graphwatch_rebuild._semantic([self.root / "a.md"], self.root)
        self.assertEqual(self.sent, [])
        self.assertEqual([n["id"] for n in res["nodes"]], ["a_doc"])

    def test_llm_failure_keeps_cached_part(self):
        import graphify.llm as gllm

        self._configure_backend()

        def boom(files, **kw):
            raise RuntimeError("upstream down")

        gllm.extract_corpus_parallel = boom
        with unittest.mock.patch("lib.ui.Reporter.warn") as warn:
            res = graphwatch_rebuild._semantic([self.root / "a.md", self.root / "b.md"], self.root)
        self.assertEqual([n["id"] for n in res["nodes"]], ["a_doc"])
        self.assertIn("upstream down", warn.call_args[0][0])

    def test_empty_result_backs_off_llm(self):
        """配额用尽时 graphify 不抛异常只回空：之后一小时内不再重发同一批文档。"""
        import graphify.llm as gllm

        self._configure_backend()
        calls = []
        gllm.extract_corpus_parallel = lambda files, **kw: (calls.append(files) or dict(graphwatch_rebuild._EMPTY))
        files = [self.root / "b.md"]
        graphwatch_rebuild._semantic(files, self.root)
        graphwatch_rebuild._semantic(files, self.root)
        self.assertEqual(len(calls), 1, "退避期内不该再调 LLM")
        with unittest.mock.patch.object(graphwatch_rebuild.time, "time",
                                        return_value=time.time() + graphwatch_rebuild.SEMANTIC_BACKOFF_SECS + 1):
            graphwatch_rebuild._semantic(files, self.root)
        self.assertEqual(len(calls), 2, "退避期过后应重试")

    def test_cache_used_even_without_backend(self):
        res = graphwatch_rebuild._semantic([self.root / "a.md", self.root / "b.md"], self.root)
        self.assertEqual(self.sent, [], "没配 backend 不该调 LLM")
        self.assertEqual([n["id"] for n in res["nodes"]], ["a_doc"])


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，集成缝跳过")
class TestRebuild(unittest.TestCase):
    """重建管线（lib/graphwatch_rebuild.py）：/graphify --mode deep --wiki --update 的源码调用等价物。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # 隔离 GRAPHWATCH_HOME：不隔离就会读到本机真实配置里的 backend，社区命名
        # 会真的打 LLM API（慢 + 花钱），测试结果还随本机配置漂移。
        self._env = os.environ.pop("GRAPHWATCH_HOME", None)
        os.environ["GRAPHWATCH_HOME"] = str(self.home / "cfg")
        if self._env is None:
            self.addCleanup(os.environ.pop, "GRAPHWATCH_HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "GRAPHWATCH_HOME", self._env)

    def test_rebuild_full_pipeline_with_wiki(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        graph = repo / "graphify-out" / "graph.json"
        wiki_index = repo / "graphify-out" / "wiki" / "index.md"
        self.assertTrue(graph.is_file(), "首次重建应产出 graph.json")
        self.assertTrue(wiki_index.is_file(), "重建应产出 wiki/index.md")
        # 用户 2026-09-10 选定的全套产出物（不含 svg）
        out = repo / "graphify-out"
        for name in ("GRAPH_REPORT.md", "graph.html", "graph.graphml", "GRAPH_TREE.html"):
            self.assertTrue((out / name).is_file(), f"重建应产出 {name}")
        self.assertFalse((out / "obsidian").exists(), "obsidian 只在每日重算图时写（用户 2026-09-27 选定）")
        self.assertFalse((out / "graph.svg").exists(), "用户没选 svg，不该产出")
        self.assertTrue((out / ".graphify_labels.json").is_file(), "社区名应落盘，避免下轮重复花钱")

        (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        data = json.loads(graph.read_text(encoding="utf-8"))
        labels = {n.get("label", n["id"]) for n in data["nodes"]}
        self.assertIn("beta", " ".join(labels), "增量重建应纳入新文件的节点")
        # manifest 必须落在项目自己的 graphify-out，而不是 daemon 的 cwd
        self.assertTrue((repo / "graphify-out" / "manifest.json").is_file())

    def test_deleting_files_shrinks_the_graph_instead_of_failing(self):
        """删文件导致图变小时必须照写。graphify 的 #479 收缩护栏对 daemon 是死路：
        它会让每一轮都撞同一堵墙、每一轮报一次失败，图永远停在删除之前。"""
        repo = self.home / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        (repo / "extra.py").write_text(
            "def beta():\n    return 2\n\n\ndef gamma():\n    return 3\n", encoding="utf-8"
        )
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        graph = repo / "graphify-out" / "graph.json"
        before = len(json.loads(graph.read_text(encoding="utf-8"))["nodes"])

        (repo / "extra.py").unlink()
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0, "删文件不该让重建失败")
        after = json.loads(graph.read_text(encoding="utf-8"))["nodes"]
        self.assertLess(len(after), before, "删掉的文件应该从图里被裁掉")
        self.assertNotIn("gamma", " ".join(n.get("label", n["id"]) for n in after),
                         "被删文件的节点不该还留在图里")

    def test_scratch_excluded_except_research_and_memory(self):
        repo = self.home / "repo"
        for rel in ("app.py", ".scratch/spec/gen_x.py", ".scratch/research/keep_r.py",
                    ".scratch/memory/keep_m.py", "sub/.scratch/tmp/gen_y.py"):
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"def {p.stem}():\n    return 1\n", encoding="utf-8")
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        data = json.loads((repo / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
        labels = " ".join(n.get("label", n["id"]) for n in data["nodes"])
        self.assertIn("keep_r", labels)
        self.assertIn("keep_m", labels)
        self.assertNotIn("gen_x", labels)
        self.assertNotIn("gen_y", labels)

    def test_rebuild_writes_stats_to_log(self):
        from lib import log as slog

        log_file = self.home / "scripts.log"
        with unittest.mock.patch.dict(os.environ, {"SCRIPTS_LOG": str(log_file)}):
            repo = self.home / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
            self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
            entries = [e for e in slog.read_entries(log_file, logger="graphwatch-daemon")
                       if e.get("event") == "graphwatch.rebuild"]
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual((e["folder"], e["code"], e["docs"], e["deleted"]), (str(repo.resolve()), 1, 0, 0))
        self.assertEqual((e["cache_hits"], e["llm_input"], e["llm_output"]), (0, 0, 0))

    def test_regraph_reclusters_without_llm_and_skips_when_unchanged(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def alpha():\n    return beta()\n\n\ndef beta():\n    return 1\n",
                                     encoding="utf-8")
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        graph = repo / "graphify-out" / "graph.json"
        with unittest.mock.patch("lib.graphwatch_artifacts._llm_labels",
                                 side_effect=AssertionError("重算图不许调 LLM")):
            self.assertEqual(graphwatch_rebuild.regraph(str(repo)), 0)
            stamp = repo / "graphify-out" / graphwatch_rebuild.REGRAPH_STAMP
            self.assertTrue(stamp.is_file())
            self.assertTrue((repo / "graphify-out" / "obsidian" / "graph.canvas").is_file(),
                            "每日重算图负责写 obsidian")
            mtime = graph.stat().st_mtime
            time.sleep(0.05)
            self.assertEqual(graphwatch_rebuild.regraph(str(repo)), 0)
        self.assertEqual(graph.stat().st_mtime, mtime, "图没变时不该重写")

    def test_watcher_ignores_excluded_paths(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / ".gitignore").write_text("vendor/\n", encoding="utf-8")
        repo = repo.resolve()  # 注册表里存的就是 resolve 后的路径，watchdog 事件路径以它为前缀
        ignored = graphwatch_daemon._ignore_matcher(repo)
        ignored_paths = (".scratch/progress.html", ".claude/worktrees/x/a.py", ".ask-ui/q.json", "vendor/v.py")
        kept_paths = ("app.py", ".scratch/research/r.md", ".scratch/memory/m.md")
        for rel in ignored_paths + kept_paths:  # 目录规则（尾斜杠）要靠磁盘上真是目录才匹配
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text("x", encoding="utf-8")
        for rel in ignored_paths:
            self.assertTrue(ignored(repo / rel), rel)
        for rel in kept_paths:
            self.assertFalse(ignored(repo / rel), rel)

    def test_regraph_without_graph_creates_nothing(self):
        repo = self.home / "empty"
        repo.mkdir()
        self.assertEqual(graphwatch_rebuild.regraph(str(repo)), 0)
        self.assertFalse((repo / "graphify-out").exists())

    def test_rebuild_no_change_is_noop(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        graphwatch_daemon._run_update(str(repo))
        mtime = (repo / "graphify-out" / "graph.json").stat().st_mtime
        time.sleep(0.05)
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        self.assertEqual((repo / "graphify-out" / "graph.json").stat().st_mtime, mtime,
                         "无变更时不应重写 graph.json")


@unittest.skipUnless(HAS_GRAPHIFY, "graphifyy 未安装，集成缝跳过")
class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self._env = os.environ.pop("GRAPHWATCH_HOME", None)
        os.environ["GRAPHWATCH_HOME"] = str(self.home)
        self.addCleanup(self._tmp.cleanup)
        if self._env is None:
            self.addCleanup(os.environ.pop, "GRAPHWATCH_HOME", None)
        else:
            self.addCleanup(os.environ.__setitem__, "GRAPHWATCH_HOME", self._env)

    def test_change_triggers_graph_rebuild(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
        graphwatch.add_folder(repo)

        stop = threading.Event()
        errors: list[Exception] = []

        def daemon():
            try:
                graphwatch.run_daemon(stop_event=stop, poll_interval=0.5)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t = threading.Thread(target=daemon, daemon=True)
        t.start()
        try:
            # graphify watch 没有首轮构建——首个 graph.json 也靠变更事件触发
            graph_json = repo / "graphify-out" / "graph.json"
            # 子进程启动要几秒；写入间隔 15s（> debounce 3s），单次写完等一轮，
            # 没触发再写——连续快速写会不断重置 debounce 永不触发
            nudge = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
            (repo / "app.py").write_text(nudge, encoding="utf-8")
            deadline = time.monotonic() + 90
            next_nudge = time.monotonic() + 15
            while time.monotonic() < deadline:
                if graph_json.is_file():
                    break
                if time.monotonic() > next_nudge:
                    (repo / "app.py").write_text(nudge + "\n# nudge\n", encoding="utf-8")
                    next_nudge = time.monotonic() + 15
                time.sleep(1)
            self.assertTrue(graph_json.is_file(), "变更未触发首次构建")
            mtime_before = graph_json.stat().st_mtime
            time.sleep(1.5)
            (repo / "app.py").write_text(
                "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n\n\ndef gamma():\n    return 3\n",
                encoding="utf-8",
            )
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if graph_json.stat().st_mtime > mtime_before:
                    break
                time.sleep(0.5)
            self.assertGreater(
                graph_json.stat().st_mtime, mtime_before, "变更后 graph.json 未重建"
            )
        finally:
            stop.set()
            t.join(timeout=10)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
