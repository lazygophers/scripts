"""graphwatch — graphify 全局 watch 守护服务：注册目录，后台自动重建知识图谱。

顶层命令:
  add <目录>      注册一个文件夹（daemon 自动热加载开始监听）
  remove <目录>   注销一个文件夹
  list           列出已注册的文件夹
  config         引导式配置向导（backend / api_key / debounce …）
  run            前台运行守护进程（全局单例）
  install        注册为系统服务（macOS launchd / Linux systemd --user / Windows 计划任务）
  uninstall      注销系统服务（配置与日志保留）
  status         服务状态 + 各目录图谱新鲜度

配置文件: ~/.config/lazygophers/scripts/graphwatch.yaml（权限 0600）
依赖: pip install '.[graphify]'（默认不装 graphify）
"""
from __future__ import annotations

import sys

from lib.fire_base import run_cli
from lib.graphwatch import GraphwatchCli


def main():
    if len(sys.argv) <= 1:
        sys.argv.append("--skills")
    run_cli(GraphwatchCli())
