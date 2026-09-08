"""websearch 薄壳入口 — 多引擎检索,结果打 stdout。"""
import sys

from lib.notify import consume_debug, consume_dry_run, consume_no_say
from lib.skills_help import consume_skills
from lib.ui import timed
from lib.websearch import main as _run


def main():
    sys.argv = consume_dry_run(
        consume_skills(consume_debug(consume_no_say(sys.argv)), __doc__),
        __doc__)
    # -h/--help 不拦截:websearch 有 argparse,完整选项说明由它出
    raise SystemExit(timed(_run, label="websearch")(sys.argv))
