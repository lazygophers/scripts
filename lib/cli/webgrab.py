"""webgrab 薄壳入口 — 抓网页转 Markdown 打 stdout。"""
import sys

from lib.notify import consume_debug, consume_dry_run, consume_help, consume_no_say
from lib.skills_help import consume_skills
from lib.ui import timed
from lib.webgrab import main as _run


def main():
    sys.argv = consume_dry_run(consume_help(
        consume_skills(consume_debug(consume_no_say(sys.argv)), __doc__),
        __doc__,
        usage="webgrab [选项] <url>",
    ), __doc__)
    raise SystemExit(timed(_run, label="webgrab")(sys.argv))
