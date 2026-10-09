"""viewer-reveal — viewer 扩展的「在资源管理器打开」native messaging host

浏览器扩展不能起系统程序，reveal（Finder / 资源管理器 / xdg-open）只能由一个
native host 代跑：浏览器起这个进程，喂一条 JSON，它跑完回一条就退。装没装由
`viewer-reveal install` 决定，没装时扩展按钮会把失败原因写在按钮上。

子命令：
- ``host``：native 协议入口，浏览器调的，人不用碰
- ``install`` / ``uninstall`` / ``status``：注册/注销/查看 host 清单（用户级，按浏览器）

路径表按官方文档维护（developer.chrome.com/docs/extensions/develop/concepts/
native-messaging 与 developer.mozilla.org/Add-ons/WebExtensions/Native_manifests），
与 ``lib/browse_install.py`` 那张是同一来源；这里单独放一份小的是为了不把
``browse_service`` 的导入链拖进每个命令。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

from lib.fire_base import BaseCli, run_cli, timed_cli

# native messaging 的 host 名，扩展侧 chrome.runtime.sendNativeMessage 用同一个。
HOST_NAME = "com.lazygophers.viewer_reveal"

# 扩展 ID 由 manifest 的 `key` 字段算出（Chromium `components/crx_file/id_util.cc`），
# 与安装目录无关——manifest 里加了固定 key，这里就能写死。
CHROMIUM_EXTENSION_ID = "nhbbpopjdiagfdhhigggbnadpkgkheke"

# Firefox 侧是 gecko.id（viewer manifest 的 browser_specific_settings）。
GECKO_ID = "viewer@lazygophers.com"

# 平台 -> 浏览器 -> (清单风味, 落点目录，相对 home)。落点存在才写（探测浏览器装没装）。
# Windows 的 chromium 系读注册表 HKCU\...\NativeMessagingHosts，单独走 reg.exe。
BROWSERS: dict[str, dict[str, tuple[str, str]]] = {
    "darwin": {
        "chrome": ("chromium", "Library/Application Support/Google/Chrome/NativeMessagingHosts"),
        "chromium": ("chromium", "Library/Application Support/Chromium/NativeMessagingHosts"),
        "edge": ("chromium", "Library/Application Support/Microsoft Edge/NativeMessagingHosts"),
        "firefox": ("gecko", "Library/Application Support/Mozilla/NativeMessagingHosts"),
    },
    "linux": {
        "chrome": ("chromium", ".config/google-chrome/NativeMessagingHosts"),
        "chromium": ("chromium", ".config/chromium/NativeMessagingHosts"),
        "edge": ("chromium", ".config/microsoft-edge/NativeMessagingHosts"),
        "firefox": ("gecko", ".mozilla/native-messaging-hosts"),
    },
}

# Windows 注册表键（HKCU，用户级），<host> 处填 HOST_NAME。
_WIN_REG = r"HKCU\Software\Google\Chrome\NativeMessagingHosts"


def host_executable() -> pathlib.Path:
    """host 脚本的绝对路径：PATH 上的优先（uvx 用户），其次仓库里的 bin/viewer-reveal。"""
    found = shutil.which("viewer-reveal")
    if found is not None:
        return pathlib.Path(found).resolve()
    repo = pathlib.Path(__file__).resolve().parents[2] / "bin" / "viewer-reveal"
    return repo


def reveal(path: str) -> str:
    """在系统文件管理器里定位 ``path``（目录就打开它，文件就打开所在目录并选中）。

    三平台各用各的打开方式；``open -R`` / ``explorer /select,`` 带选中，
    xdg-open 没有统一选中语义，只开所在目录。
    """
    target = pathlib.Path(path)
    if not target.exists():
        raise FileNotFoundError(path)
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(target)], check=True)
    elif sys.platform == "win32":
        # explorer 的 /select 只吃反斜杠；explorer 自己几乎永远返回非零，不 check。
        win = str(target.resolve()).replace("/", "\\")
        argv = ["explorer", win] if target.is_dir() else ["explorer", f"/select,{win}"]
        subprocess.run(argv, check=False)
    else:
        opener = target if target.is_dir() else target.parent
        subprocess.run(["xdg-open", str(opener)], check=True)
    return f"已在文件管理器打开 {path}"


def _host() -> int:
    """native messaging 协议：读一条 4 字节长度前缀的 JSON，跑完回一条就退。"""
    size = int.from_bytes(sys.stdin.buffer.read(4), "little")
    request = json.loads(sys.stdin.buffer.read(size))
    try:
        message = reveal(str(request["path"]))
        reply: dict[str, object] = {"ok": True, "message": message}
    except Exception as exc:  # noqa: BLE001 - host 的职责就是把任何失败带回去
        reply = {"ok": False, "error": str(exc)}
    payload = json.dumps(reply, ensure_ascii=False).encode()
    sys.stdout.buffer.write(len(payload).to_bytes(4, "little") + payload)
    sys.stdout.buffer.flush()
    return 0


class ViewerRevealCli(BaseCli):
    """viewer 扩展的「在资源管理器打开」native host 管理"""

    @timed_cli
    def host(self):
        """native 协议入口（浏览器调的）"""
        return _host()

    @timed_cli
    def install(self):
        """把 host 清单写进检测到的浏览器（用户级，无需 sudo）"""
        self._r.ok(str(self._install()))
        return 0

    @timed_cli
    def uninstall(self):
        """清掉所有 host 清单，不留残留"""
        for dest in self._destinations().values():
            (dest / f"{HOST_NAME}.json").unlink(missing_ok=True)
        self._r.ok("已清理")
        return 0

    @timed_cli
    def status(self):
        """看装没装：每个浏览器一行"""
        exe = host_executable()
        self._r.info(f"host 脚本: {exe}（{'存在' if exe.exists() else '不存在'}）")
        for browser, dest in sorted(self._destinations().items()):
            installed = (dest / f"{HOST_NAME}.json").exists()
            self._r.status("ok" if installed else "skip", f"{browser}: {dest}")
        if sys.platform == "win32":
            self._r.info(f"Windows 注册表键请自查: {_WIN_REG}\\{HOST_NAME}")
        return 0

    # ---- 内部 ----

    def _destinations(self) -> dict[str, pathlib.Path]:
        """落点只列「浏览器真的装了」的：探测目录（NativeMessagingHosts 的上一级）存在才算。"""
        found: dict[str, pathlib.Path] = {}
        for browser, (_flavor, rel) in BROWSERS.get(sys.platform, {}).items():
            dest = pathlib.Path.home() / rel
            if dest.parent.exists():
                found[browser] = dest
        return found

    def _install(self) -> int:
        exe = host_executable()
        if not exe.exists():
            raise FileNotFoundError(f"host 脚本不存在: {exe}")
        written: list[str] = []
        for browser, dest in self._destinations().items():
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{HOST_NAME}.json").write_text(
                json.dumps(self._manifest(browser), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            written.append(browser)
        return len(written)

    @staticmethod
    def _manifest(browser: str) -> dict[str, object]:
        flavor = BROWSERS[sys.platform][browser][0]
        base: dict[str, object] = {
            "name": HOST_NAME,
            "description": "lazygophers viewer — 在系统文件管理器里定位文件/文件夹",
            "path": str(host_executable()),
            "type": "stdio",
        }
        if flavor == "chromium":
            base["allowed_origins"] = [f"chrome-extension://{CHROMIUM_EXTENSION_ID}/"]
        else:
            base["allowed_extensions"] = [GECKO_ID]
        return base


def main():
    run_cli(ViewerRevealCli())
