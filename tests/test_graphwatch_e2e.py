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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import graphify  # noqa: F401
    import watchdog  # noqa: F401  # graphify watch 的硬依赖，缺了子进程起不来
    HAS_GRAPHIFY = True
except ImportError:
    HAS_GRAPHIFY = False

from lib import graphwatch, graphwatch_daemon
from lib import graphwatch_rebuild


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
class TestRebuild(unittest.TestCase):
    """重建管线（lib/graphwatch_rebuild.py）：/graphify --mode deep --wiki --update 的源码调用等价物。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_rebuild_full_pipeline_with_wiki(self):
        repo = self.home / "repo"
        repo.mkdir()
        (repo / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")

        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        graph = repo / "graphify-out" / "graph.json"
        wiki_index = repo / "graphify-out" / "wiki" / "index.md"
        self.assertTrue(graph.is_file(), "首次重建应产出 graph.json")
        self.assertTrue(wiki_index.is_file(), "重建应产出 wiki/index.md")

        (repo / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
        self.assertEqual(graphwatch_daemon._run_update(str(repo)), 0)
        data = json.loads(graph.read_text(encoding="utf-8"))
        labels = {n.get("label", n["id"]) for n in data["nodes"]}
        self.assertIn("beta", " ".join(labels), "增量重建应纳入新文件的节点")
        # manifest 必须落在项目自己的 graphify-out，而不是 daemon 的 cwd
        self.assertTrue((repo / "graphify-out" / "manifest.json").is_file())

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
