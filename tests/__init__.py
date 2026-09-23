"""测试包统一入口：清掉 AI shell 标记变量，保证输出断言确定性。

在 Claude Code / Codex 里跑测试时，环境里的 CLAUDECODE 等标记会让
Reporter 切到极简输出（lib/ui.py），大量断言图标/表格的用例会假失败。
这里在导入期统一移除；需要测极简模式本身的用例用 patch.dict 显式注入。
"""
import os

from lib.ai_env import _MARKERS

for _var in _MARKERS:
    os.environ.pop(_var, None)
