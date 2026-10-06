"""每个 lib/cli/*.py 的 main() 都是同一形状：run_cli(XCli())。

bin 薄壳靠子进程调它，覆盖率看不见；这里在进程内用假 run_cli 断言每个 main()
真的把对应 CLI 类交给了 fire 入口——参数错、类名对错位都在这一层拦。
"""
import importlib
import unittest
from unittest import mock


CLI_CLASSES = {
    "archery": "ArcheryCli", "browse": "BrowseCli", "check_ai": "CheckAiCli",
    "checkwork": "CheckworkCli", "cicd": "CicdCli", "cpd": None, "email": "EmailCli",
    "fetch_all": "FetchAllCli", "gitwf": None, "grafana": "GrafanaCli",
    "graphwatch": "GraphwatchCli", "inject": "InjectCli", "ipv6": "Ipv6Cli",
    "issue": "IssueCli", "kk": "KkCli", "kkp": "KkpCli", "lazyhelp": "LazyhelpCli",
    "list_branch": "ListBranchCli", "live_server": None, "loop": "LoopCli",
    "mr": "MrCli", "n": "NCli", "ovpn": "OvpnCli", "squash_pr": None,
    "switch_branch": "SwitchBranchCli", "sync_branch": "SyncBranchCli",
    "sync_master": "SyncMasterCli", "unsleep": "UnsleepCli", "vpn_prio": "VpnPrioCli",
    "webgrab": "WebgrabCli", "websearch": "WebsearchCli",
    "claude_session": None, "commit": "CommitCli", "delete_branch": None,
    "delete_branch_remote": None, "issue": "IssueCli",
}


class TestCliMainCallsRunCli(unittest.TestCase):
    def test_every_main_passes_its_cli_to_run_cli(self):
        checked = []
        for name, cls_name in CLI_CLASSES.items():
            if cls_name is None:
                continue  # 非标准形状（fire 直调 / 自带 main 逻辑），不在本守卫范围
            mod = importlib.import_module(f"lib.cli.{name}")
            if not hasattr(mod, "run_cli"):
                continue  # 自己写 main 的（browse 等），不在本守卫范围
            with mock.patch.object(mod, "run_cli") as rc:
                mod.main()
            rc.assert_called_once()
            self.assertIs(type(rc.call_args[0][0]).__name__, cls_name,
                          f"lib.cli.{name}.main() 交出的不是 {cls_name}")
            checked.append(name)
        self.assertGreater(len(checked), 15)


if __name__ == "__main__":
    unittest.main()


class TestDeleteBranchRemote(unittest.TestCase):
    def test_no_branches_is_an_error_and_yes_sets_env(self):
        from lib.cli import delete_branch_remote as mod

        # 分支缺失：直接报错，1 退出
        cli = mod.DeleteBranchRemoteCli()
        self.assertEqual(cli.here(), 1)

        # --yes 走 BATCH_NO_CONFIRM 环境变量（all 子命令）
        import os
        cli = mod.DeleteBranchRemoteCli()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BATCH_NO_CONFIRM", None)
            with mock.patch.object(mod, "delete_branch_remote_all", return_value=0) as allr:
                self.assertEqual(cli.all("feat", yes=True), 0)
            self.assertEqual(os.environ.get("BATCH_NO_CONFIRM"), "1")
        allr.assert_called_once_with("feat", remote="origin")
