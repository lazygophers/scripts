"""ovpn — 用 management interface 驱动 OpenVPN CLI，自动填账号密码与二步验证码。

原理（openvpn doc/management-notes.txt）：
  openvpn --management 127.0.0.1 <port> <pwfile> --management-hold
          --management-query-passwords
  启动后在 hold 状态等待；本脚本连上 management TCP，`hold release` 放行，
  之后监听实时消息：
    >PASSWORD:Need 'Auth' username/password              → 普通账号密码
    >PASSWORD:Need 'Auth' username/password SC:<flag>,<文本>  → static challenge
    >PASSWORD:Verification Failed: 'Auth' ['CRV1:...']   → dynamic challenge
  回复：
    username "Auth" <user>
    password "Auth" <secret>
  二步验证码由本地 TOTP（RFC 6238）算出，密钥存在 config 文件里。

TOTP 用标准库实现（hmac + base64 + struct），不引入 pyotp 依赖。
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse


def real_home() -> pathlib.Path:
    """sudo 下 `~` 会变成 /var/root，配置得留在真实用户的家目录里。"""
    user = os.environ.get("SUDO_USER")
    if user:
        import pwd

        try:
            return pathlib.Path(pwd.getpwnam(user).pw_dir)
        except KeyError:
            pass
    return pathlib.Path.home()


CONFIG_PATH = real_home() / ".config" / "lazygophers" / "scripts" / "ovpn.yaml"

# brew / 系统常见安装位置（PATH 里没有时兜底）
_EXTRA_BIN_DIRS = (
    "/opt/homebrew/sbin",
    "/opt/homebrew/bin",
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/sbin",
)


# ---------------------------------------------------------------- config

def is_root() -> bool:
    return os.geteuid() == 0


class NeedRoot(Exception):
    """配置文件是 root:0600，普通用户读不到也写不了。"""


def require_root(script: pathlib.Path, argv: list[str]) -> None:
    """碰配置的命令的门槛：不是 root 就用 sudo 原样重跑自己（execvp，不返回）。

    解释器写成绝对路径（sys.executable），避免 sudo 的 PATH 里没有 mise/venv 的
    python；配置路径不用传，`real_home()` 会按 `SUDO_USER` 回落到真实家目录。
    """
    if is_root():
        return
    cmd = ["sudo", sys.executable, str(script), *argv]
    try:
        os.execvp("sudo", cmd)
    except OSError as e:
        raise NeedRoot(f"这条命令需要 root，但起不了 sudo: {e}") from e


def secure_config(path: pathlib.Path = CONFIG_PATH) -> bool:
    """把旧的用户属主配置收归 root:0600。只有 root 跑得动，改动了返回 True。"""
    if not is_root() or not path.exists():
        return False
    st = path.stat()
    if st.st_uid == 0 and (st.st_mode & 0o777) == 0o600:
        return False
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)
    return True


def load_config(path: pathlib.Path = CONFIG_PATH) -> dict:
    """读 YAML 配置；文件不存在或为空 → 返回空 dict。

    文件属主是 root、权限 0600，所以非 root 读会拿到 PermissionError，
    转成 NeedRoot 让调用方提示「加 sudo」。
    """
    import yaml

    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except PermissionError as e:
        raise NeedRoot(f"读不了 {path}（只有 root 能读），命令前面加 sudo") from e
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def save_config(data: dict, path: pathlib.Path = CONFIG_PATH) -> None:
    """写 YAML 配置：属主 root、权限 0600（里面有明文密码和 TOTP 密钥）。"""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    # 先建 0600 再写，避免密码在 umask 宽松时短暂可读
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    except PermissionError as e:
        raise NeedRoot(f"写不了 {path}（只有 root 能写），命令前面加 sudo") from e
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)
    if is_root():
        os.chown(path, 0, 0)  # 已经存在的旧文件也顺手收归 root


# ---------------------------------------------------------------- TOTP

def normalize_secret(raw: str) -> str:
    """把用户粘的密钥归一成 base32 字符串。

    支持 otpauth://totp/...?secret=XXX 形式，也支持带空格/小写的裸密钥。
    """
    s = (raw or "").strip()
    if s.lower().startswith("otpauth://"):
        query = urllib.parse.urlparse(s).query
        got = urllib.parse.parse_qs(query).get("secret", [""])[0]
        s = got
    return re.sub(r"[\s-]", "", s).upper()


def totp_counter(at: float | None = None, *, period: int = 30) -> int:
    """当前 TOTP 时间窗序号。同一个窗内算出来的验证码是同一个。"""
    return int((time.time() if at is None else at) // period)


def wait_fresh_totp(last_counter: int | None, reporter=None, *, period: int = 30) -> None:
    """上一个验证码若还在同一时间窗内，等到下一个窗再返回。

    服务端通常拒绝重复使用的 TOTP，断线重连时马上重试会直接被打回。
    """
    if last_counter is None or totp_counter(period=period) != last_counter:
        return
    wait = period - (time.time() % period) + 0.5
    if reporter:
        reporter.info(f"上一个验证码还在有效期内，等 {wait:.0f}s 换新码再认证")
    time.sleep(wait)


def totp(secret: str, *, digits: int = 6, period: int = 30, at: float | None = None) -> str:
    """RFC 6238 TOTP（HMAC-SHA1，默认 6 位 / 30 秒）。"""
    key_b32 = normalize_secret(secret)
    pad = "=" * (-len(key_b32) % 8)
    try:
        key = base64.b32decode(key_b32 + pad, casefold=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"二步验证密钥不是合法 base32: {e}") from e
    counter = int((time.time() if at is None else at) // period)
    mac = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    off = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


# ---------------------------------------------------------------- binary

def find_openvpn() -> str | None:
    """找 openvpn 二进制：先 PATH，再 brew / 系统常见 sbin 目录（brew 的 sbin 常不在 PATH）。"""
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    dirs += list(_EXTRA_BIN_DIRS)
    for d in dirs:
        p = os.path.join(d, "openvpn")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def find_brew() -> str | None:
    """找 Homebrew 可执行文件（Apple Silicon / Intel / Linuxbrew 三个默认位置 + PATH）。"""
    for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew",
              "/home/linuxbrew/.linuxbrew/bin/brew"):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return shutil.which("brew")


def ensure_openvpn(reporter) -> str | None:
    """返回 openvpn 二进制路径；没有就用 brew 自动装一次，装不了返回 None。"""
    found = find_openvpn()
    if found:
        return found

    brew = find_brew()
    if not brew:
        reporter.err("找不到 openvpn 二进制，也没有 Homebrew，无法自动安装")
        reporter.info("装 Homebrew: https://brew.sh  然后跑 brew install openvpn")
        return None

    reporter.warn("没有 openvpn 二进制，自动安装中：brew install openvpn")
    rc = subprocess.run([brew, "install", "openvpn"]).returncode
    if rc != 0:
        reporter.err(f"brew install openvpn 失败 (exit={rc})，手动装完再跑一次")
        return None

    found = find_openvpn()
    if not found:
        reporter.err("brew 报告安装成功，但仍然找不到 openvpn 二进制")
        reporter.info(f"检查一下: {brew} --prefix openvpn")
        return None
    reporter.ok(f"已安装: {found}")
    return found


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- 运行态

def running_processes() -> list[tuple[int, str]]:
    """列出正在跑的 openvpn 进程 → [(pid, 完整命令行)]。

    只认命令行里带 `--config` 的 openvpn 进程，避免误伤 OpenVPN Connect.app
    的 ovpnagent / OVPNHelper 常驻守护进程。
    """
    out = subprocess.run(["ps", "-Ao", "pid=,command="], capture_output=True, text=True)
    found = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition(" ")
        if not pid_s.isdigit():
            continue
        exe = cmd.split(" ", 1)[0]
        if os.path.basename(exe) != "openvpn" or "--config" not in cmd:
            continue
        found.append((int(pid_s), cmd))
    return found


def tun_interfaces() -> list[tuple[str, str]]:
    """列出已配置 IPv4 的 utun 网卡 → [(接口名, IP)]。"""
    out = subprocess.run(["ifconfig"], capture_output=True, text=True)
    result = []
    current = ""
    for line in out.stdout.splitlines():
        if line and not line[0].isspace():
            current = line.split(":", 1)[0]
            continue
        if current.startswith("utun") and line.strip().startswith("inet "):
            result.append((current, line.split()[1]))
    return result


def disconnect(reporter) -> int:
    """终止所有 openvpn 连接进程（SIGTERM，必要时 SIGKILL）。需要 sudo。"""
    from lib.ovpn_split import clean_resolver_files

    procs = running_processes()
    if not procs:
        reporter.info("没有正在运行的 openvpn 连接")
        # 进程没了但 resolver 文件可能还在（上次被 kill -9），一并收干净
        clean_resolver_files(reporter)
        return 0
    pids = [str(pid) for pid, _ in procs]
    for pid, cmd in procs:
        reporter.step(f"终止 pid {pid}: {cmd[:100]}")
    rc = subprocess.run(["sudo", "kill", "-TERM", *pids]).returncode
    if rc != 0:
        reporter.err(f"kill -TERM 失败 (exit={rc})")
        return rc
    for _ in range(30):  # 最多等 3 秒退干净
        time.sleep(0.1)
        if not running_processes():
            clean_resolver_files(reporter)
            reporter.ok("已断开")
            return 0
    left = [str(pid) for pid, _ in running_processes()]
    reporter.warn(f"进程未在 3 秒内退出，改用 SIGKILL: {', '.join(left)}")
    subprocess.run(["sudo", "kill", "-KILL", *left])
    clean_resolver_files(reporter)
    reporter.ok("已断开")
    return 0


# ---------------------------------------------------------------- protocol helpers

def _quote(value: str) -> str:
    """management 命令参数转义：规则同 openvpn 配置文件（\\ 和 " 需转义）。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def build_password_reply(password: str, *, otp: str | None, sc_flags: int | None,
                         crv_state: str | None) -> str:
    """按当前挑战类型拼出 `password "Auth" <x>` 里的 <x>（未加引号）。

    - dynamic challenge（CRV1）：CRV1::<state_id>::<otp>
    - static challenge FORMAT=1（flags & 0x2）：<password><otp> 直接拼接
    - static challenge FORMAT=0：SCRV1:<b64(password)>:<b64(otp)>
    - 无挑战：原样密码
    """
    if crv_state:
        return f"CRV1::{crv_state}::{otp or ''}"
    if sc_flags is not None:
        if not otp:
            raise ValueError("服务端要求 static challenge，但配置里没有二步验证密钥")
        if sc_flags & 0x2:
            return f"{password}{otp}"
        return f"SCRV1:{_b64(password)}:{_b64(otp)}"
    return password


_SC_RE = re.compile(r"SC:(\d+),")
_CRV_RE = re.compile(r"CRV1:([^:]*):([^:]*):([^:]*):(.*?)'?\]?$")


def parse_need_auth(line: str) -> tuple[bool, int | None]:
    """解析 `>PASSWORD:Need 'Auth' username/password[ SC:flags,text]`。

    返回 (是否命中, static challenge flags 或 None)。
    """
    if "Need 'Auth' username/password" not in line:
        return False, None
    m = _SC_RE.search(line)
    return True, int(m.group(1)) if m else None


def parse_dynamic_challenge(line: str) -> str | None:
    """从 `>PASSWORD:Verification Failed: 'Auth' ['CRV1:flags:state:user:text']` 取 state_id。"""
    if "Verification Failed" not in line or "CRV1:" not in line:
        return None
    m = _CRV_RE.search(line)
    return m.group(2) if m else None


_LOG_KEEP = ("ERROR", "WARNING", "FATAL", "Cannot", "failed", "Initialization Sequence Completed")

_OPENVPN_HINTS = (
    ("Initialization Sequence Completed", "VPN 已经连通；现在可以访问需要 VPN 的内网站点"),
    ("AUTH_FAILED", "VPN 认证被拒；跑 `sudo ovpn login` 重新填写用户名、密码和二步验证密钥"),
    ("TLS Error", "VPN 握手超时；检查网络是否能访问 VPN 服务器，或换个网络再试"),
    ("Cannot resolve host address", "VPN 服务器域名解析失败；检查本机 DNS 或网络连接"),
    ("Connection refused", "VPN 服务器拒绝连接；检查 .ovpn 文件里的服务器地址和端口是否正确"),
    ("Network is unreachable", "本机网络不可达；先确认 Wi-Fi / 有线网络能上网"),
    ("Options error", "OpenVPN 配置文件格式有问题；检查 .ovpn 文件，或跑 `sudo ovpn login` 重新选择"),
)


def explain_openvpn_log(line: str) -> str:
    """把常见 openvpn 原始日志翻译成用户能直接行动的提示。"""
    for needle, hint in _OPENVPN_HINTS:
        if needle in line:
            return f"{hint}｜原始日志: {line}"
    return line


_PUSH_DNS_RE = re.compile(r"dhcp-option\s+DNS6?\s+([0-9a-fA-F:.]+)")


def parse_pushed_dns(line: str) -> list[str]:
    """从 openvpn 日志里的 PUSH_REPLY 取服务端下发的 DNS。

    日志形如 `PUSH: Received control message: 'PUSH_REPLY,dhcp-option DNS 10.8.0.1,...'`
    （src/openvpn/push.c，msg 等级 D_PUSH = verb 3），所以分流模式下 verb 至少要 3。
    """
    if "PUSH_REPLY" not in line:
        return []
    out = []
    for ip in _PUSH_DNS_RE.findall(line):
        if ip not in out:
            out.append(ip)
    return out


# ---------------------------------------------------------------- connect

class ManagementClient:
    """OpenVPN management interface 的行协议客户端（TCP，行以 \\r\\n 结尾）。"""

    def __init__(self, host: str, port: int, password: str) -> None:
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(None)
        self._buf = b""
        self._password = password

    def send(self, line: str) -> None:
        self.sock.sendall(line.encode("utf-8") + b"\n")

    def readline(self, timeout: float | None = None) -> str | None:
        """读一行（去掉行尾 \\r\\n）；连接关闭返回 None，超时抛 socket.timeout。"""
        while b"\n" not in self._buf:
            self.sock.settimeout(timeout)
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", "replace").rstrip("\r")

    def authenticate(self) -> None:
        """management 口令认证：收到 `ENTER PASSWORD:` 后发口令。"""
        self.sock.settimeout(5)
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return
        self._buf += data
        if b"ENTER PASSWORD" in self._buf:
            self._buf = b""
            self.send(self._password)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


_KEEPALIVE_DIRECTIVES = ("keepalive", "ping", "ping-restart", "ping-exit")


def _needs_keepalive(profile_path: pathlib.Path) -> bool:
    """profile 里没写任何 keepalive / ping 指令时返回 True（我们补一组默认值）。

    服务端 push 的 keepalive 优先级高于客户端本地设置，所以补上是安全的：
    只在服务端也没管的情况下才生效，用来兜住「链路死了但进程还在」的场景。
    """
    try:
        text = profile_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for raw in text.splitlines():
        head = raw.strip().split("#", 1)[0].split(";", 1)[0].split()
        if head and head[0] in _KEEPALIVE_DIRECTIVES:
            return False
    return True


def connect(cfg: dict, reporter, *, verbose: bool = False,
            reconnect: bool | None = None, reconnect_max: int | None = None) -> int:
    """连接 VPN，断线自动重连。

    两层重连：
      1. openvpn 自身的 SIGUSR1 重启 —— management 循环一直活着，重新回填凭据；
      2. openvpn 进程整个挂掉 —— 本函数重新拉起，退避 5s 起步、翻倍、上限 60s。
    连上一次之后退避计时重置。Ctrl-C 或认证失败（凭据错）不重连。

    reconnect / reconnect_max 传 None 时读配置的 `auto_reconnect`（默认开）和
    `reconnect_max`（默认 0 = 不限次数）。
    """
    if reconnect is None:
        reconnect = bool(cfg.get("auto_reconnect", True))
    if reconnect_max is None:
        reconnect_max = int(cfg.get("reconnect_max", 0) or 0)

    attempt = 0
    delay = 5.0
    last_counter: int | None = None
    while True:
        rc, connected, last_counter = _connect_once(
            cfg, reporter, verbose=verbose, last_otp_counter=last_counter)
        if rc in (0, 2, 127, 130):
            # 0=正常退出 2=凭据/配置错 127=没有二进制 130=Ctrl-C：重连没有意义
            return rc
        if not reconnect:
            return rc
        attempt = 0 if connected else attempt + 1
        delay = 5.0 if connected else min(delay * 2, 60.0)
        if reconnect_max and attempt > reconnect_max:
            reporter.err(f"VPN 已连续重连 {reconnect_max} 次都失败，已停止。下一步: 跑 `ovpn connect --verbose` 看完整 OpenVPN 原始日志")
            return rc
        reporter.warn(f"VPN 连接断开，{delay:.0f} 秒后自动重试第 {attempt or 1} 次。按 Ctrl-C 可停止")
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 130


def _connect_once(cfg: dict, reporter, *, verbose: bool = False,
                  last_otp_counter: int | None = None) -> tuple[int, bool, int | None]:
    """跑一次 openvpn 直到它退出。

    返回 (退出码, 本次是否连上过, 最后一次用掉的 TOTP 时间窗序号)。
    """
    ovpn_bin = ensure_openvpn(reporter)
    if not ovpn_bin:
        return 127, False, last_otp_counter

    profile = str(cfg.get("config") or "").strip()
    profile_path = pathlib.Path(profile).expanduser()
    if not profile or not profile_path.is_file():
        reporter.err(f"找不到 VPN 配置文件: {profile or '(空)'}。下一步: 跑 `sudo ovpn login` 重新选择 .ovpn 文件")
        return 2, False, last_otp_counter

    username = str(cfg.get("username") or "")
    password = str(cfg.get("password") or "")
    secret = str(cfg.get("totp_secret") or "")
    extra = [str(x) for x in (cfg.get("extra_args") or [])]

    port = _free_port()
    mgmt_pw = base64.urlsafe_b64encode(os.urandom(18)).decode("ascii")

    tmp_dir = tempfile.mkdtemp(prefix="ovpn-mgmt-")
    os.chmod(tmp_dir, 0o700)
    pw_file = pathlib.Path(tmp_dir) / "mgmt.pw"
    fd = os.open(str(pw_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(mgmt_pw + "\n")
    # 保持 0600：openvpn 以 root 跑，root 读取不受权限限制

    from lib.ovpn_split import SplitTunnel, clean_resolver_files

    split = SplitTunnel(cfg, reporter)
    use_split = split.enabled and bool(cfg.get("split_tunnel", True))
    if use_split:
        # 上次被 kill -9 可能留下 resolver 文件，指向一个已经没人监听的端口，
        # 那些域名会解析失败 —— 起连接前先清干净
        clean_resolver_files(reporter)

    keepalive = ["--ping", "10", "--ping-restart", "60"] if _needs_keepalive(profile_path) else []
    cmd = [
        "sudo", ovpn_bin,
        "--config", str(profile_path),
        "--cd", str(profile_path.parent),
        "--management", "127.0.0.1", str(port), str(pw_file),
        "--management-hold",
        "--management-query-passwords",
        "--auth-nocache",
        "--auth-retry", "interact",
        "--connect-retry", "5",
        # verb 3 才会打印 PUSH_REPLY（D_PUSH），分流要靠它拿服务端下发的 DNS
        "--verb", "3" if (verbose or use_split) else "1",
        # 分流模式下不接服务端 push 的任何路由，默认出口留给本地网络
        *(["--route-nopull"] if use_split else []),
        *keepalive,
        *extra,
    ]

    reporter.step(f"正在启动 VPN: {profile_path}")
    reporter.info("系统会要求输入本机密码；这是为了创建 VPN 网卡和写入路由")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    import threading

    quiet_log = use_split and not verbose

    def _pump_log() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            if use_split and "PUSH_REPLY" in line:
                pushed = parse_pushed_dns(line)
                if pushed:
                    kept = split.note_pushed_dns(pushed)
                    if kept:
                        reporter.info(f"VPN 下发了内网 DNS: {', '.join(kept)}。分流域名会先问这些 DNS")
                    else:
                        reporter.info("VPN 下发的是公网 DNS，已改用默认 DNS: AdGuard、Cloudflare、阿里、腾讯")
            if quiet_log and not any(k in line for k in _LOG_KEEP):
                continue  # 分流把 verb 提到了 3，非 verbose 时只留关键行，别刷屏
            reporter.output(explain_openvpn_log(line), prefix="  openvpn | ")

    threading.Thread(target=_pump_log, daemon=True).start()

    mgmt: ManagementClient | None = None
    try:
        for _ in range(50):  # 最多等 5 秒让 management 端口起来
            if proc.poll() is not None:
                reporter.err(f"OpenVPN 启动后立刻退出（退出码 {proc.returncode}）。下一步: 跑 `ovpn connect --verbose` 看上方原始日志")
                return proc.returncode or 1, False, last_otp_counter
            try:
                mgmt = ManagementClient("127.0.0.1", port, mgmt_pw)
                break
            except OSError:
                time.sleep(0.1)
        if mgmt is None:
            reporter.err("脚本连不上 OpenVPN 管理接口，无法自动填写账号密码。下一步: 跑 `ovpn connect --verbose` 看 OpenVPN 是否正常启动")
            proc.terminate()
            return 1, False, last_otp_counter

        return _drive(mgmt, proc, reporter, username=username, password=password,
                      secret=secret, verbose=verbose, last_otp_counter=last_otp_counter,
                      split=split if use_split else None)
    finally:
        if use_split:
            split.stop()
        if mgmt is not None:
            mgmt.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _drive(mgmt: ManagementClient, proc, reporter, *, username: str, password: str,
           secret: str, verbose: bool,
           last_otp_counter: int | None = None,
           split=None) -> tuple[int, bool, int | None]:
    """management 主循环：应答挑战 + 打印状态，直到 openvpn 退出。

    openvpn 自己发起的 SIGUSR1 重启不会退出进程，只是重新走一遍 AUTH，
    所以这个循环一直活着就等于「断线自动重连」的第一层。
    """
    mgmt.authenticate()
    mgmt.send("state on")
    mgmt.send("hold release")

    crv_state: str | None = None
    connected_ever = False
    reconnecting = False

    while True:
        if proc.poll() is not None:
            return proc.returncode or 1, connected_ever, last_otp_counter
        try:
            line = mgmt.readline(timeout=1.0)
        except socket.timeout:
            continue
        except OSError:
            return (1 if reconnecting else 0), connected_ever, last_otp_counter
        if line is None:
            return (1 if reconnecting else 0), connected_ever, last_otp_counter
        if verbose and line.startswith(">"):
            reporter.output(line, prefix="  mgmt | ")

        need_auth, sc_flags = parse_need_auth(line)
        if need_auth:
            otp = None
            if secret:
                wait_fresh_totp(last_otp_counter, reporter)
                last_otp_counter = totp_counter()
                otp = totp(secret)
            if crv_state:
                reporter.step("VPN 要求二步验证码，正在自动填写")
            elif sc_flags is not None:
                reporter.step("VPN 要求密码和二步验证码，正在自动填写")
            else:
                reporter.step("VPN 要求用户名和密码，正在自动填写")
            try:
                reply = build_password_reply(password, otp=otp, sc_flags=sc_flags,
                                             crv_state=crv_state)
            except ValueError as e:
                reporter.err(str(e))
                return 2, connected_ever, last_otp_counter
            mgmt.send(f"username {_quote('Auth')} {_quote(username)}")
            mgmt.send(f"password {_quote('Auth')} {_quote(reply)}")
            crv_state = None
            continue

        state = parse_dynamic_challenge(line)
        if state:
            crv_state = state
            reporter.warn("第一次认证被拒，VPN 又要求一次二步验证码，正在自动填写")
            continue

        if line.startswith(">PASSWORD:Verification Failed"):
            reporter.err("VPN 认证失败：用户名、密码或二步验证码不对。下一步: 跑 `sudo ovpn login` 重新填写")
            return 2, connected_ever, last_otp_counter

        if line.startswith(">STATE:"):
            fields = line[len(">STATE:"):].split(",")
            name = fields[1] if len(fields) > 1 else "?"
            detail = fields[2] if len(fields) > 2 else ""
            if name == "CONNECTED":
                reconnecting = False
                connected_ever = True
                local_ip = fields[3] if len(fields) > 3 else ""
                remote = fields[4] if len(fields) > 4 else ""
                reporter.ok(f"VPN 已连接。本机 VPN IP: {local_ip or '(未显示)'}；VPN 服务器: {remote or '(未显示)'}")
                if split is not None:
                    # 分流规则依赖 tun 网卡，必须等 CONNECTED 拿到本地 IP 才能定位网卡
                    split.start(local_ip)
                reporter.info("保持这个窗口开着。要断开 VPN，按 Ctrl-C")
            elif name == "RECONNECTING":
                if connected_ever:
                    reconnecting = True
                reporter.warn(f"VPN 网络断了一下，OpenVPN 正在自动重连。原因: {detail or '没有给出原因'}")
                if connected_ever:
                    reporter.info("OpenVPN 进程还在，等它自己恢复。恢复后这里会继续出日志")
            elif name == "EXITING":
                reporter.warn(f"VPN 正在退出。原因: {detail or '没有给出原因'}")
            else:
                reporter.step(f"VPN 状态: {name}{('；详情: ' + detail) if detail else ''}")

    proc.wait()
    return proc.returncode or 0, connected_ever, last_otp_counter
