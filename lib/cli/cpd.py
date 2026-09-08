"""cpd 薄壳入口 — 深度覆盖复制。"""
import sys

from lib.cpd import copy
from lib.notify import consume_debug, consume_dry_run, consume_help, consume_no_say
from lib.skills_help import consume_skills
from lib.ui import timed


def main():
    sys.argv = consume_dry_run(consume_help(
        consume_skills(consume_debug(consume_no_say(sys.argv)), __doc__),
        __doc__,
        usage="cpd [选项] <源...> <目标>",
    ), __doc__)
    raise SystemExit(timed(copy, label="cpd")(sys.argv))
