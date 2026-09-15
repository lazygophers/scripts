"""email — 多邮箱收发邮件。

子命令:
  login            配置一个邮箱（选服务商 → 照着引导拿授权码 → 真连一次验证 → 保存）
  list             列出已配置的邮箱
  use <邮箱>       设为默认邮箱
  remove <邮箱>    删掉一个邮箱的配置
  check            对已配置的邮箱重新验证一次连接
  send             发信（可带附件）
  inbox            列最近的邮件
  read <uid>       读一封的正文
  search <关键词>  全文搜索
  attach <uid>     把一封的附件下载下来
  mark <uid>       标已读 / 标未读

配置文件: ~/.config/lazygophers/scripts/email.yaml（0600），按邮箱地址分开存。
配了多个邮箱时，命令加 --email 指定用哪个；只配了一个就不用加。
支持: QQ / Gmail / 163 / 126 / iCloud / Fastmail / Zoho（都用应用专用密码或授权码）
"""
from __future__ import annotations

import pathlib
import sys

from lib.email import (
    PROVIDERS,
    MailError,
    build_profile,
    config_lock,
    default_config_path,
    email_key,
    load_config,
    profiles,
    provider_for,
    put_profile,
    resolve_profile,
    save_config,
)
from lib.fire_base import BaseCli, run_cli, timed_cli
from lib.profile_store import mask


def _cmd(method):
    """子命令装饰器：计时 + MailError 转一行人话（同 archery / graphwatch 的做法）。"""

    @timed_cli
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except MailError as e:
            self._r.err(str(e))
            return 2

    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class EmailCli(BaseCli):
    """email CLI。子命令：login / list / use / remove / check / send / inbox / read / search / attach / mark。"""

    # ------------------------------------------------------------ 配置

    @_cmd
    def login(self, email: str = "", provider: str = "") -> int:
        """配置一个邮箱：选服务商 → 照引导拿授权码 → 真连一次验证 → 通过才保存。

        用法: email login                       # 全程引导
              email login --email me@qq.com     # 直接指定邮箱，服务商自动认
        """
        from lib.ui import ask_confirm, ask_secret, ask_select, ask_text

        address = email or (ask_text("邮箱地址") or "")
        address = email_key(address)

        name = provider or provider_for(address)
        if not name:
            labels = [f"{k} — {v['label']}" for k, v in PROVIDERS.items()]
            picked = ask_select(f"{address} 是哪家的邮箱？", labels)
            if picked is None:
                self._r.warn("没选服务商，取消")
                return 1
            name = picked.split(" — ")[0]
        if name not in PROVIDERS:
            raise MailError(f"不认识的服务商: {name}（可选: {', '.join(PROVIDERS)}）")
        spec = PROVIDERS[name]
        self._r.ok(f"服务商: {spec['label']}")

        self._r.rule("怎么拿到授权码")
        for i, line in enumerate(spec["guide"], 1):
            self._r.info(f"{i}. {line}")
        if spec.get("warn"):
            self._r.warn(spec["warn"])

        secret = ask_secret(f"{address} 的应用专用密码 / 授权码（输入时不显示）")
        if not secret:
            self._r.warn("没输入密码，取消")
            return 1
        secret = secret.replace(" ", "")  # Gmail 的 16 位是带空格展示的

        imap, smtp = dict(spec["imap"]), dict(spec["smtp"])
        self._r.kv("默认服务器", {
            "IMAP": f"{imap['host']}:{imap['port']}",
            "SMTP": f"{smtp['host']}:{smtp['port']}（{smtp['mode']}）",
        })
        if ask_confirm("要改服务器地址吗？（付费 Zoho 组织域名、自定义域名才需要）", default=False):
            imap["host"] = ask_text("IMAP 地址", default=imap["host"]) or imap["host"]
            imap["port"] = int(ask_text("IMAP 端口", default=str(imap["port"])) or imap["port"])
            smtp["host"] = ask_text("SMTP 地址", default=smtp["host"]) or smtp["host"]
            smtp["port"] = int(ask_text("SMTP 端口", default=str(smtp["port"])) or smtp["port"])
            smtp["mode"] = ask_text("SMTP 加密（ssl / starttls）", default=smtp["mode"]) or smtp["mode"]

        profile = build_profile(address, name, secret, imap=imap, smtp=smtp)
        return self._verify_then_save(address, profile)

    def _verify_then_save(self, address: str, profile: dict) -> int:
        """保存前先真连一次。不通过就问要不要仍然保存（用户 2026-09-10 指定的行为）。"""
        from lib.email import check as do_check
        from lib.ui import ask_confirm

        self._r.step(f"正在验证 {address}（真连一次 IMAP 和 SMTP）…")
        ok, detail = do_check(address, profile)
        if ok:
            self._r.ok(detail)
        else:
            self._r.err(f"验证没通过: {detail}")
            if not ask_confirm("仍然保存这份配置吗？", default=False):
                self._r.warn("没保存。改完再跑一次 `email login`")
                return 1
            self._r.warn("已按你的要求保存一份没验证通过的配置")
        with config_lock():
            cfg = load_config()
            put_profile(cfg, address, profile)
            save_config(cfg)
        self._r.ok(f"已保存: {address}（配置在 {default_config_path()}）")
        return 0 if ok else 1

    @_cmd
    def list(self) -> int:
        """列出已配置的邮箱。

        用法: email list
        """
        from rich.table import Table

        cfg = load_config()
        all_p = profiles(cfg)
        if not all_p:
            self._r.info("还没有配置任何邮箱。先跑: email login")
            return 0
        current = str(cfg.get("current") or "")
        table = Table(title=f"已配置的邮箱（{len(all_p)} 个）")
        table.add_column("邮箱", style="bold")
        table.add_column("服务商")
        table.add_column("IMAP")
        table.add_column("密码")
        table.add_column("默认")
        for key in sorted(all_p):
            p = all_p[key]
            imap = p.get("imap") or {}
            table.add_row(key, str(p.get("provider") or "?"),
                          f"{imap.get('host')}:{imap.get('port')}",
                          mask(str(p.get("password") or "")),
                          "✓" if key == current else "")
        self._r.console.print(table)
        return 0

    @_cmd
    def use(self, email: str) -> int:
        """把某个邮箱设为默认（不加 --email 时就用它）。

        用法: email use me@qq.com
        """
        key = email_key(email)
        with config_lock():
            cfg = load_config()
            if key not in profiles(cfg):
                raise MailError(f"没有这个邮箱的配置: {key}。跑 `email login --email {email}`")
            cfg["current"] = key
            save_config(cfg)
        self._r.ok(f"默认邮箱: {key}")
        return 0

    @_cmd
    def remove(self, email: str) -> int:
        """删掉一个邮箱的配置。

        用法: email remove me@qq.com
        """
        key = email_key(email)
        with config_lock():
            cfg = load_config()
            all_p = profiles(cfg)
            if key not in all_p:
                raise MailError(f"没有这个邮箱的配置: {key}")
            all_p.pop(key)
            cfg["profiles"] = all_p
            if cfg.get("current") == key:
                cfg["current"] = next(iter(sorted(all_p)), "")
            save_config(cfg)
        self._r.ok(f"已删除: {key}")
        return 0

    @_cmd
    def check(self, email: str = "") -> int:
        """对已配置的邮箱重新验证一次连接（换过授权码、怀疑过期时用）。

        用法: email check
              email check --email me@qq.com
        """
        from lib.email import check as do_check

        address, profile = resolve_profile(load_config(), email)
        self._r.step(f"正在验证 {address}…")
        ok, detail = do_check(address, profile)
        (self._r.ok if ok else self._r.err)(detail)
        return 0 if ok else 1

    # ------------------------------------------------------------ 收发

    @_cmd
    def send(self, to: str, subject: str, body: str = "", cc: str = "",
             attach: str = "", email: str = "") -> int:
        """发一封邮件，可带附件。

        用法: email send --to a@b.com --subject 标题 --body 正文
              email send --to a@b.com,c@d.com --subject 报告 --body 见附件 --attach ./x.pdf
              email send --to a@b.com --subject 标题 --body - < 正文.txt
        """
        from lib.email import send as do_send

        address, profile = resolve_profile(load_config(), email)
        text = sys.stdin.read() if body == "-" else body
        files = [f for f in attach.split(",") if f.strip()] if attach else []
        rcpts = do_send(address, profile, to=to, subject=subject, body=text, cc=cc, attach=files)
        self._r.ok(f"已发出（{address} → {', '.join(rcpts)}）")
        return 0

    @_cmd
    def inbox(self, limit: int = 20, folder: str = "INBOX", unread: bool = False,
              email: str = "") -> int:
        """列最近的邮件（最新在前），不会把邮件标成已读。

        用法: email inbox
              email inbox --limit 50 --unread
              email inbox --folder 'Sent Messages'
        """
        from lib.email import inbox as do_inbox

        address, profile = resolve_profile(load_config(), email)
        rows = do_inbox(address, profile, folder=folder, limit=limit, unread_only=unread)
        return self._print_rows(address, folder, rows, "这个文件夹里没有邮件")

    @_cmd
    def search(self, query: str, limit: int = 20, folder: str = "INBOX", email: str = "") -> int:
        """全文搜索（正文和头都搜）。

        用法: email search 发票
              email search invoice --limit 50
        """
        from lib.email import search as do_search

        address, profile = resolve_profile(load_config(), email)
        rows = do_search(address, profile, query, folder=folder, limit=limit)
        return self._print_rows(address, folder, rows, f"没搜到含「{query}」的邮件")

    def _print_rows(self, address: str, folder: str, rows: list[dict], empty_msg: str) -> int:
        from rich.table import Table

        if not rows:
            self._r.info(empty_msg)
            return 0
        table = Table(title=f"{address} · {folder}（{len(rows)} 封）")
        table.add_column("UID", style="bold")
        table.add_column("")
        table.add_column("发件人")
        table.add_column("主题")
        table.add_column("时间")
        for r in rows:
            table.add_row(r["uid"], "●" if r["unread"] else "",
                          _short(r["From"], 30), _short(r["Subject"], 50), _short(r["Date"], 31))
        self._r.console.print(table)
        self._r.info("● = 未读 · 读正文: email read <UID>")
        return 0

    @_cmd
    def read(self, uid: str, folder: str = "INBOX", email: str = "") -> int:
        """读一封邮件的正文。

        用法: email read 12345
        """
        from lib.email import read as do_read

        address, profile = resolve_profile(load_config(), email)
        m = do_read(address, profile, str(uid), folder=folder)
        self._r.kv(f"UID {m['uid']}", {
            "发件人": m["From"], "收件人": m["To"], "主题": m["Subject"], "时间": m["Date"],
            "附件": ", ".join(m["attachments"]) or "(无)",
        })
        self._r.console.print(m["body"] or "(空正文)")
        if m["attachments"]:
            self._r.info(f"下载附件: email attach {m['uid']}")
        return 0

    @_cmd
    def attach(self, uid: str, out: str = ".", folder: str = "INBOX", email: str = "") -> int:
        """把一封邮件的附件下载下来。

        用法: email attach 12345
              email attach 12345 --out ~/Downloads
        """
        from lib.email import save_attachments

        address, profile = resolve_profile(load_config(), email)
        written = save_attachments(address, profile, str(uid), pathlib.Path(out), folder=folder)
        if not written:
            self._r.info(f"UID {uid} 没有附件")
            return 0
        for p in written:
            self._r.ok(f"已存: {p}")
        return 0

    @_cmd
    def mark(self, uid: str, unread: bool = False, folder: str = "INBOX", email: str = "") -> int:
        """标已读；加 --unread 标回未读。

        用法: email mark 12345
              email mark 12345 --unread
        """
        from lib.email import mark as do_mark

        address, profile = resolve_profile(load_config(), email)
        do_mark(address, profile, str(uid), seen=not unread, folder=folder)
        self._r.ok(f"UID {uid} 已标为{'未读' if unread else '已读'}")
        return 0


def _short(text: str, width: int) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= width else s[: width - 1] + "…"


def main():
    if len(sys.argv) <= 1:
        sys.argv.append("--skills")
    run_cli(EmailCli())
