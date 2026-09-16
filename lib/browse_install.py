"""把 browse 注册成浏览器的 native messaging host（三平台，用户级）

2026-09-15 连接层重做后，本模块只剩三件事：

- `EXTENSION_IDS`：bridge 的 WebSocket Origin 白名单（`lib/browse_bridge.py`）
- `uninstall()`：清掉 2026-09-15 之前装的 native messaging 注册（manifest/wrapper/
  注册表键），不留残留；`install_status()` 供 `browse status` 提示旧注册还在
- `main()`：构建扩展 + 指引用户在 chrome://extensions 加载一次 + 等它连上 bridge。
  不再写任何 native messaging 注册 —— 扩展自己连 bridge 的 WebSocket（ws://127.0.0.1:9330），
  装完不需要重启浏览器

路径表（BROWSERS/LEGACY_DESTS）只服务 uninstall/残留检测，历史出处：
`.scratch/browser-control-extension/spec.md` 7.3 与
<https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_manifests>。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

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
        # 每个浏览器只读自己的目录（出处：developer.chrome.com/docs/extensions/
        # develop/concepts/native-messaging 的 per-browser 路径表，Brave 官方社区
        # community.brave.app/t/164487 同口径）。2026-09-15 实锤纠错：以前把
        # brave/opera 的 manifest 也写进 Chrome 共享目录，多个浏览器写同一个文件，
        # 后写的覆盖先写的 —— Chrome 的扩展 fork 到 opera 的 wrapper，daemon 就把
        # Chrome 的连接标成 opera（用户机器上没装 opera 却显示「opera 已连接」）。
        "brave": (CHROMIUM, "Library/Application Support/BraveSoftware/Brave-Browser",
                  ("Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts",)),
        "opera": (CHROMIUM, "Library/Application Support/com.operasoftware.Opera",
                  ("Library/Application Support/com.operasoftware.Opera/NativeMessagingHosts",)),
        "vivaldi": (CHROMIUM, "Library/Application Support/Vivaldi",
                    ("Library/Application Support/Vivaldi/NativeMessagingHosts",)),
        # Arc 是 Chromium 分支，manifest 格式同 Chromium，但落点在自己的
        # `Arc/User Data/NativeMessagingHosts`（多一层 User Data）。
        # 出处（类3）: https://www.reddit.com/r/StopTheMadnessSupport/comments/1jocs70/
        # 需要: Windows 版 Arc 的注册表落点按 Chrome 键处理是推测，实机验证
        "arc": (CHROMIUM, "Library/Application Support/Arc",
                ("Library/Application Support/Arc/User Data/NativeMessagingHosts",)),
        "firefox": (GECKO, "Library/Application Support/Firefox",
                    ("Library/Application Support/Mozilla/NativeMessagingHosts",)),
    },
    "linux": {
        "chrome": (CHROMIUM, ".config/google-chrome", (_LINUX_CHROME,)),
        "chromium": (CHROMIUM, ".config/chromium", (".config/chromium/NativeMessagingHosts",)),
        "edge": (CHROMIUM, ".config/microsoft-edge", (".config/microsoft-edge/NativeMessagingHosts",)),
        "brave": (CHROMIUM, ".config/BraveSoftware/Brave-Browser",
                  (".config/BraveSoftware/Brave-Browser/NativeMessagingHosts",)),
        # Chromium 系各读各的用户级目录（developer.chrome.com 同一套约定），
        # 不借 Chrome 的目录 —— 那会让两个浏览器写同一个文件互相覆盖（见 darwin 注释）
        "opera": (CHROMIUM, ".config/opera", (".config/opera/NativeMessagingHosts",)),
        "vivaldi": (CHROMIUM, ".config/vivaldi", (".config/vivaldi/NativeMessagingHosts",)),
        "arc": (CHROMIUM, ".config/Arc", (".config/Arc/User Data/NativeMessagingHosts",)),
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
                     (r"reg:SOFTWARE\Chromium\NativeMessagingHosts",)),
        "edge": (CHROMIUM, "AppData/Local/Microsoft/Edge/User Data",
                 (r"reg:SOFTWARE\Microsoft\Edge\NativeMessagingHosts",)),
        # Windows 同一个约定：各读各的 HKCU\Software\<厂牌>\<浏览器>\NativeMessagingHosts
        # （出处同 developer.chrome.com 的 Windows 一节）。旧版本把 brave/opera/
        # vivaldi 都写到 Chrome 键上，同文件互相覆盖，卸载时由 LEGACY_DESTS 清理。
        "brave": (CHROMIUM, "AppData/Local/BraveSoftware/Brave-Browser/User Data",
                  (r"reg:SOFTWARE\BraveSoftware\Brave-Browser\NativeMessagingHosts",)),
        "opera": (CHROMIUM, "AppData/Roaming/Opera Software/Opera Stable",
                  (r"reg:SOFTWARE\Opera Software\NativeMessagingHosts",)),
        "vivaldi": (CHROMIUM, "AppData/Local/Vivaldi/User Data",
                    (r"reg:SOFTWARE\Vivaldi\NativeMessagingHosts",)),
        # Windows 版 Arc 是 MSIX 打包，native messaging 落点没有公开文档；
        # 按约定给独立键，需要: 实机验证
        "arc": (CHROMIUM, "AppData/Local/Packages/TheBrowserCompany.Arc",
                (r"reg:SOFTWARE\Arc\NativeMessagingHosts",)),
        "firefox": (GECKO, "AppData/Roaming/Mozilla/Firefox",
                    (r"reg:SOFTWARE\Mozilla\NativeMessagingHosts",)),
    },
}

WIN_MANIFEST_DIR = "AppData/Local/lazygophers/browse"

# 2026-09-15 之前 brave/opera/vivaldi 在 Windows 上错写 Chrome 注册表键，卸载时
# 要把这些旧键一并清掉，不留指向已删 manifest 的死键。
LEGACY_DESTS: dict[str, dict[str, tuple[str, ...]]] = {
    "win32": {
        "brave": (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",),
        "opera": (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",),
        "vivaldi": (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",),
        "chromium": (r"reg:SOFTWARE\Google\Chrome\NativeMessagingHosts",),
    },
}
# wrapper 的落点。位置必须稳定且是绝对路径——manifest 里写死的就是它，manifest 不
# 接受相对路径，也不会去查 PATH。
WRAPPER_DIR = ".local/state/lazygophers/scripts"
WRAPPER_NAME = "browse-native-host"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# 装完之后那几个只能人点的开关。都是「按需」，不是必做 —— 写成必做会让人以为不点就用不了。
MANUAL_TOGGLES = (
    "装好后这几个开关按需自己点，都在扩展的「详情」页里，各点一次就行：",
    "  · 固定到工具栏      —— 想让图标一直露在地址栏右边，不用每次去菜单里翻",
    "  · 允许访问文件网址  —— 只有要操作 file:// 开头的本地文件时才需要",
    "  · 在无痕模式下启用  —— 只有要在无痕（隐身）窗口里干活时才需要",
    "  这三个只能你自己点：改它们等于改浏览器自己的配置，我们不动那里",
)


def platform_key(platform: str | None = None) -> str:
    """把 sys.platform 归到 BROWSERS 的三个键之一。"""
    plat = sys.platform if platform is None else platform
    if plat.startswith("win"):
        return "win32"
    if plat == "darwin":
        return "darwin"
    return "linux"


def wrapper_path(home: pathlib.Path, plat: str, browser: str = "") -> pathlib.Path:
    """manifest 的 path 指向的那个脚本。Windows 上 .sh 跑不了，用 .cmd。

    **每个浏览器一个**（`browse-native-host-chrome`、`-brave`…）。名字就是身份：wrapper
    里写死 `--browser <名字>`，native host 启动时就知道自己代表谁，daemon 据此分槽。
    不这样的话一台机器上装了多个浏览器时，后连的会把先连的顶掉，而扩展侧会退避重连 ——
    两者相乘就是无限互踢。

    `browser` 留空给的是**旧版的通用 wrapper 路径**，只在卸载/清理时用得着。
    """
    name = f"{WRAPPER_NAME}-{browser}" if browser else WRAPPER_NAME
    if plat == "win32":
        return home / WIN_MANIFEST_DIR / f"{name}.cmd"
    return home / WRAPPER_DIR / name


def win_manifest_path(home: pathlib.Path, flavor: str) -> pathlib.Path:
    suffix = ".firefox.json" if flavor == GECKO else ".json"
    return home / WIN_MANIFEST_DIR / f"{HOST_NAME}{suffix}"


def _reg_delete(key: str) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except FileNotFoundError:
        pass


def _reg_query(key: str) -> str | None:
    """Windows：读注册表键的默认值（manifest 文件的绝对路径），没有就是 None。"""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            value, _ = winreg.QueryValueEx(handle, "")
            return str(value)
    except OSError:
        return None


_WRAPPER_BROWSE_RE = re.compile(r'"([^"]+)"\s+--native-host')


def install_status(home: pathlib.Path, plat: str, *,
                   reg_query=_reg_query) -> list[dict]:
    """`browse status` 的安装侧：每个浏览器一行，链路上每一环都查。

    链路是 manifest → wrapper → browse 三环：manifest 在注册位置吗、它指的 wrapper
    存在且有执行位吗、wrapper 里写死的 browse 还在吗。任何一环断了，浏览器重启后
    native host 就起不来，报的具体是哪一环就是排查顺序。
    """
    rows: list[dict] = []
    for name, (flavor, probe_dir, dests) in BROWSERS[plat].items():
        manifests: list[dict] = []
        for dest in dests:
            if dest.startswith("reg:"):
                where = f"HKCU\\{dest[4:]}\\{HOST_NAME}"
                keyed = reg_query(f"{dest[4:]}\\{HOST_NAME}")
                # 键在就算「注册过」：它指向的文件没了同样要报断链，而不是报没注册
                manifest_path = pathlib.Path(keyed) if keyed else home / "nowhere"
                exists = bool(keyed)
            else:
                where = str(home / dest / f"{HOST_NAME}.json")
                manifest_path = home / dest / f"{HOST_NAME}.json"
                exists = manifest_path.is_file()
            row = {"where": where, "exists": exists, "ok": False, "wrapper": "",
                   "wrapper_ok": False, "browse_path": "", "browse_ok": False}
            if exists:
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    wrapper = pathlib.Path(str(manifest.get("path", "")))
                    row["wrapper"] = str(wrapper)
                    row["wrapper_ok"] = wrapper.is_file() and os.access(wrapper, os.X_OK)
                    if row["wrapper_ok"]:
                        match = _WRAPPER_BROWSE_RE.search(wrapper.read_text(encoding="utf-8"))
                        if match:
                            browse_path = pathlib.Path(match.group(1))
                            row["browse_path"] = str(browse_path)
                            row["browse_ok"] = browse_path.exists()
                    row["ok"] = row["wrapper_ok"] and row["browse_ok"]
                except (OSError, ValueError):
                    pass  # manifest 读不了/不是 JSON：整条按断链报
            manifests.append(row)
        # stale 只算「注册了但断链」的；从没装过的不算，那是「没注册」
        registered = any(m["ok"] for m in manifests)
        stale = [m["where"] for m in manifests if m["exists"] and not m["ok"]]
        rows.append({
            "browser": name,
            "flavor": flavor,
            "detected": (home / probe_dir).exists(),
            "registered": registered,
            "manifests": manifests,
            "stale": stale,
        })
    return rows


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
    for name, dests in LEGACY_DESTS.get(plat, {}).items():
        for dest in dests:
            if dest.startswith("reg:"):
                reg_delete(f"{dest[4:]}\\{HOST_NAME}")
                removed.append((name, f"legacy HKCU\\{dest[4:]}\\{HOST_NAME}"))
    for flavor in (CHROMIUM, GECKO):
        path = win_manifest_path(home, flavor)
        if path.exists():
            path.unlink()
            removed.append(("windows", str(path)))
    # 每个浏览器一个 wrapper，外加旧版那个通用的 —— 全删掉，不留残留
    wrappers = [wrapper_path(home, plat_key, browser)
                for plat_key in ("win32", "linux")
                for browser in ("", *BROWSERS[plat_key])]
    for wrapper in dict.fromkeys(wrappers):
        if wrapper.exists():
            wrapper.unlink()
            removed.append(("wrapper", str(wrapper)))
    for stale in (home / WIN_MANIFEST_DIR, home / WRAPPER_DIR):
        if stale.is_dir() and not any(stale.iterdir()):
            stale.rmdir()
    return removed


EXTENSION_SRC = REPO_ROOT / "browser-extension" / "browse"
CONNECT_POLL_SECONDS = 2.0
WAIT_TIMEOUT = 300.0


def build_extension(src: pathlib.Path = EXTENSION_SRC) -> pathlib.Path:
    """构建扩展，返回 dist 目录。每次都强制重新跑，不检查 dist 是否已存在。

    浏览器加载的是 dist/ 而不是 src/，忘了构建的话扩展装上去也是坏的
    （`page-locate.js` 不在，所有 input.* 都会失败）；`dist/` 已存在也不代表
    是最新的——之前「存在就跳过」导致改完 src/manifest.json 之后 `dist/` 还是
    旧内容，Chrome 报 key 无效，且 `dist/` 本来就没进 git（`.gitignore`），
    留着旧的没有任何好处。真想跳过构建走 `--no-build`（`main()` 里单独判断，
    不经过这个函数）。
    """
    dist = src / "dist"
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


def wait_for_extension(timeout: float) -> bool:
    """真跑一条指令，等它成功。成功返回 True，超时返回 False。

    故意走用户自己会走的那条路（`browse browsingContext getTree`）而不是探测
    daemon 的内部状态：manifest 写对了不代表扩展真的加载了，扩展加载了也不代表
    它连得上。只有一条指令真的跑通，才说明整条链路是好的。

    退出码 3 = 浏览器没连上（`lib/cli/browse.py` 的 EXIT_NO_BROWSER），是等待中的
    正常状态；0 = 通了；其余退出码说明是别的毛病，不再干等。
    """
    from lib.lazyhelp import _resolve

    browse_bin = _resolve("browse") or "browse"
    deadline = time.monotonic() + timeout
    while True:
        # 不能假设 sys.argv[0] 是 browse 自己——`lazyhelp install` 之类的调用方
        # 在同一个解释器里直接喊 wait_for_extension()，argv[0] 是调用方自己的
        # 薄壳路径。跟 lib/lazyhelp.py:show_full() 一样，靠 _resolve() 找真正的
        # browse 可执行文件：仓库内优先 bin/browse，装成包之后退回 PATH 里的
        # `browse`；它会自动把 bridge 拉起来。
        done = subprocess.run(
            [browse_bin, "browsingContext", "getTree", "--no-say"],
            capture_output=True,
        )
        if done.returncode == 0:
            return True
        if done.returncode != 3:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(CONNECT_POLL_SECONDS)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="browse install",
        description="构建扩展 + 指引加载（bridge 由任意一条 browse 指令自动拉起）",
    )
    parser.add_argument("--uninstall", action="store_true", help="清掉旧 native messaging 注册")
    parser.add_argument("--no-build", action="store_true",
                        help="不自动构建扩展（默认 dist/ 缺失时自动跑 npm run build）")
    parser.add_argument("--no-wait", action="store_true",
                        help="不等扩展连上就返回（默认等 120 秒）")
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    """`browse install` / `browse uninstall` 的实现。

    install 只做三件事：构建扩展、指引用户加载一次（Chrome 不允许任何程序替用户
    装扩展：--load-extension 于 137 移除、CDP Extensions.loadUnpacked 要
    browser-level target 而 136+ 拒绝对默认 profile 开调试端口）、等它连上 bridge。
    bridge 本身不用装 —— 任何一条 browse 指令都会自动把它拉起。

    `--no-say` / `--debug` / 计时都由 `lib/cli/browse.py` 的入口统一处理过了。
    """
    args = _parse(argv)
    out = reporter(stderr=True)
    home = pathlib.Path.home()
    plat = platform_key()

    if args.uninstall:
        removed = uninstall(home, plat)
        for name, where in removed:
            out.ok(f"{name}: 已删 {where}")
        if not removed:
            out.info("没有找到任何旧注册（本来就没装过 native messaging）")
        return EXIT_OK

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
    out.info("")
    out.info("加载后扩展自动连 bridge，不用重启浏览器。每个浏览器要各加载一次。")
    out.info("")
    for line in MANUAL_TOGGLES:
        out.info(line)
    out.info("")

    if args.no_wait or not sys.stderr.isatty():
        out.info("装完自己验：browse status")
        return EXIT_OK

    out.info(f"等你装上……（最多 {int(WAIT_TIMEOUT)} 秒，Ctrl-C 可中断）")
    try:
        connected = wait_for_extension(WAIT_TIMEOUT)
    except KeyboardInterrupt:
        out.info("没等到。装好后自己验：browse status")
        return EXIT_OK
    if connected:
        out.ok("扩展已连上 bridge，整条链路通了。试试：browse status")
        return EXIT_OK
    out.err("超时：扩展还没连上。排查顺序：")
    out.err("  1. chrome://extensions 里有没有看到这个扩展、是不是启用状态")
    out.err("  2. 扩展 ID 是不是 podeceeeafjdcemppcgjhhokcokpcama（不是的话 dist 里的 manifest key 被改过）")
    out.err("  3. 点扩展卡片上的 service worker，看控制台有没有报错（bridge 连接失败会打印原因）")
    out.err("  4. browse bridge start 之后重试")
    return EXIT_FAILED
