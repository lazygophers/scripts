"""gitnexuswatch — 登记 git 仓库，后台自动保持 gitnexus 代码图谱新鲜。

顶层命令:
  add <目录>      登记仓库（非 git 文件夹拆成里面的 git 仓库逐个登记）
  remove <目录>   注销仓库（给文件夹则注销其下全部）
  list           列出登记的仓库和索引新鲜度
  rebuild [目录]  前台立即重建一个仓库
  run            前台运行守护进程（全局单例）
  install        注册为系统服务（macOS launchd / Linux systemd --user / Windows 计划任务）
  status         服务状态 + 各仓库索引新鲜度

配置文件: ~/.config/lazygophers/scripts/gitnexuswatch.yaml（权限 0600）
依赖: gitnexus（npm i -g gitnexus）、watchdog
"""
from __future__ import annotations

import sys

from lib.fire_base import run_cli
from lib.gitnexuswatch import GitnexuswatchCli


def main():
    if len(sys.argv) <= 1:
        sys.argv.append("--skills")
    run_cli(GitnexuswatchCli())
