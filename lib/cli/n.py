"""n — macOS 语音播报（fire 重构）"""
from __future__ import annotations

from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.notify import say_content


class NCli(BaseCli):
    """macOS 语音播报（`say`）"""

    def __call__(self, *content: str):
        """裸调用 `n <content...>` 等同 `n say <content>`"""
        if not content:
            self._r.err("n: 缺少内容")
            return 1
        return self.say(" ".join(content))

    @timed_cli
    def say(self, content: str):
        """播报内容

        用法: n say <content>
        """
        return say_content(content)


def main():
    run_cli(NCli())
