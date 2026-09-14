"""给浏览器写企业策略，让自分发的 browse 扩展不被自动停用、并固定在工具栏上

**这个模块写的是系统级文件，要管理员密码。** 所以它把「算出要写什么」和「真的写下去」
分成两半：`plan()` 纯计算、可测试、没有副作用；`apply()` 才拿着 plan 去 sudo。
`browse install` 会先把 plan 原样打印给用户看，再要密码。

## 能做什么、不能做什么（先说不能）

| 想要的 | 策略字段 | 结论 |
|---|---|---|
| 不被自动停用 | `installation_mode: allowed` | **做了**，这是唯一不要求加域 / MDM 的那条路 |
| 固定在工具栏 | `toolbar_pin: force_pinned` | **做了** |
| 允许访问 `file://` | `file_url_navigation_allowed: true` | **做了** |
| 自动装上（不用人点） | `installation_mode: force_installed` | **做不了**，见下 |
| 无痕模式下可用 | —— | **做不了**：`ExtensionSettings` 里根本没有 `incognito` 字段 |

`force_installed` 做不了有两层原因，任意一层单独成立都够：

1. **schema 上过不去**：`force_installed` 的条目必须同时给 `update_url`，浏览器按那个
   地址去下载 CRX。我们没有发布过 CRX，也没有更新服务器 —— 而且扩展 ID 是由 CRX 的
   签名私钥算出来的，仓库里只有公钥（manifest 的 `key`），签不出 ID 对得上的 CRX。
2. **就算有 CRX 也不行**：Chrome 官方文档写明「On Windows and macOS, extensions from
   outside the Chrome Web Store require domain joining or enterprise enrollment」
   <https://developer.chrome.com/docs/extensions/how-to/distribute/install-extensions>。
   个人 Mac 上没有加域也没有 MDM。

所以「扩展本体仍需你手动加载一次」这句话在本模块之后依然成立。本模块解决的是加载**之后**
的三件事：别被停用、别藏在菜单里、能读本地文件。

## `installation_mode: allowed` 为什么是那条逃生口

`AllowedByEnterprisePolicy(id)` → `IsInstallationExplicitlyAllowed(id)` 在按 ID 的
`installation_mode` 为 forced / recommended / **allowed** 时返回 true
（`chromium/src:chrome/browser/extensions/extension_management.cc:291-305`、`:699-706`）。
关键差别是：`ExtensionInstallForcelist` 和 `ExtensionSettings` 的策略定义里写着
「Windows 要加域、macOS 要 MDM」，而 `ExtensionInstallAllowlist` 的定义里**没有**那段话。
调研全文见 `.scratch/browser-control-extension/issues/04-self-hosted-distribution.md`。

**这条源码链是完整的，但「未被托管的普通 Mac 认不认手工放进 /Library/Managed
Preferences 的 plist」没有先例**，本模块把它写下去就是那次实验本身。

## 落点

- **macOS**：`/Library/Managed Preferences/<bundle-id>.plist`。Chromium 在 macOS 上用
  `CFPreferencesCopyAppValue` + `CFPreferencesAppValueIsForced` 读策略
  （`chromium/src:components/policy/core/common/policy_loader_mac.cc`），被「forced」的
  那个来源就是这个目录。每个域名只有一个文件，**所以必须合并写，不能覆盖** —— 覆盖会
  把公司 MDM 下发的策略抹掉。
- **Linux**：`/etc/opt/<vendor>/policies/managed/` 下**我们自己的一个文件**。那个目录
  是「把所有 json 合起来」的语义，各写各的，不用合并。
- **Windows**：没做。策略落点是 HKLM 注册表，这台机器上验不了，与其写一段没验过的代码
  不如明说。`plan()` 在 Windows 上返回空表，安装流程照常走手动加载。
- **Firefox**：没做，而且不该做。它不读 plist，读的是 app 包内的
  `Contents/Resources/distribution/policies.json`（改 app 包要先去掉 quarantine 属性，
  否则 Firefox 直接报「is damaged and can't be opened」）。更要紧的是**企业策略绕不过
  Mozilla 签名**（<https://extensionworkshop.com/documentation/enterprise/enterprise-distribution/>），
  我们的扩展没过 AMO unlisted 签名，写了也装不上。为一件没用的事去改别人 app 包里的
  文件，是拿用户的浏览器冒险。
"""

from __future__ import annotations

import json
import pathlib
import plistlib
import shlex
import subprocess
import tempfile

# 策略里我们这一条的内容。三个字段的取值出处见模块 docstring 的表。
POLICY_MODE = "allowed"
POLICY_PIN = "force_pinned"

# macOS 的 bundle id。chrome / brave 是在本机 `Info.plist` 里读出来的，其余四个来自各家
# 文档，**本机没装，没能实测**。
# 需要: 在装了 chromium / edge / opera / vivaldi 的机器上核对这四个 bundle id。
MAC_DOMAINS: dict[str, str] = {
    "chrome": "com.google.Chrome",
    "chromium": "org.chromium.Chromium",
    "edge": "com.microsoft.Edge",
    "brave": "com.brave.Browser",
    "opera": "com.operasoftware.Opera",
    "vivaldi": "com.vivaldi.Vivaldi",
}

# Linux 的托管策略目录。Chromium 系一律是 `<配置根>/policies/managed/`，差别只在厂商名。
LINUX_DIRS: dict[str, str] = {
    "chrome": "/etc/opt/chrome/policies/managed",
    "chromium": "/etc/chromium/policies/managed",
    "edge": "/etc/opt/edge/policies/managed",
    "brave": "/etc/brave/policies/managed",
    "opera": "/etc/opt/opera/policies/managed",
    "vivaldi": "/etc/opt/vivaldi/policies/managed",
}

MAC_POLICY_DIR = "/Library/Managed Preferences"
# Linux 上我们自己那个文件的名字。目录里的 json 是合并语义，名字只要不撞就行。
LINUX_POLICY_FILE = "lazygophers-browse.json"

POLICY_KEY = "ExtensionSettings"


class PolicyError(Exception):
    """策略没写成。调用方负责降级到手动流程，不要当致命错误。"""


def settings_for(extension_ids) -> dict:
    """我们要往 `ExtensionSettings` 里塞的那几条，一个扩展 ID 一条。"""
    return {
        ext_id: {
            "installation_mode": POLICY_MODE,
            "toolbar_pin": POLICY_PIN,
            "file_url_navigation_allowed": True,
        }
        for ext_id in dict.fromkeys(extension_ids)
    }


class Target:
    """一个浏览器的一个策略落点。`browser` 只用来在输出里说是谁。"""

    def __init__(self, browser: str, path: pathlib.Path, fmt: str):
        self.browser = browser
        self.path = path
        self.fmt = fmt  # "plist"（macOS，要合并） | "json"（Linux，独占一个文件）

    def __repr__(self) -> str:  # 测试失败时看得懂
        return f"Target({self.browser!r}, {str(self.path)!r}, {self.fmt!r})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, Target) and self.browser == other.browser
                and self.path == other.path and self.fmt == other.fmt)


def targets(plat: str, browsers, mac_root: pathlib.Path | None = None,
            linux_root: pathlib.Path | None = None) -> list[Target]:
    """探测到的浏览器 → 各自的策略落点。不认识的浏览器直接跳过。

    两个 `*_root` 只为测试存在：真实落点是 `/Library` 和 `/etc`，测试里绝不能往那写。
    """
    out: list[Target] = []
    for name in browsers:
        if plat == "darwin" and name in MAC_DOMAINS:
            root = mac_root or pathlib.Path(MAC_POLICY_DIR)
            out.append(Target(name, root / f"{MAC_DOMAINS[name]}.plist", "plist"))
        elif plat == "linux" and name in LINUX_DIRS:
            path = pathlib.Path(LINUX_DIRS[name])
            if linux_root is not None:
                path = linux_root / path.relative_to("/")
            out.append(Target(name, path / LINUX_POLICY_FILE, "json"))
    # macOS 上好几个浏览器可能指向同一个文件吗？不会——bundle id 各不相同。但去个重
    # 以防将来加了别名。
    seen: set = set()
    return [t for t in out if not (t.path in seen or seen.add(t.path))]


# ---------------------------------------------------------------- 读 / 改 / 写
def _read(target: Target) -> dict:
    """读回落点现在的内容。文件不在、或者内容不是我们认得的形状，都当空的。"""
    if not target.path.exists():
        return {}
    try:
        raw = target.path.read_bytes()
        got = plistlib.loads(raw) if target.fmt == "plist" else json.loads(raw)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return got if isinstance(got, dict) else {}


def merge(current: dict, extension_ids) -> dict:
    """把我们那几条并进现有策略。**别的字段一个都不动。**

    macOS 一个域名只有一个 plist，公司 MDM 下发的策略也在里面 —— 整份覆盖就是把它抹掉。
    """
    merged = dict(current)
    existing = merged.get(POLICY_KEY)
    settings = dict(existing) if isinstance(existing, dict) else {}
    settings.update(settings_for(extension_ids))
    merged[POLICY_KEY] = settings
    return merged


def unmerge(current: dict, extension_ids) -> dict:
    """把我们那几条摘出来，别人的留着。"""
    merged = dict(current)
    existing = merged.get(POLICY_KEY)
    if not isinstance(existing, dict):
        merged.pop(POLICY_KEY, None)
        return merged
    settings = {k: v for k, v in existing.items() if k not in set(extension_ids)}
    if settings:
        merged[POLICY_KEY] = settings
    else:
        merged.pop(POLICY_KEY, None)
    return merged


def encode(data: dict, fmt: str) -> bytes:
    if fmt == "plist":
        return plistlib.dumps(data, fmt=plistlib.FMT_XML)
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def plan(plat: str, browsers, extension_ids, *, remove: bool = False,
         mac_root: pathlib.Path | None = None,
         linux_root: pathlib.Path | None = None) -> list[tuple[Target, bytes | None]]:
    """算出每个落点该变成什么样。**纯计算，不写任何东西。**

    第二项是要写进去的字节；`None` 表示这个文件该整个删掉（摘掉我们那几条之后它空了，
    说明当初就是我们建的）。内容和现在一模一样的落点不会出现在结果里 —— 没有要改的东西
    就不该去要管理员密码。
    """
    out: list[tuple[Target, bytes | None]] = []
    for target in targets(plat, browsers, mac_root, linux_root):
        current = _read(target)
        exists = target.path.exists()
        wanted = unmerge(current, extension_ids) if remove else merge(current, extension_ids)
        if exists and wanted == current:
            continue          # 已经是想要的样子，别为它去要管理员密码
        if not wanted:
            if exists:
                out.append((target, None))   # 摘掉我们那几条就空了，说明当初是我们建的
            continue
        out.append((target, encode(wanted, target.fmt)))
    return out


def describe(steps: list[tuple[Target, bytes | None]]) -> list[str]:
    """给用户看的「我要动哪些文件」。要密码之前先把这个打出来。"""
    return [f"{'删除' if body is None else '写入'} {step.path}（{step.browser}）"
            for step, body in steps]


# ---------------------------------------------------------------- 真的写下去
def _script(steps: list[tuple[Target, bytes | None]], staged: dict) -> str:
    """一段 sh：把暂存好的文件搬到位。一次 sudo 干完全部，不是每个文件问一次密码。"""
    lines = ["set -e"]
    for target, body in steps:
        dest = shlex.quote(str(target.path))
        if body is None:
            lines.append(f"rm -f {dest}")
            continue
        lines.append(f"mkdir -p {shlex.quote(str(target.path.parent))}")
        lines.append(f"cat {shlex.quote(str(staged[target.path]))} > {dest}")
        # 浏览器以普通用户身份读它，内容不含密钥，0644；属主必须是 root，否则策略目录
        # 变成一个普通用户可写的洞
        lines.append(f"chmod 644 {dest}")
        lines.append(f"chown root {dest}")
    return "\n".join(lines)


def apply(steps: list[tuple[Target, bytes | None]], *, runner=subprocess.run) -> None:
    """拿着 plan 去 sudo。用户拒绝、密码错、写失败，一律抛 PolicyError。

    `runner` 是为了测试能注入 —— 单元测试里绝不真的调 sudo、也绝不真的写 /Library。
    """
    if not steps:
        return
    staged: dict[pathlib.Path, str] = {}
    tmp = tempfile.TemporaryDirectory(prefix="browse-policy-")
    try:
        for index, (target, body) in enumerate(steps):
            if body is None:
                continue
            stage = pathlib.Path(tmp.name) / f"{index}.policy"
            stage.write_bytes(body)
            # sudo 起的是 root 的 sh，它得读得到暂存文件
            stage.chmod(0o644)
            staged[target.path] = str(stage)
        pathlib.Path(tmp.name).chmod(0o755)
        done = runner(["sudo", "/bin/sh", "-c", _script(steps, staged)],
                      capture_output=True, text=True)
    finally:
        tmp.cleanup()
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip() or f"sudo 退出码 {done.returncode}"
        raise PolicyError(detail)


def sudo_available(*, runner=subprocess.run) -> bool:
    """这台机器上有 sudo 吗。没有就别摆一个要不到的密码框。"""
    try:
        return runner(["sudo", "-V"], capture_output=True).returncode == 0
    except OSError:
        return False
