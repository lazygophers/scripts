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

CONFIG_PATH = pathlib.Path.home() / ".config" / "lazygophers" / "scripts" / "ovpn.yaml"

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

def load_config(path: pathlib.Path = CONFIG_PATH) -> dict:
    """读 YAML 配置；文件不存在或为空 → 返回空 dict。"""
    import yaml

    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def save_config(data: dict, path: pathlib.Path = CONFIG_PATH) -> None:
    """写 YAML 配置，权限 0600（里面有明文密码和 TOTP 密钥）。"""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    # 先建 0600 再写，避免密码在 umask 宽松时短暂可读
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


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
    procs = running_processes()
    if not procs:
        reporter.info("没有正在运行的 openvpn 连接")
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
            reporter.ok("已断开")
            return 0
    left = [str(pid) for pid, _ in running_processes()]
    reporter.warn(f"进程未在 3 秒内退出，改用 SIGKILL: {', '.join(left)}")
    subprocess.run(["sudo", "kill", "-KILL", *left])
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


def connect(cfg: dict, reporter, *, verbose: bool = False) -> int:
    """启动 openvpn 并通过 management interface 自动应答账号密码 / 二步验证码。

    返回 openvpn 的退出码。前台运行，Ctrl-C 断开。
    """
    ovpn_bin = ensure_openvpn(reporter)
    if not ovpn_bin:
        return 127

    profile = str(cfg.get("config") or "").strip()
    profile_path = pathlib.Path(profile).expanduser()
    if not profile or not profile_path.is_file():
        reporter.err(f"配置里的 .ovpn 文件不存在: {profile or '(空)'}；跑 `openvpn login` 重填")
        return 2

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

    cmd = [
        "sudo", ovpn_bin,
        "--config", str(profile_path),
        "--cd", str(profile_path.parent),
        "--management", "127.0.0.1", str(port), str(pw_file),
        "--management-hold",
        "--management-query-passwords",
        "--auth-nocache",
        "--auth-retry", "interact",
        "--verb", "3" if verbose else "1",
        *extra,
    ]

    reporter.step(f"启动 openvpn: {profile_path}")
    reporter.info("需要 sudo 权限创建 tun 设备并改路由，可能会提示输入本机密码")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    import threading

    def _pump_log() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                reporter.output(line, prefix="  ovpn | ")

    threading.Thread(target=_pump_log, daemon=True).start()

    mgmt: ManagementClient | None = None
    for _ in range(50):  # 最多等 5 秒让 management 端口起来
        if proc.poll() is not None:
            reporter.err(f"openvpn 提前退出 (exit={proc.returncode})")
            return proc.returncode or 1
        try:
            mgmt = ManagementClient("127.0.0.1", port, mgmt_pw)
            break
        except OSError:
            time.sleep(0.1)
    if mgmt is None:
        reporter.err("连不上 management 接口，放弃")
        proc.terminate()
        return 1

    try:
        return _drive(mgmt, proc, reporter, username=username, password=password,
                      secret=secret, verbose=verbose)
    finally:
        mgmt.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _drive(mgmt: ManagementClient, proc, reporter, *, username: str, password: str,
           secret: str, verbose: bool) -> int:
    """management 主循环：应答挑战 + 打印状态，直到 openvpn 退出。"""
    mgmt.authenticate()
    mgmt.send("state on")
    mgmt.send("hold release")

    crv_state: str | None = None
    connected = False

    while True:
        if proc.poll() is not None and not connected:
            return proc.returncode or 1
        try:
            line = mgmt.readline(timeout=1.0)
        except socket.timeout:
            continue
        except OSError:
            break
        if line is None:
            break
        if verbose and line.startswith(">"):
            reporter.output(line, prefix="  mgmt | ")

        need_auth, sc_flags = parse_need_auth(line)
        if need_auth:
            otp = totp(secret) if secret else None
            if crv_state:
                reporter.step("服务端下发动态挑战，回填二步验证码")
            elif sc_flags is not None:
                reporter.step("服务端要求 static challenge，回填密码 + 二步验证码")
            else:
                reporter.step("回填用户名 / 密码")
            try:
                reply = build_password_reply(password, otp=otp, sc_flags=sc_flags,
                                             crv_state=crv_state)
            except ValueError as e:
                reporter.err(str(e))
                return 2
            mgmt.send(f"username {_quote('Auth')} {_quote(username)}")
            mgmt.send(f"password {_quote('Auth')} {_quote(reply)}")
            crv_state = None
            continue

        state = parse_dynamic_challenge(line)
        if state:
            crv_state = state
            reporter.warn("首轮认证被拒，服务端进入动态挑战，准备回填验证码")
            continue

        if line.startswith(">PASSWORD:Verification Failed"):
            reporter.err("认证失败：用户名 / 密码 / 二步验证码不对。跑 `openvpn login` 重填")
            return 2

        if line.startswith(">STATE:"):
            fields = line[len(">STATE:"):].split(",")
            name = fields[1] if len(fields) > 1 else "?"
            detail = fields[2] if len(fields) > 2 else ""
            if name == "CONNECTED":
                connected = True
                local_ip = fields[3] if len(fields) > 3 else ""
                remote = fields[4] if len(fields) > 4 else ""
                reporter.ok(f"已连接  本地 IP {local_ip or '(无)'}  服务端 {remote or '(无)'}")
                reporter.info("Ctrl-C 断开")
            elif name == "EXITING":
                reporter.warn(f"连接退出: {detail}")
            else:
                reporter.step(f"状态 {name}{(' · ' + detail) if detail else ''}")

    proc.wait()
    return proc.returncode or 0
