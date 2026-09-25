"""测试包统一入口：清掉 AI shell 标记变量，保证输出断言确定性。

在 Claude Code / Codex 里跑测试时，环境里的 CLAUDECODE 等标记会让
Reporter 切到极简输出（lib/ui.py），大量断言图标/表格的用例会假失败。
这里在导入期统一移除；需要测极简模式本身的用例用 patch.dict 显式注入。
"""
import atexit
import os
import tempfile

from lib.ai_env import _MARKERS

for _var in _MARKERS:
    os.environ.pop(_var, None)

# 测试写日志一律落到临时文件，不污染生产 JSONL（2026-09-23 审计：真实日志里
# 混进过 logger 'boom'/'smoke'/'炸了' 等测试产物）。test_log.py 的用例用
# patch.dict 覆盖这个值，退出 context 后会还原回这里设的临时路径。
if "SCRIPTS_LOG" not in os.environ:  # 已被上层指定（spawn 的子进程、单测 setUp）时不覆盖
    _suite_log = tempfile.NamedTemporaryFile(prefix="scripts-tests-", suffix=".log", delete=False)
    _suite_log.close()
    os.environ["SCRIPTS_LOG"] = _suite_log.name

    def _cleanup_suite_log(path: str = _suite_log.name) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    atexit.register(_cleanup_suite_log)
