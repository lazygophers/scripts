"""archery CLI 层测试：子命令分派、参数拼装、root 门禁、输出分流、返回码。

全部在进程内跑：不起 HTTP（`lib.archery.ArcheryClient` 的端到端行为已由
tests/test_archery.py 的 FakeArchery 服务端覆盖，这里只验 CLI 怎么调它）、
不 sudo（`require_root` 一律 patch 掉）、不碰用户 HOME（配置读写全 patch）。
"""

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from lib.archery import ArcheryError  # noqa: E402
from lib.cli import archery as ac  # noqa: E402

# 子组列表：构造 CLI 后要把每个组的 reporter 都换成同一个 mock
_GROUP_PATHS = ("user", "user.group", "user.resourcegroup", "user.twofa",
                "instance", "instance.tunnel", "instance.rds", "query", "workflow")


def cli() -> ac.ArcheryCli:
    """构造 CLI，并把它和所有子组的 reporter 换成同一个 mock。"""
    c = ac.ArcheryCli()
    r = mock.MagicMock()
    c._r = r
    for path in _GROUP_PATHS:
        obj = c
        for part in path.split("."):
            obj = getattr(obj, part)
        obj._r = r
    return c


def run(fn, *args, **kwargs) -> tuple[int, str]:
    """跑一个子命令，返回 (退出码, stdout)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args, **kwargs)
    return rc, buf.getvalue()


def messages(reporter_mock, level: str) -> list[str]:
    return [call.args[0] for call in getattr(reporter_mock, level).call_args_list]


def tmp_file(text: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    f.write(text)
    f.close()
    return f.name


class ClientCase(unittest.TestCase):
    """基类：把 client_for 换成 mock 客户端，子命令不会真发请求。"""

    def setUp(self):
        self.client = mock.MagicMock()
        # 默认回一个空 dict：emit() 要把返回值 json.dumps，裸 MagicMock 序列化不了
        for name in ("get", "post", "put", "request", "web"):
            getattr(self.client, name).return_value = {}
        self.client.delete.return_value = None
        p = mock.patch.object(ac, "client_for", return_value=self.client)
        self.client_for = p.start()
        self.addCleanup(p.stop)
        self.cli = cli()


# ---------------------------------------------------------------- 公共件

class TestEmit(unittest.TestCase):
    """emit：None 静默、字符串原样、其余压缩 JSON。"""

    def test_none_prints_nothing(self):
        self.assertEqual(run(ac.emit, None)[1], "")

    def test_str_printed_verbatim(self):
        self.assertEqual(run(ac.emit, "hi")[1], "hi\n")

    def test_dict_printed_as_json(self):
        self.assertEqual(json.loads(run(ac.emit, {"a": 1})[1]), {"a": 1})


class TestConfigPath(unittest.TestCase):
    """--config 显式路径优先，否则按当前身份推断。"""

    def test_explicit_path_is_expanded(self):
        self.assertEqual(ac._config_path("~/x.yaml"), pathlib.Path.home() / "x.yaml")

    def test_empty_falls_back_to_default(self):
        want = pathlib.Path("/tmp/archery.yaml")
        with mock.patch.object(ac, "default_config_path", return_value=want):
            self.assertEqual(ac._config_path(""), want)


class TestCmdDecorator(ClientCase):
    """@cmd：ArcheryError 收敛成 rc=2 + 一行 err，不甩 traceback。"""

    def test_archery_error_becomes_rc2(self):
        self.client.get.side_effect = ArcheryError("token 过期")
        rc, _ = run(self.cli.info)
        self.assertEqual(rc, 2)
        self.assertIn("token 过期", messages(self.cli._r, "err"))

    def test_other_exception_still_raises(self):
        self.client.get.side_effect = ValueError("bug")
        with self.assertRaises(ValueError):
            self.cli.info()


class TestHostFlag(ClientCase):
    """--host 决定用哪个 profile：原样透给 client_for。"""

    def test_host_reaches_client_for(self):
        run(self.cli.info, host="b.example.com")
        self.assertEqual(self.client_for.call_args.args[0], "b.example.com")

    def test_nested_group_also_forwards_host(self):
        run(self.cli.user.twofa.state, "nico", host="b.example.com")
        self.assertEqual(self.client_for.call_args.args[0], "b.example.com")

    def test_default_host_is_empty(self):
        run(self.cli.info)
        self.assertEqual(self.client_for.call_args.args[0], "")


# ---------------------------------------------------------------- user

class TestUserCli(ClientCase):
    """user：增删改查 + 账号密码校验。"""

    def test_list_forwards_filters(self):
        rc, _ = run(self.cli.user.list, username="nico", size=50)
        self.assertEqual(rc, 0)
        self.client.get.assert_called_once_with("v1/user/", username="nico", size=50)

    def test_create_parses_data(self):
        run(self.cli.user.create, '{"username":"nico"}')
        self.client.post.assert_called_once_with("v1/user/", {"username": "nico"})

    def test_create_rejects_bad_json(self):
        rc, _ = run(self.cli.user.create, "不是 JSON")
        self.assertEqual(rc, 2)
        self.client.post.assert_not_called()

    def test_update_coerces_pk_into_path(self):
        run(self.cli.user.update, "7", {"display": "Nico"})
        self.client.put.assert_called_once_with("v1/user/7/", {"display": "Nico"})

    def test_delete_reports_ok(self):
        rc, out = run(self.cli.user.delete, 7)
        self.assertEqual((rc, out), (0, ""))
        self.client.delete.assert_called_once_with("v1/user/7/")
        self.assertIn("已删除用户 7", messages(self.cli._r, "ok"))

    def test_auth_posts_credentials(self):
        run(self.cli.user.auth, "nico", "secret")
        self.client.post.assert_called_once_with(
            "v1/user/auth/", {"engineer": "nico", "password": "secret"})


class TestUserGroupCli(ClientCase):
    """user group：Django 权限组。"""

    def test_list(self):
        run(self.cli.user.group.list, page=2)
        self.client.get.assert_called_once_with("v1/user/group/", page=2)

    def test_create(self):
        run(self.cli.user.group.create, '{"name":"dba"}')
        self.client.post.assert_called_once_with("v1/user/group/", {"name": "dba"})

    def test_update(self):
        run(self.cli.user.group.update, 3, '{"name":"dba"}')
        self.client.put.assert_called_once_with("v1/user/group/3/", {"name": "dba"})

    def test_delete(self):
        run(self.cli.user.group.delete, 3)
        self.client.delete.assert_called_once_with("v1/user/group/3/")
        self.assertIn("已删除用户组 3", messages(self.cli._r, "ok"))


class TestResourceGroupCli(ClientCase):
    """user resourcegroup：实例归属分组。"""

    def test_list(self):
        run(self.cli.user.resourcegroup.list)
        self.client.get.assert_called_once_with("v1/user/resourcegroup/")

    def test_create(self):
        run(self.cli.user.resourcegroup.create, '{"group_name":"核心库"}')
        self.client.post.assert_called_once_with(
            "v1/user/resourcegroup/", {"group_name": "核心库"})

    def test_update(self):
        run(self.cli.user.resourcegroup.update, 2, '{"group_name":"x"}')
        self.client.put.assert_called_once_with("v1/user/resourcegroup/2/", {"group_name": "x"})

    def test_delete(self):
        run(self.cli.user.resourcegroup.delete, 2)
        self.client.delete.assert_called_once_with("v1/user/resourcegroup/2/")
        self.assertIn("已删除资源组 2", messages(self.cli._r, "ok"))


class TestTwoFACli(ClientCase):
    """user twofa：2FA 开关与校验，可选字段为空时不进 body。"""

    def test_state(self):
        run(self.cli.user.twofa.state, "nico")
        self.client.post.assert_called_once_with("v1/user/2fa/state/", {"engineer": "nico"})

    def test_enable_defaults_to_totp_without_phone(self):
        run(self.cli.user.twofa.enable, "nico")
        self.client.post.assert_called_once_with(
            "v1/user/2fa/", {"engineer": "nico", "enable": "true", "auth_type": "totp"})

    def test_enable_sms_includes_phone(self):
        run(self.cli.user.twofa.enable, "nico", auth_type="sms", phone="13800000000")
        body = self.client.post.call_args.args[1]
        self.assertEqual(body["auth_type"], "sms")
        self.assertEqual(body["phone"], "13800000000")

    def test_disable(self):
        run(self.cli.user.twofa.disable, "nico")
        self.client.post.assert_called_once_with(
            "v1/user/2fa/", {"engineer": "nico", "enable": "false", "auth_type": "totp"})

    def test_save_includes_key_only_when_given(self):
        run(self.cli.user.twofa.save, "nico", key="JBSWY3DPEHPK3PXP")
        self.assertEqual(self.client.post.call_args.args[1],
                         {"engineer": "nico", "auth_type": "totp", "key": "JBSWY3DPEHPK3PXP"})

    def test_save_sms_includes_phone(self):
        run(self.cli.user.twofa.save, "nico", auth_type="sms", phone="13800000000")
        self.assertEqual(self.client.post.call_args.args[1],
                         {"engineer": "nico", "auth_type": "sms", "phone": "13800000000"})

    def test_verify_coerces_otp_to_int(self):
        run(self.cli.user.twofa.verify, "nico", "123456")
        self.assertEqual(self.client.post.call_args.args[1]["otp"], 123456)

    def test_verify_passes_key_and_phone_when_given(self):
        run(self.cli.user.twofa.verify, "nico", 123456,
            key="JBSWY3DPEHPK3PXP", phone="13800000000")
        body = self.client.post.call_args.args[1]
        self.assertEqual((body["key"], body["phone"]), ("JBSWY3DPEHPK3PXP", "13800000000"))


# ---------------------------------------------------------------- instance

class TestInstanceCli(ClientCase):
    """instance：实例增删改查、资源枚举、按表名反查。"""

    def test_list_renames_host_ip_filter(self):
        # --host 被本工具占用来选站点，实例 IP 过滤走 --host_ip
        run(self.cli.instance.list, host_ip="10.0.0.9", db_type="mysql")
        self.client.get.assert_called_once_with("v1/instance/", db_type="mysql", host="10.0.0.9")

    def test_list_without_host_ip(self):
        run(self.cli.instance.list, size=50)
        self.client.get.assert_called_once_with("v1/instance/", size=50)

    def test_create(self):
        run(self.cli.instance.create, '{"instance_name":"prod"}')
        self.client.post.assert_called_once_with("v1/instance/", {"instance_name": "prod"})

    def test_update(self):
        run(self.cli.instance.update, 5, '{"host":"10.0.0.9"}')
        self.client.put.assert_called_once_with("v1/instance/5/", {"host": "10.0.0.9"})

    def test_delete(self):
        run(self.cli.instance.delete, 5)
        self.client.delete.assert_called_once_with("v1/instance/5/")
        self.assertIn("已删除实例 5", messages(self.cli._r, "ok"))

    def test_resource_drops_empty_optionals(self):
        run(self.cli.instance.resource, "5", "database")
        self.client.post.assert_called_once_with(
            "v1/instance/resource/", {"instance_id": 5, "resource_type": "database"})

    def test_resource_includes_given_optionals(self):
        run(self.cli.instance.resource, 5, "column", db_name="orders", tb_name="t_order")
        self.assertEqual(self.client.post.call_args.args[1],
                         {"instance_id": 5, "resource_type": "column",
                          "db_name": "orders", "tb_name": "t_order"})

    def test_locate(self):
        run(self.cli.instance.locate, "t_order")
        self.client.post.assert_called_once_with(
            "v1/instance/table-instances/", {"table_name": "t_order"})


class TestTunnelAndRdsCli(ClientCase):
    """instance tunnel / rds：两个薄子组。"""

    def test_tunnel_list(self):
        run(self.cli.instance.tunnel.list, page=1)
        self.client.get.assert_called_once_with("v1/instance/tunnel/", page=1)

    def test_tunnel_create_reads_at_file(self):
        path = tmp_file('{"tunnel_name":"t1"}', ".json")
        self.addCleanup(pathlib.Path(path).unlink)
        run(self.cli.instance.tunnel.create, f"@{path}")
        self.client.post.assert_called_once_with("v1/instance/tunnel/", {"tunnel_name": "t1"})

    def test_rds_list(self):
        run(self.cli.instance.rds.list)
        self.client.get.assert_called_once_with("v1/instance/rds/")

    def test_rds_create(self):
        run(self.cli.instance.rds.create, '{"rds_name":"r1"}')
        self.client.post.assert_called_once_with("v1/instance/rds/", {"rds_name": "r1"})


# ---------------------------------------------------------------- query

class TestApiOrWeb(unittest.TestCase):
    """_api_or_web：只有 HTTP 404（老站点没这组 REST 端点）才回落网页端。"""

    def test_api_result_wins(self):
        self.assertEqual(ac._api_or_web(lambda: "api", lambda: "web"), "api")

    def test_404_falls_back_to_web(self):
        def boom():
            raise ArcheryError("HTTP 404 Not Found")
        self.assertEqual(ac._api_or_web(boom, lambda: "web"), "web")

    def test_other_error_propagates(self):
        def boom():
            raise ArcheryError("HTTP 500 boom")
        with self.assertRaises(ArcheryError):
            ac._api_or_web(boom, lambda: "web")


class TestResultRows(unittest.TestCase):
    """_result_rows：认 data 包一层或不包，行可以是 list 也可以是 dict。"""

    def test_wrapped_payload_with_list_rows(self):
        got = ac._result_rows({"data": {"column_list": ["id"], "rows": [[1]]}})
        self.assertEqual(got, (["id"], [[1]]))

    def test_flat_payload_with_dict_rows(self):
        got = ac._result_rows({"column_list": ["id", "name"], "rows": [{"name": "a", "id": 1}]})
        self.assertEqual(got, (["id", "name"], [[1, "a"]]))

    def test_wrong_shape_returns_none(self):
        self.assertIsNone(ac._result_rows({"data": {"rows": "nope"}}))
        self.assertIsNone(ac._result_rows(["not", "a", "dict"]))


class TestQueryCli(ClientCase):
    """query：REST 优先、404 回落网页端，execute 的三种输出形态。"""

    def test_instances_uses_rest_first(self):
        run(self.cli.query.instances, db_type="mysql")
        self.client.get.assert_called_once_with("v1/sqlquery/instances/", db_type="mysql")
        self.client.web.assert_not_called()

    def test_instances_falls_back_to_web_on_404(self):
        self.client.get.side_effect = ArcheryError("HTTP 404 Not Found")
        self.client.web.return_value = {"data": []}
        rc, _ = run(self.cli.query.instances, db_type="mysql")
        self.assertEqual(rc, 0)
        self.client.web.assert_called_once_with(
            "POST", "/group/user_all_instances/", form={"db_type": "mysql"})

    def test_resources_includes_instance_id_only_when_given(self):
        run(self.cli.query.resources, "table", instance_name="prod", db_name="orders")
        params = self.client.get.call_args.kwargs
        self.assertNotIn("instance_id", params)
        self.assertEqual(params["resource_type"], "table")

    def test_resources_with_instance_id(self):
        run(self.cli.query.resources, "table", instance_id="5")
        self.assertEqual(self.client.get.call_args.kwargs["instance_id"], 5)

    def test_describe(self):
        run(self.cli.query.describe, "prod", "orders", "t_order")
        self.client.post.assert_called_once_with(
            "v1/sqlquery/describetable/",
            {"instance_name": "prod", "db_name": "orders", "tb_name": "t_order", "schema_name": ""})

    def test_logs(self):
        run(self.cli.query.logs, limit=20)
        self.client.get.assert_called_once_with("v1/sqlquery/logs/", limit=20)

    def test_favorite_star_is_string_true(self):
        run(self.cli.query.favorite, "123", alias="订单")
        self.assertEqual(self.client.post.call_args.args[1],
                         {"query_log_id": 123, "star": "true", "alias": "订单"})

    def test_favorite_no_star(self):
        run(self.cli.query.favorite, 123, star=False)
        self.assertEqual(self.client.post.call_args.args[1]["star"], "false")


class TestQueryExecute(ClientCase):
    """query execute：SQL 来源、输出格式分流、NULL 处理。"""

    def setUp(self):
        super().setUp()
        self.client.post.return_value = {
            "data": {"column_list": ["id", "name"], "rows": [[1, "a"], [2, None]]}}

    def test_default_output_is_tsv(self):
        rc, out = run(self.cli.query.execute, "select 1",
                      instance_name="prod", db_name="orders")
        self.assertEqual(rc, 0)
        self.assertEqual(out, "id\tname\n1\ta\n2\t\n")  # NULL → 空串

    def test_table_flag_uses_rich_table(self):
        rc, out = run(self.cli.query.execute, "select 1", instance_name="prod",
                      db_name="orders", table=True)
        self.assertEqual((rc, out), (0, ""))
        self.cli._r.data_table.assert_called_once_with(["id", "name"], [[1, "a"], [2, ""]])

    def test_json_out_prints_raw_payload(self):
        rc, out = run(self.cli.query.execute, "select 1", instance_name="prod",
                      db_name="orders", json_out=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"]["rows"], [[1, "a"], [2, None]])

    def test_non_tabular_payload_falls_back_to_json(self):
        self.client.post.return_value = {"status": 1, "msg": "无权限"}
        rc, out = run(self.cli.query.execute, "select 1", instance_name="prod", db_name="orders")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["msg"], "无权限")

    def test_sql_body_carries_limit_and_content(self):
        run(self.cli.query.execute, "select 1", instance_name="prod",
            db_name="orders", limit_num="100")
        body = self.client.post.call_args.args[1]
        self.assertEqual(body["sql_content"], "select 1")
        self.assertEqual(body["limit_num"], 100)

    def test_at_file_is_read(self):
        path = tmp_file("select 2\n", ".sql")
        self.addCleanup(pathlib.Path(path).unlink)
        run(self.cli.query.execute, f"@{path}", instance_name="prod", db_name="orders")
        self.assertEqual(self.client.post.call_args.args[1]["sql_content"], "select 2\n")

    def test_missing_sql_file_exits_2(self):
        rc, _ = run(self.cli.query.execute, "@/nope/missing.sql",
                    instance_name="prod", db_name="orders")
        self.assertEqual(rc, 2)
        self.client.post.assert_not_called()

    def test_falls_back_to_web_on_404(self):
        self.client.post.side_effect = ArcheryError("HTTP 404 Not Found")
        self.client.web.return_value = {"data": {"column_list": ["id"], "rows": [[9]]}}
        rc, out = run(self.cli.query.execute, "select 1", instance_name="prod", db_name="orders")
        self.assertEqual((rc, out), (0, "id\n9\n"))
        self.assertEqual(self.client.web.call_args.args[:2], ("POST", "/query/"))


# ---------------------------------------------------------------- workflow

class TestWorkflowCli(ClientCase):
    """workflow：工单清单、检查、提交、审核、执行、日志。"""

    def test_list_forwards_filters(self):
        run(self.cli.workflow.list, size=20)
        self.client.get.assert_called_once_with("v1/workflow/", size=20)

    def test_check_inline_sql(self):
        run(self.cli.workflow.check, "5", "orders", "select 1")
        self.client.post.assert_called_once_with(
            "v1/workflow/sqlcheck/",
            {"instance_id": 5, "db_name": "orders", "full_sql": "select 1"})

    def test_check_reads_at_file(self):
        path = tmp_file("alter table t add c int;\n", ".sql")
        self.addCleanup(pathlib.Path(path).unlink)
        run(self.cli.workflow.check, 5, "orders", f"@{path}")
        self.assertEqual(self.client.post.call_args.args[1]["full_sql"],
                         "alter table t add c int;\n")

    def test_check_missing_file_exits_2(self):
        rc, _ = run(self.cli.workflow.check, 5, "orders", "@/nope/change.sql")
        self.assertEqual(rc, 2)
        self.client.post.assert_not_called()

    def test_submit_parses_data(self):
        run(self.cli.workflow.submit, '{"workflow":{"workflow_name":"x"}}')
        self.client.post.assert_called_once_with(
            "v1/workflow/", {"workflow": {"workflow_name": "x"}})

    def test_audit_body(self):
        run(self.cli.workflow.audit, "nico", "42", "看过了")
        self.client.post.assert_called_once_with("v1/workflow/audit/", {
            "engineer": "nico", "workflow_id": 42, "audit_remark": "看过了",
            "workflow_type": 2, "audit_type": "pass"})

    def test_auditlist(self):
        run(self.cli.workflow.auditlist, "nico", page=1)
        self.client.post.assert_called_once_with(
            "v1/workflow/auditlist/", {"engineer": "nico"}, page=1)

    def test_execute_sql_workflow_requires_engineer(self):
        rc, _ = run(self.cli.workflow.execute, 42)
        self.assertEqual(rc, 2)
        self.client.post.assert_not_called()
        self.assertIn("SQL 上线工单必须带 --engineer（执行人用户名）",
                      messages(self.cli._r, "err"))

    def test_execute_sql_workflow_with_engineer(self):
        run(self.cli.workflow.execute, "42", engineer="nico")
        self.client.post.assert_called_once_with("v1/workflow/execute/", {
            "workflow_id": 42, "workflow_type": 2, "engineer": "nico", "mode": "auto"})

    def test_execute_archive_workflow_needs_no_engineer(self):
        rc, _ = run(self.cli.workflow.execute, 42, workflow_type=3)
        self.assertEqual(rc, 0)
        self.client.post.assert_called_once_with(
            "v1/workflow/execute/", {"workflow_id": 42, "workflow_type": 3})

    def test_log(self):
        run(self.cli.workflow.log, "42", page=2)
        self.client.post.assert_called_once_with(
            "v1/workflow/log/", {"workflow_id": 42, "workflow_type": 2}, page=2)


# ---------------------------------------------------------------- 顶层

class ConfigCase(unittest.TestCase):
    """基类：配置读写全在内存里，不碰用户 HOME。"""

    def setUp(self):
        self.cfg = {"current": "a.com", "profiles": {
            "a.com": {"url": "https://a.com", "username": "nico", "password": "pw",
                      "totp_secret": "", "token": {"access": "at", "refresh": "rt"}},
            "b.com": {"url": "https://b.com", "username": "bob"},
        }}
        self.saved = {}
        for p in (mock.patch.object(ac, "config_lock"),
                  mock.patch.object(ac, "load_config", side_effect=lambda *a, **k: self.cfg),
                  mock.patch.object(ac, "save_config", side_effect=self.saved.update)):
            p.start()
            self.addCleanup(p.stop)
        self.cli = cli()


class TestHosts(ConfigCase):
    """hosts：空配置 rc=1 并指路 login；有配置列出并标 ★。"""

    def test_empty_config_exits_1(self):
        self.cfg = {}
        rc, _ = run(self.cli.hosts)
        self.assertEqual(rc, 1)
        self.assertIn("archery login", messages(self.cli._r, "warn")[0])

    def test_marks_current_site(self):
        rc, _ = run(self.cli.hosts)
        self.assertEqual(rc, 0)
        rows = self.cli._r.kv.call_args.args[1]
        self.assertEqual(sorted(rows), ["  b.com", "★ a.com"])
        self.assertIn("用户 nico", rows["★ a.com"])

    def test_missing_username_shows_placeholder(self):
        self.cfg["profiles"]["b.com"].pop("username")
        run(self.cli.hosts)
        self.assertIn("(未设置)", self.cli._r.kv.call_args.args[1]["  b.com"])


class TestUse(ConfigCase):
    """use：切默认站点。"""

    def test_switch_writes_current(self):
        rc, _ = run(self.cli.use, "b.com")
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved["current"], "b.com")

    def test_unknown_host_exits_2(self):
        rc, _ = run(self.cli.use, "nope.com")
        self.assertEqual(rc, 2)
        self.assertEqual(self.saved, {})


class TestLogout(ConfigCase):
    """logout：默认只清 token/cookie，--forget 连账号一起删。"""

    def test_clears_token_and_cookies(self):
        rc, _ = run(self.cli.logout)
        self.assertEqual(rc, 0)
        profile = self.saved["profiles"]["a.com"]
        self.assertEqual((profile["token"], profile["web_cookies"]), ({}, {}))
        self.assertEqual(profile["username"], "nico")  # 账号密码保留

    def test_forget_removes_profile_and_moves_current(self):
        rc, _ = run(self.cli.logout, forget=True)
        self.assertEqual(rc, 0)
        self.assertNotIn("a.com", self.saved["profiles"])
        self.assertEqual(self.saved["current"], "b.com")

    def test_forget_last_profile_clears_current(self):
        self.cfg["profiles"].pop("b.com")
        run(self.cli.logout, forget=True)
        self.assertEqual(self.saved["current"], "")

    def test_forget_other_host_keeps_current(self):
        rc, _ = run(self.cli.logout, host="b.com", forget=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved["current"], "a.com")


class TestLogin(ConfigCase):
    """login：交互补参数、2FA 密钥格式校验、登录成功才落盘。"""

    def setUp(self):
        super().setUp()
        self.client = mock.MagicMock()
        p = mock.patch.object(ac, "ArcheryClient", return_value=self.client)
        self.client_cls = p.start()
        self.addCleanup(p.stop)

    def test_full_args_login_and_save(self):
        rc, _ = run(self.cli.login, url="archery.example.com", username="nico", password="pw")
        self.assertEqual(rc, 0)
        self.client.login.assert_called_once_with()
        self.assertEqual(self.saved["current"], "archery.example.com")
        profile = self.client_cls.call_args.args[1]
        self.assertEqual(profile["url"], "https://archery.example.com")
        self.assertEqual(profile["token"], {})

    def test_no_current_keeps_old_default(self):
        rc, _ = run(self.cli.login, url="c.com", username="nico", password="pw", current=False)
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved, {})  # 没写 current 就不落这一步
        self.assertTrue(messages(self.cli._r, "info"))

    def test_prompts_for_missing_fields(self):
        with mock.patch.object(ac, "ask_text", side_effect=["c.com", "nico", "pw"]), \
             mock.patch.object(ac, "ask_confirm", return_value=False):
            rc, _ = run(self.cli.login)
        self.assertEqual(rc, 0)
        self.assertEqual(self.saved["current"], "c.com")

    def test_empty_url_cancels(self):
        with mock.patch.object(ac, "ask_text", return_value="  "):
            rc, _ = run(self.cli.login)
        self.assertEqual(rc, 1)
        self.client.login.assert_not_called()

    def test_empty_username_cancels(self):
        with mock.patch.object(ac, "ask_text", side_effect=["c.com", "  "]):
            rc, _ = run(self.cli.login)
        self.assertEqual(rc, 1)

    def test_empty_password_cancels(self):
        with mock.patch.object(ac, "ask_text", side_effect=["c.com", "nico", ""]):
            rc, _ = run(self.cli.login)
        self.assertEqual(rc, 1)

    def test_asks_for_totp_secret_when_confirmed(self):
        with mock.patch.object(ac, "ask_confirm", return_value=True), \
             mock.patch.object(ac, "ask_text", return_value="JBSWY3DPEHPK3PXP"):
            rc, _ = run(self.cli.login, url="c.com", username="nico", password="pw")
        self.assertEqual(rc, 0)
        self.assertEqual(self.client_cls.call_args.args[1]["totp_secret"], "JBSWY3DPEHPK3PXP")
        self.assertIn("2FA 密钥格式可用", messages(self.cli._r, "ok"))

    def test_bad_totp_secret_exits_2_before_login(self):
        rc, _ = run(self.cli.login, url="c.com", username="nico", password="pw",
                    totp_secret="这不是密钥")
        self.assertEqual(rc, 2)
        self.client.login.assert_not_called()

    def test_login_failure_leaves_config_untouched(self):
        self.client.login.side_effect = ArcheryError("用户名或密码错误")
        rc, _ = run(self.cli.login, url="c.com", username="nico", password="bad")
        self.assertEqual(rc, 2)
        self.assertEqual(self.saved, {})

    def test_reuses_saved_totp_secret_of_known_host(self):
        self.cfg["profiles"]["a.com"]["totp_secret"] = "JBSWY3DPEHPK3PXP"
        rc, _ = run(self.cli.login, url="a.com", username="nico", password="pw")
        self.assertEqual(rc, 0)
        self.assertEqual(self.client_cls.call_args.args[1]["totp_secret"], "JBSWY3DPEHPK3PXP")

    def test_insecure_is_sticky(self):
        self.cfg["profiles"]["a.com"]["insecure"] = True
        run(self.cli.login, url="a.com", username="nico", password="pw")
        self.assertIs(self.client_cls.call_args.args[1]["insecure"], True)


class RootGateCase(ConfigCase):
    """基类：root 门禁一律 patch 掉——真跑会 execvp sudo 换掉整个测试进程。"""

    def setUp(self):
        super().setUp()
        p = mock.patch.object(ac, "require_root")
        self.require_root = p.start()
        self.addCleanup(p.stop)


class TestShow(RootGateCase):
    """show：先过 root 门禁，再明文打出凭据。"""

    def test_requires_root_before_reading_config(self):
        rc, _ = run(self.cli.show)
        self.assertEqual(rc, 0)
        self.require_root.assert_called_once()
        self.assertEqual(self.require_root.call_args.args[0], ac.SCRIPT_PATH)

    def test_config_flag_reaches_root_gate(self):
        run(self.cli.show, config="/tmp/other.yaml")
        self.assertEqual(self.require_root.call_args.args[2], pathlib.Path("/tmp/other.yaml"))

    def test_prints_credentials_of_selected_host(self):
        run(self.cli.show, host="b.com")
        rows = self.cli._r.kv.call_args.args[1]
        self.assertEqual(rows["站点地址"], "https://b.com")
        self.assertEqual(rows["是否默认站点"], "否")

    def test_missing_fields_show_placeholder(self):
        run(self.cli.show, host="b.com")
        rows = self.cli._r.kv.call_args.args[1]
        self.assertEqual(rows["密码"], "(未设置)")
        self.assertEqual(rows["access token"], "(未设置)")

    def test_insecure_is_reported(self):
        self.cfg["profiles"]["a.com"]["insecure"] = True
        run(self.cli.show)
        self.assertEqual(self.cli._r.kv.call_args.args[1]["TLS 校验"], "关（--insecure）")


class TestCode(RootGateCase):
    """code：同样要 root；没配密钥 rc=1，密钥坏 rc=2。"""

    def test_prints_six_digit_code(self):
        self.cfg["profiles"]["a.com"]["totp_secret"] = "JBSWY3DPEHPK3PXP"
        rc, out = run(self.cli.code)
        self.assertEqual(rc, 0)
        self.assertRegex(out.strip(), r"^\d{6}$")
        self.require_root.assert_called_once()

    def test_without_secret_exits_1(self):
        rc, out = run(self.cli.code)
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("没配 2FA 密钥", messages(self.cli._r, "err")[0])

    def test_broken_secret_exits_2(self):
        self.cfg["profiles"]["a.com"]["totp_secret"] = "!!!!"
        rc, out = run(self.cli.code)
        self.assertEqual((rc, out), (2, ""))


class TestInfoAndSchema(ClientCase):
    """info / schema：端点清单的过滤、原始输出与空结果。"""

    def setUp(self):
        super().setUp()
        self.schema = {"paths": {
            "/api/v1/user/": {"get": {"summary": "列出用户"}, "post": {}},
            "/api/v1/workflow/": {"get": {"description": "工单清单\n第二行"}},
        }}

    def test_info_hits_api_info(self):
        self.client.get.return_value = {"version": "1.9.0"}
        rc, out = run(self.cli.info)
        self.assertEqual(rc, 0)
        self.client.get.assert_called_once_with("/api/info")
        self.assertEqual(json.loads(out)["version"], "1.9.0")

    def test_schema_lists_endpoints(self):
        self.client.request.return_value = self.schema
        rc, out = run(self.cli.schema)
        self.assertEqual(rc, 0)
        self.assertIn("GET    /api/v1/user/   # 列出用户", out)
        self.assertIn("POST   /api/v1/user/", out)
        self.client.request.assert_called_once_with(
            "GET", "/api/schema/", params={"format": "json"})

    def test_schema_grep_filters(self):
        self.client.request.return_value = self.schema
        rc, out = run(self.cli.schema, grep="workflow")
        self.assertEqual(rc, 0)
        self.assertNotIn("/api/v1/user/", out)
        self.assertIn("工单清单", out)  # 多行 description 只取首行

    def test_schema_grep_without_match_exits_1(self):
        self.client.request.return_value = self.schema
        rc, out = run(self.cli.schema, grep="不存在")
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("没有匹配的端点", messages(self.cli._r, "warn"))

    def test_schema_raw_dumps_json(self):
        self.client.request.return_value = self.schema
        rc, out = run(self.cli.schema, raw=True)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), self.schema)

    def test_schema_parses_yaml_response(self):
        # 有的站点不认 ?format=json，回的是 YAML 文本
        self.client.request.return_value = "paths:\n  /api/v1/user/:\n    get:\n      summary: 列出用户\n"
        rc, out = run(self.cli.schema)
        self.assertEqual(rc, 0)
        self.assertIn("/api/v1/user/", out)


class TestApiCommand(ClientCase):
    """api：方法 / 路径 / body / 查询参数的组装。"""

    def test_get_without_data_sends_no_body(self):
        self.client.request.return_value = {"ok": 1}
        rc, out = run(self.cli.api, "get", "v1/user/", size=50)
        self.assertEqual(rc, 0)
        self.client.request.assert_called_once_with(
            "get", "v1/user/", params={"size": 50}, json_body=None)
        self.assertEqual(json.loads(out), {"ok": 1})

    def test_post_with_inline_json_body(self):
        run(self.cli.api, "post", "v1/workflow/sqlcheck/", data='{"db_name":"orders"}')
        self.assertEqual(self.client.request.call_args.kwargs["json_body"], {"db_name": "orders"})

    def test_data_from_at_file(self):
        path = tmp_file('{"instance_id": 5}', ".json")
        self.addCleanup(pathlib.Path(path).unlink)
        run(self.cli.api, "post", "v1/workflow/sqlcheck/", data=f"@{path}")
        self.assertEqual(self.client.request.call_args.kwargs["json_body"], {"instance_id": 5})

    def test_missing_data_file_exits_2(self):
        rc, _ = run(self.cli.api, "post", "v1/user/", data="@/nope/body.json")
        self.assertEqual(rc, 2)
        self.client.request.assert_not_called()

    def test_empty_data_string_becomes_empty_body(self):
        run(self.cli.api, "post", "v1/user/", data="")
        self.assertEqual(self.client.request.call_args.kwargs["json_body"], {})

    def test_no_params_sends_none(self):
        run(self.cli.api, "delete", "v1/user/7/")
        self.assertIsNone(self.client.request.call_args.kwargs["params"])


class TestMain(unittest.TestCase):
    """main：裸跑补 --skills，其余原样交给 fire。"""

    def test_bare_call_appends_skills(self):
        with mock.patch.object(sys, "argv", ["archery"]), \
             mock.patch.object(ac, "run_cli") as run_cli:
            ac.main()
            self.assertEqual(sys.argv, ["archery", "--skills"])
        run_cli.assert_called_once()
        self.assertIsInstance(run_cli.call_args.args[0], ac.ArcheryCli)

    def test_subcommand_argv_untouched(self):
        with mock.patch.object(sys, "argv", ["archery", "hosts"]), \
             mock.patch.object(ac, "run_cli"):
            ac.main()
            self.assertEqual(sys.argv, ["archery", "hosts"])


if __name__ == "__main__":
    unittest.main()
