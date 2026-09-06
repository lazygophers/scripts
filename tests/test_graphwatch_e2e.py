"""graphwatch 集成缝：真跑一轮 graphify 增量重建。

graphifyy 未安装的环境自动 skip，不挡 CI。
跑法（装有 graphifyy 的解释器）:
  PYTHONPATH=. /path/to/graphifyy/venv/bin/python -m unittest tests.test_graphwatch_e2e
"""

from __future__ import annotations

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

from lib import graphwatch


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
