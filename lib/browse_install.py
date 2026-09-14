"""把 browse 注册成浏览器的 native messaging host（三平台，用户级）

常用：
  browse install                      # 装到探测到的浏览器
  browse install --list               # 只看探测到了谁，不写盘
  browse uninstall                    # 全部卸干净
  browse install --browsers chrome,firefox
  browse install --extension-id <Edge 商店 ID>

写两样东西：一份 JSON（manifest）和一个 wrapper 脚本。**native messaging 的 manifest
没有 `args` 字段**（Chrome / Edge / Firefox 都没有），字段只有 name / description /
path / type / allowed_origins（Firefox 是 allowed_extensions），所以 `--native-host`
这个参数没地方传——manifest 的 `path` 只能指向一个自带该参数的 wrapper：

    #!/bin/sh
    exec /abs/path/to/browse --native-host "$@"

浏览器是直接 fork 执行 `path` 的，所以 wrapper 必须有执行位；被它指向的 browse 不
需要，native host 内部用 sys.executable 显式起（T03 `lib/browse_native_host.py`）。

路径表出处：`.scratch/browser-control-extension/spec.md` 7.3，Firefox 一行出自
<https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_manifests>。

**住在 `lib/` 而不是 `browser-extension/install/`**：manifest 里写的是浏览器要 fork
的绝对路径，所以装扩展这件事必须跟着 browse 一起发布。放在仓库的 extension 目录下
时，`uvx` 装的人手上根本没有那个文件。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
import time

from lib import browse_policy
from lib.ui import reporter

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

HOST_NAME = "com.lazygophers.browse"
DESCRIPTION = "lazygophers browse — 用命令行驱动浏览器"

# 扩展 ID 由 manifest 的 `key` 字段算出（`components/crx_file/id_util.cc:44-57`），
# 与安装目录无关。Chromium 系（Chrome / Edge / Brave / Opera / Vivaldi）用同一套算
# 法，所以自分发的 unpacked 扩展在 Edge 上拿到的是同一个 ID。
# **Edge on Windows 只读第一个命中的 manifest**（9 级回落到 Chromium 键、Chrome
# 键），所以只能有一份 manifest 同时授权两边——ID 写成列表就是为这个：真去 Edge
# Add-ons 商店上架后商店会另发一个 ID，用 `--extension-id` 追加进来即可。
# 需要: 上架 Edge Add-ons 后把商店分配的 ID 补进 EXTENSION_IDS。
EXTENSION_IDS: tuple[str, ...] = ("podeceeeafjdcemppcgjhhokcokpcama",)

# Firefox 侧字段名是 allowed_extensions，值是 gecko.id 字符串（不是 URL）。扩展侧
# 的 browser_specific_settings.gecko.id 必须与此一致。
GECKO_IDS: tuple[str, ...] = ("browse@lazygophers.com",)

CHROMIUM = "chromium"
GECKO = "gecko"

_MAC_CHROME = "Library/Application Support/Google/Chrome/NativeMessagingHosts"
_LINUX_CHROME = ".config/google-chrome/NativeMessagingHosts"

# 浏览器 -> (manifest 风味, 探测目录, 落点...)。路径一律相对 home。
# 落点以 "reg:" 开头的是 Windows 注册表键（HKCU），其余是要写 <HOST_NAME>.json 的目录。
BROWSERS: dict[str, dict[str, tuple[str, str, tuple[str, ...]]]] = {
    "darwin": {
        "chrome": (CHROMIUM, "Library/Application Support/Google/Chrome", (_MAC_CHROME,)),
        "chromium": (CHROMIUM, "Library/Application Support/Chromium",
                     ("Library/Application Support/Chromium/NativeMessagingHosts",)),
        "edge": (CHROMIUM, "Library/Application Support/Microsoft Edge",
                 ("Library/Application Support/Microsoft Edge/NativeMessagingHosts",)),
        # brave-core 源码（app/brave_main_delegate.cc:141-164）显式 override 到
        # Chrome 目录，KeePassXC 与社区指 BraveSoftware 目录，两个来源冲突且实测机
        # 上两个目录都存在——成本只是多拷一个文件，两个都写。
        "brave": (CHROMIUM, "Library/Application Support/BraveSoftware/Brave-Browser",
                  ("Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts",
                   _MAC_CHROME)),
        "opera": (CHROMIUM, "Library/Application Support/com.operasoftware.Opera", (_MAC_CHROME,)),
        "vivaldi": (CHROMIUM, "Library/Application Support/Vivaldi",
                    ("Library/Application Support/Vivaldi/NativeMessagingHosts",)),
        "firefox": (GECKO, "Library/Application Support/Firefox",
                    ("Library/Application Support/Mozilla/NativeMessagingHosts",)),
    },
    "linux": {
        "chrome": (CHROMIUM, ".config/google-chrome", (_LINUX_CHROME,)),
        "chromium": (CHROMIUM, ".config/chromium", (".config/chromium/NativeMessagingHosts",)),
        "edge": (CHROMIUM, ".config/microsoft-edge", (".config/microsoft-edge/NativeMessagingHosts",)),
        "brave": (CHROMIUM, ".config/BraveSoftware/Brave-Browser",
                  (".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",)),
        # spec 7.3 给 Opera 的 Linux 落点是系统级 /etc/opt/chrome/native-messaging-hosts，
        # 要 root。这里走等价的用户级 Chrome 目录，不提权。
        # 需要: 在装了 Opera 的 Linux 上确认用户级 Chrome 目录确实被读到。
        "opera": (CHROMIUM, ".config/opera", (_LINUX_CHROME,)),
        "vivaldi": (CHROMIUM, ".config/vivaldi", (".config/vivaldi/NativeMessagingHosts",)),
        "firefox": (GECKO, ".mozilla/firefox", (".mozilla/native-messaging-hosts",)),
    },
    # Windows 不按目录读，按注册表键读：键的默认值是 manifest 文件的绝对路径，
    # 文件本身统一放 %LOCALAPPDATA%\lazygophers\browse\。
    # 需要: 实机验证——macOS 上跑不到真注册表，这里的写/删走 reg_set / reg_delete
    # 两个可注入的函数，单元测试用假的。
    "win32": {
        "chrome": (CHROMIUM, "AppData/Local/Google/Chrome/User Data",
                   (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",)),
        "chromium": (CHROMIUM, "AppData/Local/Chromium/User Data",
                     (r"reg:SOFTWARE\Chromium\NativeMessagingHosts",
                      r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts")),
        "edge": (CHROMIUM, "AppData/Local/Microsoft/Edge/User Data",
                 (r"reg:SOFTWARE\Microsoft\Edge\NativeMessagingHosts",)),
        # Brave / Opera / Vivaldi 最终都落 Chrome 键（Opera 官方文档给的是 HKLM，
        # 需要管理员；HKCU 同键对当前用户等效，这里只写 HKCU）。
        "brave": (CHROMIUM, "AppData/Local/BraveSoftware/Brave-Browser/User Data",
                  (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",)),
        "opera": (CHROMIUM, "AppData/Roaming/Opera Software/Opera Stable",
                  (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",)),
        "vivaldi": (CHROMIUM, "AppData/Local/Vivaldi/User Data",
                    (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",)),
        "firefox": (GECKO, "AppData/Roaming/Mozilla/Firefox",
                    (r"reg:SOFTWARE\Mozilla\NativeMessagingHosts",)),
    },
}

WIN_MANIFEST_DIR = "AppData/Local/lazygophers/browse"
# wrapper 的落点。位置必须稳定且是绝对路径——manifest 里写死的就是它，manifest 不
# 接受相对路径，也不会去查 PATH。
WRAPPER_DIR = ".local/state/lazygophers/scripts"
WRAPPER_NAME = "browse-native-host"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def platform_key(platform: str | None = None) -> str:
    """把 sys.platform 归到 BROWSERS 的三个键之一。"""
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        return "win32"
    if plat == "darwin":
        return "darwin"
    return "linux"


def build_manifest(flavor: str, host_path: pathlib.Path,
                   extension_ids: tuple[str, ...] = EXTENSION_IDS,
                   gecko_ids: tuple[str, ...] = GECKO_IDS) -> dict:
    """一份 native host manifest。Chromium 系和 Firefox 的授权字段名不同。

    host_path 是浏览器要 fork 的那个文件——**wrapper，不是 browse 本身**。
    manifest 没有 args 字段，`--native-host` 只能由 wrapper 自己带上。
    """
    manifest = {
        "name": HOST_NAME,
        "description": DESCRIPTION,
        "path": str(host_path),
        "type": "stdio",
    }
    if flavor == GECKO:
        manifest["allowed_extensions"] = list(dict.fromkeys(gecko_ids))
    else:
        # 末尾斜杠必带，且不支持通配符。
        manifest["allowed_origins"] = [f"chrome-extension://{i}/"
                                       for i in dict.fromkeys(extension_ids)]
    return manifest


def detect(home: pathlib.Path, plat: str) -> list[str]:
    """探测装了哪些浏览器：看它的用户数据目录在不在。

    浏览器装了但一次都没启动过时目录还不存在，这时用 --browsers 指定。
    """
    return [name for name, (_, probe, _) in BROWSERS[plat].items()
            if (home / probe).exists()]


def wrapper_path(home: pathlib.Path, plat: str) -> pathlib.Path:
    """manifest 的 path 指向的那个脚本。Windows 上 .sh 跑不了，用 .cmd。"""
    if plat == "win32":
        return home / WIN_MANIFEST_DIR / f"{WRAPPER_NAME}.cmd"
    return home / WRAPPER_DIR / WRAPPER_NAME


def write_wrapper(home: pathlib.Path, plat: str,
                  browse_path: pathlib.Path) -> pathlib.Path:
    """生成 wrapper 并给上执行位，返回它的绝对路径。"""
    path = wrapper_path(home, plat)
    path.parent.mkdir(parents=True, exist_ok=True)
    if plat == "win32":
        # 需要: 实机验证 .cmd 这一路。Windows 上 browse 是 uv 装出来的 browse.exe
        # 或 py 启动器认的脚本，这里只负责把参数原样透传。
        body = f'@echo off\r\n"{browse_path}" --native-host %*\r\n'
        newline = ""
    else:
        body = f'#!/bin/sh\nexec "{browse_path}" --native-host "$@"\n'
        newline = "\n"
    path.write_text(body, encoding="utf-8", newline=newline)
    # 浏览器直接 fork 执行这个文件，没有执行位就是启动失败。
    path.chmod(0o755)
    return path


def win_manifest_path(home: pathlib.Path, flavor: str) -> pathlib.Path:
    suffix = ".firefox.json" if flavor == GECKO else ".json"
    return home / WIN_MANIFEST_DIR / f"{HOST_NAME}{suffix}"


def _reg_set(key: str, value: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, value)


def _reg_delete(key: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


def _write_manifest(path: pathlib.Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    # 浏览器以当前用户身份读它，内容不含密钥，0644 即可。
    path.chmod(0o644)


def install(home: pathlib.Path, plat: str, browse_path: pathlib.Path, *,
            browsers: list[str] | None = None,
            extension_ids: tuple[str, ...] = EXTENSION_IDS,
            gecko_ids: tuple[str, ...] = GECKO_IDS,
            reg_set=_reg_set) -> list[tuple[str, str]]:
    """写 wrapper + 给每个选中的浏览器写 manifest，返回 [(浏览器, 落点描述)]。"""
    names = detect(home, plat) if browsers is None else browsers
    wrapper = write_wrapper(home, plat, browse_path)
    done: list[tuple[str, str]] = []
    for name in names:
        flavor, _, dests = BROWSERS[plat][name]
        manifest = build_manifest(flavor, wrapper, extension_ids, gecko_ids)
        for dest in dests:
            if dest.startswith("reg:"):
                path = win_manifest_path(home, flavor)
                _write_manifest(path, manifest)
                reg_set(f"{dest[4:]}\\{HOST_NAME}", str(path))
                done.append((name, f"HKCU\\{dest[4:]}\\{HOST_NAME} -> {path}"))
            else:
                path = home / dest / f"{HOST_NAME}.json"
                _write_manifest(path, manifest)
                done.append((name, str(path)))
    return done


def uninstall(home: pathlib.Path, plat: str, *,
              reg_delete=_reg_delete) -> list[tuple[str, str]]:
    """删掉所有已知落点（不管当初探测到没有），不留残留。"""
    removed: list[tuple[str, str]] = []
    for name, (_, _, dests) in BROWSERS[plat].items():
        for dest in dests:
            if dest.startswith("reg:"):
                reg_delete(f"{dest[4:]}\\{HOST_NAME}")
                removed.append((name, f"HKCU\\{dest[4:]}\\{HOST_NAME}"))
                continue
            path = home / dest / f"{HOST_NAME}.json"
            if path.exists():
                path.unlink()
                removed.append((name, str(path)))
    for flavor in (CHROMIUM, GECKO):
        path = win_manifest_path(home, flavor)
        if path.exists():
            path.unlink()
            removed.append(("windows", str(path)))
    for wrapper in (wrapper_path(home, "win32"), wrapper_path(home, "linux")):
        if wrapper.exists():
            wrapper.unlink()
            removed.append(("wrapper", str(wrapper)))
    for stale in (home / WIN_MANIFEST_DIR, home / WRAPPER_DIR):
        if stale.is_dir() and not any(stale.iterdir()):
            stale.rmdir()
    return removed


def resolve_browse_path(given: str | None) -> pathlib.Path:
    """manifest 里要写的 browse 绝对路径。

    这个路径要长期有效：浏览器每次启动 native host 都按它去 fork。`uvx` 那种一次性
    环境里的路径随时会消失，所以先找 PATH 上装好的那个（`uv tool install` /
    `pipx install` 的落点），再退回仓库里的 `bin/browse`。
    """
    if given:
        path = pathlib.Path(given).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"--browse-path 指向的文件不存在：{path}")
        return path
    found = shutil.which("browse")
    if found:
        return pathlib.Path(found).resolve()
    local = REPO_ROOT / "bin" / "browse"
    if local.exists():
        return local.resolve()
    raise ValueError(
        "PATH 上没有 browse，仓库里也没有 bin/browse。先 `uv tool install "
        "git+https://github.com/lazygophers/scripts` 装成常驻命令，或用 "
        "--browse-path 指一个不会消失的绝对路径"
    )


EXTENSION_SRC = REPO_ROOT / "browser-extension" / "extension"
CONNECT_POLL_SECONDS = 2.0
WAIT_TIMEOUT = 120.0


def build_extension(src: pathlib.Path = EXTENSION_SRC) -> pathlib.Path:
    """构建扩展，返回 dist 目录。已经构建过就直接返回，不重复跑。

    浏览器加载的是 dist/ 而不是 src/，忘了构建的话扩展装上去也是坏的
    （`page-locate.js` 不在，所有 input.* 都会失败）。
    """
    dist = src / "dist"
    if (dist / "manifest.json").exists():
        return dist
    if not (src / "package.json").exists():
        raise FileNotFoundError(f"扩展源码不在 {src}，用 --no-build 跳过构建")
    if not (src / "node_modules").exists():
        subprocess.run(["npm", "install"], cwd=src, check=True)
    subprocess.run(["npm", "run", "build"], cwd=src, check=True)
    return dist


def copy_to_clipboard(text: str) -> bool:
    """把路径塞进剪贴板，好让用户在文件选择框里直接粘贴。失败不算错。"""
    tool = {"darwin": ["pbcopy"], "linux": ["xclip", "-selection", "clipboard"],
            "windows": ["clip"]}.get(platform_key())
    if tool is None:
        return False
    try:
        subprocess.run(tool, input=text.encode(), check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def wait_for_extension(browse_path: pathlib.Path, timeout: float) -> bool:
    """真跑一条指令，等它成功。成功返回 True，超时返回 False。

    故意走用户自己会走的那条路（`browse browsingContext getTree`）而不是探测
    daemon 的内部状态：manifest 写对了不代表扩展真的加载了，扩展加载了也不代表
    它连得上。只有一条指令真的跑通，才说明整条链路是好的。

    退出码 3 = 浏览器没连上（`lib/cli/browse.py` 的 EXIT_NO_BROWSER），是等待中的
    正常状态；0 = 通了；其余退出码说明是别的毛病，不再干等。
    """
    deadline = time.monotonic() + timeout
    while True:
        done = subprocess.run(
            [sys.executable, str(browse_path), "browsingContext", "getTree", "--no-say"],
            capture_output=True,
        )
        if done.returncode == 0:
            return True
        if done.returncode != 3:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(CONNECT_POLL_SECONDS)


# 策略换来的三件事。写之前原样打印给用户看 —— 要管理员密码之前得先说清楚要干嘛。
POLICY_WANTS = (
    "扩展不会被浏览器自动停用（自分发的扩展默认会被停）",
    "扩展图标固定在工具栏上，不用每次去菜单里翻",
    "扩展能访问 file:// 开头的本地文件",
)


def write_policy(out, plat: str, browsers: list[str], extension_ids: tuple[str, ...], *,
                 remove: bool = False) -> bool:
    """写 / 删浏览器企业策略。成功或本来就没事可做返回 True，没写成返回 False。

    没写成**不是致命错误**：手动加载那条路依然通，只是上面 POLICY_WANTS 那三条拿不到。
    所以这里只报告，由调用方决定怎么往下走 —— 绝不静默吞掉。
    """
    try:
        steps = browse_policy.plan(plat, browsers, extension_ids, remove=remove)
    except OSError as exc:
        out.warn(f"读不了现有策略，跳过这一步：{exc}")
        return False
    if not steps:
        out.info("浏览器策略：没有要清理的" if remove else "浏览器策略：已经是想要的样子，不用改")
        return True

    out.info("")
    out.info("下面要改这几个系统文件，所以需要你的管理员密码：")
    for line in browse_policy.describe(steps):
        out.info(f"  {line}")
    if not remove:
        out.info("写它是为了三件事：")
        for want in POLICY_WANTS:
            out.info(f"  · {want}")
        out.info("要撤销：`browse uninstall` 会把这几条原样删掉，别人的策略一个字不动")
    out.info("")

    if not browse_policy.sudo_available():
        out.err("这台机器上没有 sudo，策略改不了")
        return False
    try:
        browse_policy.apply(steps)
    except browse_policy.PolicyError as exc:
        out.err(f"策略没{'清理' if remove else '写'}成：{exc}")
        return False
    out.ok(f"浏览器策略已{'清理' if remove else '写入'}（{len(steps)} 个文件）")
    return True


def policy_fallback_notice(out) -> None:
    """策略没写成时，明说因此少了什么。不许假装无事发生。"""
    out.warn("策略没写成，下面三件事就没有了，扩展本身还是能用：")
    for want in POLICY_WANTS:
        out.warn(f"  · {want}")
    out.warn("想再试一次：`browse install`；不想要就 `browse install --no-policy`")


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="browse install",
        description="把 browse 注册成浏览器的 native messaging host",
    )
    parser.add_argument("--uninstall", action="store_true", help="删掉所有已知落点")
    parser.add_argument("--list", action="store_true", dest="list_only",
                        help="只打印探测到的浏览器和落点，不写任何东西")
    parser.add_argument("--browsers", help="逗号分隔，跳过探测直接指定，如 chrome,firefox")
    parser.add_argument("--browse-path", help="manifest 里写的 browse 绝对路径")
    parser.add_argument("--extension-id", action="append", default=[],
                        help="追加一个 Chromium 扩展 ID（Edge 商店另发的 ID 用这个补）")
    parser.add_argument("--no-build", action="store_true",
                        help="不自动构建扩展（默认 dist/ 缺失时自动跑 npm run build）")
    parser.add_argument("--no-wait", action="store_true",
                        help="不等扩展连上就返回（默认等 120 秒）")
    parser.add_argument("--no-policy", action="store_true",
                        help="不写浏览器企业策略（默认会写，需要管理员密码）")
    parser.add_argument("--gecko-id", action="append", default=[],
                        help="追加一个 Firefox 扩展 ID（gecko.id）")
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    """`browse install` / `browse uninstall` 的实现。

    `--no-say` / `--debug` / 计时都由 `lib/cli/browse.py` 的入口统一处理过了，这里
    不再包一层。
    """
    args = _parse(argv)
    out = reporter(stderr=True)
    home = pathlib.Path.home()
    plat = platform_key()
    table = BROWSERS[plat]

    if args.browsers:
        names = [n.strip() for n in args.browsers.split(",") if n.strip()]
        unknown = [n for n in names if n not in table]
        if unknown:
            out.err(f"{plat} 上不认识这些浏览器：{', '.join(unknown)}；"
                    f"可选：{', '.join(table)}")
            return EXIT_USAGE
    else:
        names = None

    if args.uninstall:
        removed = uninstall(home, plat)
        for name, where in removed:
            out.ok(f"{name}: 已删 {where}")
        if not removed:
            out.info("没有找到任何已安装的 manifest")
        # 策略是装的时候写进去的，卸载必须原样清掉，不留残留。按「所有已知浏览器」清，
        # 和上面删 manifest 同一个口径：当初探测到谁，现在不一定还探测得到。
        if not args.no_policy and sys.stderr.isatty():
            write_policy(out, plat, list(table), EXTENSION_IDS + tuple(args.extension_id),
                         remove=True)
        return EXIT_OK

    try:
        browse_path = resolve_browse_path(args.browse_path)
    except ValueError as exc:
        out.err(str(exc))
        return EXIT_USAGE

    found = names if names is not None else detect(home, plat)
    if not found:
        out.err(f"没探测到任何浏览器（{plat}）。浏览器装了但没启动过时用户目录还不存在，"
                f"用 --browsers 指定，可选：{', '.join(table)}")
        return EXIT_FAILED

    if args.list_only:
        out.info(f"browse：{browse_path}")
        out.info(f"wrapper（manifest 的 path 指向它）：{wrapper_path(home, plat)}")
        for name in found:
            flavor, _, dests = table[name]
            for dest in dests:
                where = (f"HKCU\\{dest[4:]}\\{HOST_NAME}" if dest.startswith("reg:")
                         else str(home / dest / f"{HOST_NAME}.json"))
                out.info(f"{name} ({flavor}): {where}")
        return EXIT_OK

    done = install(
        home, plat, browse_path,
        browsers=found,
        extension_ids=EXTENSION_IDS + tuple(args.extension_id),
        gecko_ids=GECKO_IDS + tuple(args.gecko_id),
    )
    for name, where in done:
        out.ok(f"{name}: {where}")
    out.info(f"wrapper：{wrapper_path(home, plat)} -> {browse_path} --native-host")

    # 到这里为止，只完成了「浏览器怎么找到 browse」。扩展本体还没装 —— 而且
    # 装不了：Chrome 把所有程序化安装扩展的路都封了（--load-extension 于 137
    # 移除、--disable-extensions-except 于 139 移除、开发者模式 pref 属受保护
    # 配置会被重置、CDP Extensions.loadUnpacked 要 browser-level target 而
    # 136+ 拒绝对默认 profile 开调试端口）。剩下的只能是人点一次。
    # 非交互（管道、CI、测试）时到此为止：只做注册。构建、指引、等待都是给
    # 坐在终端前的人看的，脚本里跑不该被一个 120 秒的等待卡住。
    if not sys.stderr.isatty():
        out.info("扩展本体要手动加载，见 browser-extension/README.md")
        # 非交互下不碰策略：那一步要管理员密码，没有终端就没人能输
        out.info("浏览器策略这一步跳过了（要管理员密码）。要写就在终端里跑 `browse install`")
        return EXIT_OK

    # 到这里 manifest 写完了。策略是**加载之后**的待遇（别被停用 / 固定工具栏 /
    # 能读本地文件），和「浏览器怎么找到 browse」无关，所以放在这一步。
    policy_ok = True
    if not args.no_policy:
        policy_ok = write_policy(out, plat, found, EXTENSION_IDS + tuple(args.extension_id))

    if args.no_build:
        dist = EXTENSION_SRC / "dist"
    else:
        try:
            dist = build_extension()
        except (OSError, subprocess.CalledProcessError) as exc:
            out.err(f"构建扩展失败：{exc}")
            out.info(f"手动构建：cd {EXTENSION_SRC} && npm install && npm run build")
            return EXIT_FAILED
        out.ok(f"扩展已构建：{dist}")

    copied = copy_to_clipboard(str(dist))
    out.info("")
    out.info("还差一步，只能你自己点 —— Chrome 不允许任何程序替用户装扩展：")
    out.info("  1. 打开 chrome://extensions")
    out.info("  2. 右上角打开「开发者模式」（此后要一直开着）")
    out.info("  3. 点「加载已解压的扩展程序」，选这个目录：")
    out.info(f"     {dist}")
    if copied:
        out.info("     （路径已复制到剪贴板，文件选择框里按 Cmd+Shift+G 粘贴即可）")
    # 无痕模式这条只能人点：`ExtensionSettings` 策略里根本没有 incognito 字段，
    # 程序侧没有入口。与其让用户以为装完就万事大吉，不如在这里说清楚。
    out.info("  4. 想让它在无痕窗口里也能用的话，点这个扩展的「详情」，")
    out.info("     打开「在无痕模式下启用」—— 这一条只能你自己点，没有任何程序能代劳")
    out.info("")
    if not policy_ok:
        policy_fallback_notice(out)
        out.info("")

    if args.no_wait:
        out.info(f"装完自己验：{browse_path} browsingContext getTree --table")
        return EXIT_OK

    out.info(f"等你装上……（最多 {int(WAIT_TIMEOUT)} 秒，Ctrl-C 可中断）")
    try:
        connected = wait_for_extension(browse_path, WAIT_TIMEOUT)
    except KeyboardInterrupt:
        out.info(f"没等到。装好后自己验：{browse_path} browsingContext getTree --table")
        return EXIT_OK
    if connected:
        out.ok("扩展已连上，整条链路通了。试试：browse browsingContext getTree --table")
        return EXIT_OK
    out.err("超时：扩展还没连上。排查顺序：")
    out.err("  1. chrome://extensions 里有没有看到这个扩展、是不是启用状态")
    out.err("  2. 扩展 ID 是不是 podeceeeafjdcemppcgjhhokcokpcama（不是的话 manifest 的 key 被改过）")
    out.err("  3. 点扩展卡片上的 service worker，看控制台有没有报错")
    return EXIT_FAILED
