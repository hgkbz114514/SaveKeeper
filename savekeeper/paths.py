"""路径定位：环境变量展开、候选路径探测、Steam 安装目录与云存档扫描。

只读文件系统与注册表，不修改任何游戏文件。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

try:  # Windows only；其它平台静默降级
    import winreg  # type: ignore
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore


# --------------------------------------------------------------------------- #
# 基础路径工具
# --------------------------------------------------------------------------- #

def expand(pattern: str) -> Path:
    """展开 %VAR% / $VAR / ~ 并规范化。"""
    raw = os.path.expandvars(pattern)
    raw = os.path.expanduser(raw)
    return Path(os.path.normpath(raw))


def existing(patterns: Iterable[str]) -> list[Path]:
    """按顺序返回第一个真实存在的目录（去重）。"""
    seen: set[str] = set()
    out: list[Path] = []
    for p in patterns:
        path = expand(p)
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            out.append(path)
    return out


def open_in_explorer(path: Path) -> None:
    """用系统资源管理器打开目录（并选中）。"""
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606  # 本地工具，路径来自用户配置
    elif os.uname().sysname == "Darwin":  # pragma: no cover
        os.system(f'open "{path}"')
    else:  # pragma: no cover
        os.system(f'xdg-open "{path}"')


def human_size(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit == "B" else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} PB"


# --------------------------------------------------------------------------- #
# Steam：安装目录 / 库 / 云存档
# --------------------------------------------------------------------------- #

_STEAM_KEYS = (
    (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
)


def steam_root() -> Path | None:
    """从注册表读取 Steam 安装根目录。"""
    if winreg is None:
        return None
    for hive, sub, name in _STEAM_KEYS:
        try:
            with winreg.OpenKey(hive, sub) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            continue
        if value:
            root = Path(str(value).replace("/", "\\"))
            if (root / "steam.exe").exists() or (root / "userdata").is_dir():
                return root
    return None


def steam_libraries(root: Path) -> list[Path]:
    """解析 libraryfolders.vdf，返回全部游戏库目录。"""
    libs = [root]
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if not vdf.is_file():
        return libs
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return libs
    for match in re.finditer(r'"path"\s+"([^"]+)"', text):
        raw = match.group(1).replace("\\\\", "\\")
        lib = Path(raw)
        if lib.is_dir() and lib not in libs:
            libs.append(lib)
    return libs


def steam_cloud_saves(appids: dict[int, str]) -> dict[int, Path]:
    """扫描 <Steam>/userdata/*/<appid>/remote，返回 appid → 存档目录。

    很多 Steam 云同步游戏把整个存档目录放在 remote 下，
    这是比硬编码路径更可靠的自动检测来源。
    """
    root = steam_root()
    if root is None:
        return {}
    userdata = root / "userdata"
    if not userdata.is_dir():
        return {}
    found: dict[int, Path] = {}
    for user_dir in userdata.iterdir():
        if not user_dir.is_dir():
            continue
        for appid in appids:
            if appid in found:
                continue
            remote = user_dir / str(appid) / "remote"
            if remote.is_dir() and any(remote.iterdir()):
                found[appid] = remote
    return found


def installed_steam_games(libraries: list[Path]) -> dict[int, str]:
    """从 appmanifest_*.acf 中读取已安装 appid（备用探测手段）。"""
    out: dict[int, str] = {}
    for lib in libraries:
        apps = lib / "steamapps"
        if not apps.is_dir():
            continue
        for manifest in apps.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            appid = _vdf_field(text, "appid")
            name = _vdf_field(text, "name")
            if appid:
                out[int(appid)] = name or str(appid)
    return out


def _vdf_field(text: str, field: str) -> str | None:
    match = re.search(rf'"{field}"\s+"([^"]*)"', text)
    return match.group(1) if match else None
