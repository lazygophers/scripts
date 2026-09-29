"""archery mcp — 把 Archery 客户端暴露成 MCP server（stdio）。

凭据不读 archery.yaml，全部来自环境变量（MCP 客户端拉起本进程时注入）：

  ARCHERY_URL         站点地址（archery.example.com 或完整 URL）
  ARCHERY_USERNAME    用户名
  ARCHERY_PASSWORD    密码
  ARCHERY_TOTP_SECRET 可选，2FA 密钥（base32 或 otpauth:// 整串）
  ARCHERY_INSECURE=1  可选，跳过 TLS 证书校验（自签证书内网）

传输是 stdio 上每行一条 JSON-RPC（MCP stdio transport）。协议面只实现
AI 客户端实际会用的四条：initialize / tools/list / tools/call / ping。
客户端懒建：credentials 配错了 server 仍能起来，错误写进 tool result
（isError: true），不摔掉整个会话；token 只存内存，不落盘。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable

from lib.archery import ArcheryClient, ArcheryError, host_key, normalize_url
from lib.ovpn import normalize_secret

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "archery"
SERVER_VERSION = "1.0.0"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def env_client() -> ArcheryClient:
    """从环境变量造一个不落盘的客户端。"""
    url = _env("ARCHERY_URL")
    username = _env("ARCHERY_USERNAME")
    password = _env("ARCHERY_PASSWORD")
    totp_secret = _env("ARCHERY_TOTP_SECRET")
    insecure = _env("ARCHERY_INSECURE").lower() in ("1", "true", "yes")
    missing = [name for name, value in (("ARCHERY_URL", url), ("ARCHERY_USERNAME", username),
                                        ("ARCHERY_PASSWORD", password)) if not value]
    if missing:
        raise ArcheryError(
            "MCP 模式的凭据只认环境变量，缺: " + ", ".join(missing)
            + "（可选: ARCHERY_TOTP_SECRET / ARCHERY_INSECURE=1），不读 archery.yaml")
    site = normalize_url(url)
    profile = {"url": site, "username": username, "password": password,
               "totp_secret": normalize_secret(totp_secret) if totp_secret else "",
               "insecure": insecure, "token": {}}
    return ArcheryClient(host_key(site), profile, {}, persist=False)


# ---------------------------------------------------------------- 工具

def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props,
            **({"required": required} if required else {})}


def _s(desc: str = "") -> dict:
    return {"type": "string", "description": desc} if desc else {"type": "string"}


def _i(desc: str = "") -> dict:
    return {"type": "integer", "description": desc} if desc else {"type": "integer"}


def _api_or_web(api, web):
    """先走 REST API；老版本 Archery（1.9.x）没有 sqlquery 那组端点，404 时回落网页端。"""
    try:
        return api()
    except ArcheryError as e:
        if "HTTP 404" not in str(e):
            raise
        return web()


def _t_query_execute(client: ArcheryClient, args: dict):
    body = {"instance_name": args["instance_name"], "db_name": args["db_name"],
            "schema_name": str(args.get("schema_name") or ""), "tb_name": str(args.get("tb_name") or ""),
            "sql_content": args["sql"], "limit_num": int(args.get("limit_num") or 0)}
    return _api_or_web(
        lambda: client.post("v1/sqlquery/execute/", body),
        lambda: client.web("POST", "/query/", form=body),
    )


def _t_query_instances(client: ArcheryClient, args: dict):
    params = {k: v for k, v in (args.get("params") or {}).items()}
    return _api_or_web(
        lambda: client.get("v1/sqlquery/instances/", **params),
        lambda: client.web("POST", "/group/user_all_instances/", form=params),
    )


def _t_query_describe(client: ArcheryClient, args: dict):
    body = {"instance_name": args["instance_name"], "db_name": args["db_name"],
            "tb_name": args["tb_name"], "schema_name": str(args.get("schema_name") or "")}
    return _api_or_web(
        lambda: client.post("v1/sqlquery/describetable/", body),
        lambda: client.web("POST", "/instance/describetable/", form=body),
    )


def _t_query_logs(client: ArcheryClient, args: dict):
    params = {k: v for k, v in (args.get("params") or {}).items()}
    return _api_or_web(
        lambda: client.get("v1/sqlquery/logs/", **params),
        lambda: client.web("GET", "/query/querylog/", params=params),
    )


def _t_workflow_list(client: ArcheryClient, args: dict):
    return client.get("v1/workflow/", **{k: v for k, v in (args.get("params") or {}).items()})


def _t_workflow_check(client: ArcheryClient, args: dict):
    return client.post("v1/workflow/sqlcheck/", {
        "instance_id": int(args["instance_id"]), "db_name": args["db_name"],
        "full_sql": args["full_sql"],
    })


def _t_workflow_audit(client: ArcheryClient, args: dict):
    return client.post("v1/workflow/audit/", {
        "engineer": args["engineer"], "workflow_id": int(args["workflow_id"]),
        "audit_remark": str(args.get("audit_remark") or ""),
        "workflow_type": int(args.get("workflow_type") or 2),
        "audit_type": str(args.get("audit_type") or "pass"),
    })


def _t_workflow_execute(client: ArcheryClient, args: dict):
    workflow_type = int(args.get("workflow_type") or 2)
    body = {"workflow_id": int(args["workflow_id"]), "workflow_type": workflow_type}
    if workflow_type == 2:
        body["engineer"] = args["engineer"]
        body["mode"] = str(args.get("mode") or "auto")
    return client.post("v1/workflow/execute/", body)


def _t_schema_list(client: ArcheryClient, args: dict):
    data = client.request("GET", "/api/schema/", params={"format": "json"})
    if isinstance(data, str):
        import yaml

        data = yaml.safe_load(data)
    rows = []
    paths = (data or {}).get("paths") if isinstance(data, dict) else None
    for path, ops in sorted((paths or {}).items()):
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                rows.append({"method": method.upper(), "path": path,
                             "summary": str(op.get("summary") or "") if isinstance(op, dict) else ""})
    return rows


def _t_api(client: ArcheryClient, args: dict):
    data = args.get("data")
    if data is not None and not isinstance(data, dict):
        raise ArcheryError("data 必须是 JSON 对象")
    return client.request(
        str(args["method"]).upper(), str(args["path"]),
        params={k: v for k, v in (args.get("params") or {}).items()} or None,
        json_body=data,
    )


TOOLS: list[dict] = [
    {
        "name": "archery_query_execute",
        "description": "在指定实例上执行一条 SQL 查询，返回结果集（列名 + 行）。"
                       "只做在线查询；上线变更走 workflow。",
        "inputSchema": _obj({
            "sql": _s("SQL 语句，如 select 1"),
            "instance_name": _s("实例名（archery_query_instances 列出的）"),
            "db_name": _s("库名"),
            "schema_name": _s("可选，PgSQL/Oracle schema"),
            "tb_name": _s("可选，查询提示用的表名"),
            "limit_num": _i("可选，返回行数上限，0 = 服务端默认"),
        }, ["sql", "instance_name", "db_name"]),
        "fn": _t_query_execute,
    },
    {
        "name": "archery_query_instances",
        "description": "列出当前用户能查询的数据库实例",
        "inputSchema": _obj({"params": _obj({"db_type": _s("按类型过滤，如 mysql")})}),
        "fn": _t_query_instances,
    },
    {
        "name": "archery_query_describe",
        "description": "查看一张表的结构（字段 / 类型）",
        "inputSchema": _obj({
            "instance_name": _s("实例名"), "db_name": _s("库名"), "tb_name": _s("表名"),
            "schema_name": _s("可选"),
        }, ["instance_name", "db_name", "tb_name"]),
        "fn": _t_query_describe,
    },
    {
        "name": "archery_query_logs",
        "description": "历史查询记录（limit / offset / search / star 等过滤写进 params）",
        "inputSchema": _obj({"params": _obj({"limit": _i(), "search": _s("按 SQL 关键词过滤")})}),
        "fn": _t_query_logs,
    },
    {
        "name": "archery_workflow_list",
        "description": "SQL 上线工单清单（workflow__status / workflow__engineer / page / size 写进 params）",
        "inputSchema": _obj({"params": _obj({"workflow__status": _s("如 waiting / finished")})}),
        "fn": _t_workflow_list,
    },
    {
        "name": "archery_workflow_check",
        "description": "提交工单前的 SQL 语法 / 规则检查",
        "inputSchema": _obj({
            "instance_id": _i("实例 id"), "db_name": _s("库名"), "full_sql": _s("要检查的全部 SQL"),
        }, ["instance_id", "db_name", "full_sql"]),
        "fn": _t_workflow_check,
    },
    {
        "name": "archery_workflow_audit",
        "description": "审核工单（通过 / 驳回）",
        "inputSchema": _obj({
            "engineer": _s("审核人用户名"), "workflow_id": _i("工单 id"),
            "audit_remark": _s("审核备注"), "audit_type": _s("pass 或 cancel，默认 pass"),
        }, ["engineer", "workflow_id"]),
        "fn": _t_workflow_audit,
    },
    {
        "name": "archery_workflow_execute",
        "description": "执行工单（workflow_type 2 = SQL 上线需 engineer，3 = 数据归档）",
        "inputSchema": _obj({
            "workflow_id": _i("工单 id"), "engineer": _s("执行人用户名，workflow_type=2 必填"),
            "workflow_type": _i("2-SQL上线 3-数据归档，默认 2"), "mode": _s("auto 或 manual，默认 auto"),
        }, ["workflow_id"]),
        "fn": _t_workflow_execute,
    },
    {
        "name": "archery_schema_list",
        "description": "列出这个 Archery 站点支持的全部 API 端点，"
                      "调其余能力前先看这里",
        "inputSchema": _obj({}),
        "fn": _t_schema_list,
    },
    {
        "name": "archery_api",
        "description": "直接发任意请求（逃生门）：method + path + 可选 data/params。"
                       "path 写 `v1/user/` 自动补 /api/，或完整 URL。",
        "inputSchema": _obj({
            "method": _s("GET / POST / PUT / PATCH / DELETE"),
            "path": _s("如 v1/user/ 或 /api/v1/user/"),
            "data": _obj({}, None) | {"description": "请求体（POST/PUT）"},
            "params": _obj({}, None) | {"description": "查询参数"},
        }, ["method", "path"]),
        "fn": _t_api,
    },
]


# ---------------------------------------------------------------- 协议

def _ok(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _wire(tool: dict) -> dict:
    return {"name": tool["name"], "description": tool["description"],
            "inputSchema": tool["inputSchema"]}


def _handle(msg, get_client: Callable[[], ArcheryClient]) -> dict | None:
    if not isinstance(msg, dict):
        return None
    method = msg.get("method")
    msg_id = msg.get("id")
    if msg_id is None:  # notification（initialized / cancelled …）不需要回包
        return None
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return _ok(msg_id, {
            "protocolVersion": str(asked or PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": [_wire(t) for t in TOOLS]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = str(params.get("name") or "")
        tool = next((t for t in TOOLS if t["name"] == name), None)
        if tool is None:
            return _err(msg_id, -32602, f"unknown tool: {name}")
        try:
            data = tool["fn"](get_client(), params.get("arguments") or {})
            text = data if isinstance(data, str) else json.dumps(
                data, ensure_ascii=False, separators=(",", ":"))
            return _ok(msg_id, {"content": [{"type": "text", "text": text}]})
        except (ArcheryError, KeyError, TypeError, ValueError) as e:
            return _ok(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})
    return _err(msg_id, -32601, f"method not found: {method}")


def serve(instream=None, outstream=None) -> int:
    """MCP server 主循环：stdin 每行一条 JSON-RPC，回包写 stdout。"""
    instream = instream if instream is not None else sys.stdin
    out = outstream if outstream is not None else sys.stdout
    box: list[ArcheryClient | None] = [None]

    def get_client() -> ArcheryClient:
        if box[0] is None:
            box[0] = env_client()
        return box[0]

    for line in instream:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue  # 坏行丢弃：MCP 传输层按行分帧，回错误包对不上号
        resp = _handle(msg, get_client)
        if resp is not None:
            out.write(json.dumps(resp, ensure_ascii=False, separators=(",", ":")) + "\n")
            out.flush()
    return 0
