"""email — 多邮箱收发的 IMAP/SMTP 客户端 + 每家一套的配置引导。

支持 QQ / Gmail / 163 / 126 / iCloud / Fastmail / Zoho。这七家**都走「应用专用
密码 / 授权码」**，没有一家需要 OAuth2（只有 Outlook.com 需要，不在支持列表里），
所以整个模块只用标准库 `imaplib` / `smtplib` / `email`，零第三方依赖。

配置存 `~/.config/lazygophers/scripts/email.yaml`（0600），按**邮箱地址**分 profile，
存储与解析共用 `lib/profile_store.py`（archery / grafana 同一份）：

    current: me@gmail.com
    profiles:
      me@gmail.com:
        provider: gmail
        imap: {host: imap.gmail.com, port: 993}
        smtp: {host: smtp.gmail.com, port: 465, mode: ssl}
        password: <应用专用密码>

模块名叫 `email` 不会遮蔽标准库的 `email`：Python 3 默认绝对导入，本文件只能以
`lib.email` 被导入，模块内的 `import email` 拿到的仍然是标准库那个。

各家的服务器地址与取授权码路径见 `docs/research/mail-tool.md`；其中 QQ 和 163/126
免费版的端口没能拿到现行官方原文（标了 `推测:`），所以配置引导里每一项都可以当场改。
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import mimetypes
import pathlib
import smtplib
import ssl
from email.message import EmailMessage

from lib.profile_store import ProfileStore

CONFIG_NAME = "email.yaml"
DEFAULT_TIMEOUT = 30

# 一次列多少封、搜索最多回多少封的默认值
DEFAULT_LIMIT = 20


class MailError(Exception):
    """连接 / 认证 / 配置失败。message 直接给用户看。"""


# ---------------------------------------------------------------- 服务商

# imap/smtp 的 mode: "ssl" = 一连上就加密（993/465），"starttls" = 明文连上再升级（587）。
# needs_id: 网易系登录后必须先发 IMAP ID 命令报出客户端身份，否则服务器回
#   "Unsafe Login. Please contact kefu@188.com" 直接踢掉（标准库 imaplib 没有
#   id() 方法，见 _imap_id）。
PROVIDERS: dict[str, dict] = {
    "gmail": {
        "label": "Gmail / Google Workspace",
        "domains": ("gmail.com", "googlemail.com"),
        "imap": {"host": "imap.gmail.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.gmail.com", "port": 465, "mode": "ssl"},
        "guide": [
            "必须先给 Google 账号开启「两步验证」，没开的话根本没有应用专用密码这个入口",
            "打开 https://myaccount.google.com/apppasswords",
            "起个名字（比如 lazyscripts），生成后得到 16 位密码",
            "把这 16 位填到下面（空格可去可留，程序会自动去掉）",
        ],
        "warn": "Google 一直在收紧这条路。哪天认证失败了，先去上面那个页面确认应用专用密码还在。",
    },
    "qq": {
        "label": "QQ 邮箱",
        "domains": ("qq.com", "vip.qq.com", "foxmail.com"),
        "imap": {"host": "imap.qq.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.qq.com", "port": 465, "mode": "ssl"},
        "guide": [
            "网页版 QQ 邮箱 →【设置】→【账号与安全】→【安全设置】",
            "找到 IMAP/SMTP 服务，先点「开启服务」（要短信验证）",
            "开启后点「生成授权码」，得到 16 位授权码",
            "把授权码填到下面（不是你的 QQ 密码）",
        ],
        "warn": "改 QQ 密码会让授权码立刻失效，到时要回来重新生成。",
    },
    "163": {
        "label": "网易 163 邮箱",
        "domains": ("163.com",),
        "imap": {"host": "imap.163.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.163.com", "port": 465, "mode": "ssl"},
        "needs_id": True,
        "guide": [
            "登录 https://mail.163.com →「设置」→「POP3/SMTP/IMAP」",
            "开启 IMAP/SMTP 服务（要短信验证）",
            "新增「客户端授权密码」，把它填到下面",
        ],
        "warn": "授权密码在网页上只显示一次，当场没存就只能重新生成。",
    },
    "126": {
        "label": "网易 126 邮箱",
        "domains": ("126.com",),
        "imap": {"host": "imap.126.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.126.com", "port": 465, "mode": "ssl"},
        "needs_id": True,
        "guide": [
            "登录 https://mail.126.com →「设置」→「POP3/SMTP/IMAP」",
            "开启 IMAP/SMTP 服务（要短信验证）",
            "新增「客户端授权密码」，把它填到下面",
        ],
        "warn": "授权密码在网页上只显示一次，当场没存就只能重新生成。",
    },
    "icloud": {
        "label": "iCloud Mail",
        "domains": ("icloud.com", "me.com", "mac.com"),
        "imap": {"host": "imap.mail.me.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.mail.me.com", "port": 587, "mode": "starttls"},
        # iCloud 的 IMAP 用户名只要 @ 前面那截，SMTP 要完整地址（Apple 支持 102525）
        "imap_user": "localpart",
        "guide": [
            "打开 https://account.apple.com 登录",
            "在「登录与安全」里找到「App 专用密码」，生成一个",
            "把生成的密码填到下面（不是你的 Apple ID 密码）",
        ],
    },
    "fastmail": {
        "label": "Fastmail",
        "domains": ("fastmail.com", "fastmail.fm"),
        "imap": {"host": "imap.fastmail.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.fastmail.com", "port": 465, "mode": "ssl"},
        "guide": [
            "登录 Fastmail →  Settings → Privacy & Security → App Passwords",
            "新建一个 app password，勾上 IMAP/SMTP 权限",
            "把它填到下面",
        ],
        "warn": "Basic 套餐没有 IMAP/SMTP 权限，得先升级套餐，否则这里怎么填都连不上。",
    },
    "zoho": {
        "label": "Zoho Mail",
        "domains": ("zoho.com", "zohomail.com"),
        "imap": {"host": "imap.zoho.com", "port": 993, "mode": "ssl"},
        "smtp": {"host": "smtp.zoho.com", "port": 465, "mode": "ssl"},
        "guide": [
            "登录 Zoho Mail → 设置 → Mail Accounts，确认 IMAP Access 已开启",
            "没开两步验证的话可以直接用账号密码",
            "开了两步验证就要去 accounts.zoho.com 生成 application-specific password",
        ],
        "warn": "付费的组织域名用的是 imappro.zoho.com / smtppro.zoho.com，"
                "下一步会让你确认服务器地址，到时改掉即可。",
    },
}


def provider_for(address: str) -> str:
    """按邮箱域名猜服务商；猜不出返回空串（让用户自己选）。"""
    domain = address.rpartition("@")[2].strip().lower()
    for name, spec in PROVIDERS.items():
        if domain in spec["domains"]:
            return name
    return ""


def email_key(raw: str) -> str:
    """profile 的 key：小写、去空格的完整邮箱地址。"""
    s = (raw or "").strip().lower()
    if "@" not in s or s.startswith("@") or s.endswith("@"):
        raise MailError(f"看不懂的邮箱地址: {raw!r}")
    return s


def default_config_path() -> pathlib.Path:
    return pathlib.Path.home() / ".config" / "lazygophers" / "scripts" / CONFIG_NAME


_STORE = ProfileStore(
    CONFIG_NAME, error=MailError, key_fn=email_key, tool="email",
    noun="邮箱", key_flag="--email", login_flag="--email", key_hint="<邮箱地址>",
    path_resolver=default_config_path,
)
load_config = _STORE.load
save_config = _STORE.save
config_lock = _STORE.lock
profiles = _STORE.profiles
resolve_profile = _STORE.resolve
put_profile = _STORE.put


def build_profile(address: str, provider: str, password: str, *,
                  imap: dict | None = None, smtp: dict | None = None) -> dict:
    """把一次登录的输入拼成落盘的 profile。imap/smtp 不给就用服务商默认值。"""
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise MailError(f"不认识的服务商: {provider}（可选: {', '.join(PROVIDERS)}）")
    return {
        "provider": provider,
        "imap": dict(imap or spec["imap"]),
        "smtp": dict(smtp or spec["smtp"]),
        "password": password,
    }


# ---------------------------------------------------------------- 连接

def _ssl_context() -> ssl.SSLContext:
    """标准库的 IMAP4_SSL / SMTP_SSL 默认**不校验证书和主机名**（官方文档原文），
    等于加了密但不确认对面是谁。必须显式给一个会校验的 context。"""
    return ssl.create_default_context()


def _login_user(address: str, provider: str, kind: str) -> str:
    """登录用户名。默认就是完整邮箱地址；iCloud 的 IMAP 只认 @ 前面那截。"""
    spec = PROVIDERS.get(provider) or {}
    if kind == "imap" and spec.get("imap_user") == "localpart":
        return address.partition("@")[0]
    return address


def _imap_id(imap: imaplib.IMAP4) -> None:
    """给网易系报一下客户端身份。

    163/126 登录后不发 ID 就会回 "Unsafe Login. Please contact kefu@188.com"，
    随后所有命令都失败。标准库 imaplib 没有 id() 方法，只能走 _simple_command。
    失败不致命——真需要 ID 的服务器会在后续命令上明确报错。
    """
    try:
        imap._simple_command("ID", '("name" "lazyscripts" "version" "1.0")')
        imap._untagged_response("OK", [None], "ID")
    except Exception:  # noqa: BLE001
        pass


def connect_imap(address: str, profile: dict, *, timeout: int = DEFAULT_TIMEOUT) -> imaplib.IMAP4:
    """连上并登录 IMAP。调用方负责 logout()。"""
    conf = dict(profile.get("imap") or {})
    host, port = str(conf.get("host") or ""), int(conf.get("port") or 993)
    if not host:
        raise MailError(f"{address} 的配置里没有 IMAP 服务器地址。跑 `email login --email {address}`")
    try:
        if str(conf.get("mode") or "ssl") == "starttls":
            imap = imaplib.IMAP4(host, port, timeout=timeout)
            imap.starttls(ssl_context=_ssl_context())
        else:
            imap = imaplib.IMAP4_SSL(host, port, ssl_context=_ssl_context(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise MailError(f"连不上 IMAP {host}:{port}: {e}") from e
    provider = str(profile.get("provider") or "")
    try:
        imap.login(_login_user(address, provider, "imap"), str(profile.get("password") or ""))
    except Exception as e:  # noqa: BLE001
        with _quiet():
            imap.logout()
        raise MailError(f"IMAP 登录被拒（{host}）: {e}") from e
    if (PROVIDERS.get(provider) or {}).get("needs_id"):
        _imap_id(imap)
    return imap


def connect_smtp(address: str, profile: dict, *, timeout: int = DEFAULT_TIMEOUT) -> smtplib.SMTP:
    """连上并登录 SMTP。调用方负责 quit()。"""
    conf = dict(profile.get("smtp") or {})
    host, port = str(conf.get("host") or ""), int(conf.get("port") or 465)
    if not host:
        raise MailError(f"{address} 的配置里没有 SMTP 服务器地址。跑 `email login --email {address}`")
    try:
        if str(conf.get("mode") or "ssl") == "starttls":
            smtp: smtplib.SMTP = smtplib.SMTP(host, port, timeout=timeout)
            smtp.starttls(context=_ssl_context())
        else:
            smtp = smtplib.SMTP_SSL(host, port, context=_ssl_context(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        raise MailError(f"连不上 SMTP {host}:{port}: {e}") from e
    provider = str(profile.get("provider") or "")
    try:
        smtp.login(_login_user(address, provider, "smtp"), str(profile.get("password") or ""))
    except Exception as e:  # noqa: BLE001
        with _quiet():
            smtp.quit()
        raise MailError(f"SMTP 登录被拒（{host}）: {e}") from e
    return smtp


class _quiet:
    """收尾时的 close/logout 本身就可能再抛一次，不该盖掉真正的失败原因。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def check(address: str, profile: dict, *, timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """真连一次 IMAP 和 SMTP 并登录，两边都通才算通过。

    返回 (是否通过, 说明)。**保存配置前必须先过这一关**——不然存下来的是一份
    看着没问题、用起来全是错的配置。
    """
    try:
        imap = connect_imap(address, profile, timeout=timeout)
    except MailError as e:
        return False, str(e)
    with _quiet():
        imap.logout()
    try:
        smtp = connect_smtp(address, profile, timeout=timeout)
    except MailError as e:
        return False, str(e)
    with _quiet():
        smtp.quit()
    imap_conf, smtp_conf = profile.get("imap") or {}, profile.get("smtp") or {}
    return True, (f"IMAP {imap_conf.get('host')}:{imap_conf.get('port')} 和 "
                  f"SMTP {smtp_conf.get('host')}:{smtp_conf.get('port')} 都登录成功")


# ---------------------------------------------------------------- 发信

def send(address: str, profile: dict, *, to: str, subject: str, body: str,
         cc: str = "", attach: list[str] | None = None,
         timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    """发一封纯文本邮件，可带附件。返回实际投递的收件人列表。

    `to` / `cc` 支持逗号分隔多个地址。
    """
    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = _addr_list(to)
    if cc:
        msg["Cc"] = _addr_list(cc)
    msg["Subject"] = subject
    msg.set_content(body)
    for item in attach or []:
        _attach_file(msg, pathlib.Path(item).expanduser())

    smtp = connect_smtp(address, profile, timeout=timeout)
    try:
        smtp.send_message(msg)
    except Exception as e:  # noqa: BLE001
        raise MailError(f"发信失败: {e}") from e
    finally:
        with _quiet():
            smtp.quit()
    return [a.strip() for a in f"{to},{cc}".split(",") if a.strip()]


def _addr_list(raw: str) -> str:
    return ", ".join(a.strip() for a in (raw or "").split(",") if a.strip())


def _attach_file(msg: EmailMessage, path: pathlib.Path) -> None:
    if not path.is_file():
        raise MailError(f"附件不存在: {path}")
    ctype, _ = mimetypes.guess_type(str(path))
    maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
    msg.add_attachment(path.read_bytes(), maintype=maintype,
                       subtype=subtype or "octet-stream", filename=path.name)


# ---------------------------------------------------------------- 收信

def _select(imap: imaplib.IMAP4, folder: str, *, readonly: bool = True) -> None:
    typ, data = imap.select(f'"{folder}"', readonly=readonly)
    if typ != "OK":
        raise MailError(f"打不开文件夹 {folder!r}: {_text(data)}")


def _uids(imap: imaplib.IMAP4, criteria: list) -> list[bytes]:
    typ, data = imap.uid("SEARCH", *criteria)
    if typ != "OK":
        raise MailError(f"搜索失败: {_text(data)}")
    return (data[0] or b"").split()


def _fetch(imap: imaplib.IMAP4, uid: bytes, part: str) -> bytes:
    typ, data = imap.uid("FETCH", uid, part)
    if typ != "OK":
        raise MailError(f"取邮件 {uid.decode()} 失败: {_text(data)}")
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            return item[1]
    raise MailError(f"邮件 {uid.decode()} 没有内容返回")


def _text(data) -> str:
    parts = [d.decode(errors="replace") if isinstance(d, bytes) else str(d) for d in (data or [])]
    return " ".join(parts) or "(无返回)"


def _headers(raw: bytes) -> dict[str, str]:
    """解析邮件头。policy.default 会自动把 RFC 2047 编码过的中文头解成 str。"""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return {k: str(msg.get(k) or "") for k in ("From", "To", "Subject", "Date")}


def _listing(imap: imaplib.IMAP4, uids: list[bytes], limit: int) -> list[dict]:
    """取每封的头 + 已读标记。

    ponytail: 一封一个来回。批量 FETCH 要自己拆 imaplib 那串半解析响应，
    值不当这个复杂度；真慢到不能忍再改成一次 FETCH 多个 UID。
    """
    out: list[dict] = []
    for uid in reversed(uids[-limit:]):
        head = _fetch(imap, uid, "(BODY.PEEK[HEADER])")
        typ, flag_data = imap.uid("FETCH", uid, "(FLAGS)")
        flags = imaplib.ParseFlags(flag_data[0]) if typ == "OK" and flag_data and flag_data[0] else ()
        row = _headers(head)
        row["uid"] = uid.decode()
        row["unread"] = rb"\Seen" not in flags
        out.append(row)
    return out


def inbox(address: str, profile: dict, *, folder: str = "INBOX", limit: int = DEFAULT_LIMIT,
          unread_only: bool = False, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """列最近的邮件（最新在前）。用 BODY.PEEK 取头，不会把邮件标成已读。"""
    imap = connect_imap(address, profile, timeout=timeout)
    try:
        _select(imap, folder)
        return _listing(imap, _uids(imap, ["UNSEEN"] if unread_only else ["ALL"]), limit)
    finally:
        with _quiet():
            imap.logout()


def search(address: str, profile: dict, query: str, *, folder: str = "INBOX",
           limit: int = DEFAULT_LIMIT, timeout: int = DEFAULT_TIMEOUT) -> list[dict]:
    """全文搜索（IMAP TEXT，正文和头都搜）。中文按 UTF-8 走 literal 传，不会乱码。"""
    imap = connect_imap(address, profile, timeout=timeout)
    try:
        _select(imap, folder)
        return _listing(imap, _uids(imap, ["CHARSET", "UTF-8", "TEXT", query.encode()]), limit)
    finally:
        with _quiet():
            imap.logout()


def read(address: str, profile: dict, uid: str, *, folder: str = "INBOX",
         timeout: int = DEFAULT_TIMEOUT) -> dict:
    """读一封的正文。返回 头 + 正文 + 附件名清单。

    只用 `imaplib` 把整封原始字节拉下来，解析全交给 `email`——IMAP 里最难啃的
    BODYSTRUCTURE 就绕开了。代价是带大附件的邮件会整封下载。
    """
    imap = connect_imap(address, profile, timeout=timeout)
    try:
        _select(imap, folder)
        raw = _fetch(imap, uid.encode(), "(BODY.PEEK[])")
    finally:
        with _quiet():
            imap.logout()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    return {
        "uid": uid,
        "From": str(msg.get("From") or ""),
        "To": str(msg.get("To") or ""),
        "Subject": str(msg.get("Subject") or ""),
        "Date": str(msg.get("Date") or ""),
        "body": body.get_content() if body is not None else "",
        "attachments": [p.get_filename() or "(未命名)" for p in msg.iter_attachments()],
    }


def save_attachments(address: str, profile: dict, uid: str, out_dir: pathlib.Path, *,
                     folder: str = "INBOX", timeout: int = DEFAULT_TIMEOUT) -> list[pathlib.Path]:
    """把一封邮件的附件全部存到 out_dir，返回写出的文件路径。"""
    imap = connect_imap(address, profile, timeout=timeout)
    try:
        _select(imap, folder)
        raw = _fetch(imap, uid.encode(), "(BODY.PEEK[])")
    finally:
        with _quiet():
            imap.logout()
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    out_dir = pathlib.Path(out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[pathlib.Path] = []
    for i, part in enumerate(msg.iter_attachments(), 1):
        name = pathlib.Path(part.get_filename() or f"attachment-{i}").name
        target = out_dir / name
        target.write_bytes(part.get_payload(decode=True) or b"")
        written.append(target)
    return written


def mark(address: str, profile: dict, uid: str, *, seen: bool = True,
         folder: str = "INBOX", timeout: int = DEFAULT_TIMEOUT) -> None:
    """标已读 / 标未读。"""
    imap = connect_imap(address, profile, timeout=timeout)
    try:
        _select(imap, folder, readonly=False)
        typ, data = imap.uid("STORE", uid.encode(), "+FLAGS" if seen else "-FLAGS", r"(\Seen)")
        if typ != "OK":
            raise MailError(f"标记邮件 {uid} 失败: {_text(data)}")
    finally:
        with _quiet():
            imap.logout()
