"""新装游戏的存档目录自动发现 —— 内置库覆盖不到的游戏靠这里兜底。

内置库只有固定几十款，玩家新买的游戏基本都不在里面。这里用三条**不依赖
硬编码路径**的线索去反查，全部只读，绝不写游戏目录：

1. **Steam 云存档**：``<Steam>/userdata/<uid>/<appid>/remote``
   对任意 appid 都成立 —— 只要枚举 userdata 下所有 appid 目录，就能拿到
   每个开了 Steam 云同步游戏的存档位置，完全不用知道游戏叫什么。

2. **安装清单**：Steam ``appmanifest_*.acf`` / Epic ``*.item`` 清单 /
   注册表 Uninstall 项 —— 拿到「已安装游戏名 ↔ appid ↔ 安装目录」，
   这是把 appid 翻译成游戏名的桥梁。

3. **名字反查**：游戏名归一化（去空格/标点/版本后缀）后，到常见存档根目录
   （%APPDATA%、%LOCALAPPDATA%、LocalLow、Documents/My Games、Saved Games）
   下匹配同名文件夹。命中多个时按「是否含近期修改的存档文件」打分。

最后再加一条兜底：**最近存档嗅探** —— 扫描常见存档根目录下近 N 天内被
改过的、带存档扩展名的文件所在目录，按时间倒序给出候选，供人工确认。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import paths

try:  # Windows only
    import winreg  # type: ignore
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore


# 存档文件常见扩展名（用于给候选目录打分、以及最近存档嗅探）
SAVE_EXTS = frozenset({
    ".sav", ".save", ".savegame", ".srm", ".dsv", ".sl2", ".erp",
    ".dat", ".bin", ".profile", ".es3", ".rw3", ".ss", ".gs", ".ox",
    ".slot", ".zds", ".state", ".xml", ".json", ".db", ".sfs",
})

# 强特征扩展名：只有游戏存档会用，软件配置基本不用。
# "最近存档"嗅探要用这一套：AppData 下到处是 .json/.db/.xml，
# 用宽集合会把 Telegram/Claude/Doubao 的缓存目录全当成游戏存档。
_STRONG_SAVE_EXTS = frozenset({
    ".sav", ".save", ".savegame", ".srm", ".dsv", ".sl2", ".erp",
    ".es3", ".rw3", ".slot", ".zds", ".sfs", ".profile",
})

# 这些路径片段下出现存档目录，几乎可以断定是游戏（Unity / Unreal / 传统布局）
_GAME_SAVE_HINTS = ("locallow", "my games", "saved games", "savegames")

# 名字归一化时要丢掉的噪音词（版本 / 包装 / 商标）
_NOISE = (
    "the", "game", "of", "edition", "definitive", "deluxe", "ultimate",
    "complete", "remastered", "remake", "goty", "enhanced", "gold",
    "steam", "pc", "hd", "vr", "collectors", "premium", "standard",
    "tm", "r", "c",
)

# 注册表 Uninstall 项里混杂大量非游戏软件，靠这些词剔掉，
# 否则 Office / .NET Runtime / WinRAR 会被当成游戏去匹配存档目录。
_NOT_A_GAME = (
    "microsoft", "windows", ".net", "visual c++", "visual studio", "runtime",
    "redistributable", "update", "driver", "sdk", "library", "service pack",
    "office", "onedrive", "onenote", "outlook", "edge", "defender",
    "winrar", "7-zip", "clash", "chrome", "firefox", "python", "node",
    "java", "git", "npm", "docker", "vscode", "notepad", "everything",
    "驱动", "运行库", "更新", "补丁", "卸载", "组件", "插件",
    # 平台启动器不是游戏本体，存档路径不能按它算
    "launcher", "games launcher", "online services", "redlauncher",
    "epic games", "rockstar", "gog galaxy", "ubisoft connect", "ea app",
    "battle.net", "wegame", "start云游戏", "steam", "steamworks",
    # 常见桌面工具 / 通讯 / 远控，注册表里冒充不了游戏
    "qq", "wechat", "微信", "copilot", "todesk", "nvidia", "加速器",
    "telemetry", "container", "example finder", "multisim", "proteus",
    "labview", "ni ", "national instruments", "circuit design",
)


def _looks_like_game(name: str) -> bool:
    """注册表来源的名字过滤：命中非游戏关键词就丢掉。"""
    low = name.lower()
    return not any(word in low for word in _NOT_A_GAME)


# --------------------------------------------------------------------------- #
# 已安装游戏清单
# --------------------------------------------------------------------------- #

@dataclass
class InstalledGame:
    """一条「已安装游戏」记录，来源可能是 Steam / Epic / 注册表。"""

    name: str
    platform: str
    appid: int | None = None
    install_dir: Path | None = None
    source: str = ""

    def __str__(self) -> str:  # pragma: no cover - 仅调试用
        return f"{self.name} ({self.platform}{' ' + str(self.appid) if self.appid else ''})"


def scan_steam_installed() -> list[InstalledGame]:
    """扫描 Steam 各库的 appmanifest_*.acf，得到已安装游戏。"""
    root = paths.steam_root()
    if root is None:
        return []
    out: list[InstalledGame] = []
    for lib in paths.steam_libraries(root):
        apps = lib / "steamapps"
        if not apps.is_dir():
            continue
        for manifest in apps.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            appid_raw = _vdf(text, "appid")
            name = _vdf(text, "name")
            installdir = _vdf(text, "installdir")
            if not name:
                continue
            out.append(
                InstalledGame(
                    name=name,
                    platform="Steam",
                    appid=int(appid_raw) if appid_raw and appid_raw.isdigit() else None,
                    install_dir=(apps / "common" / installdir) if installdir else None,
                    source=str(manifest),
                )
            )
    return out


def scan_epic_installed() -> list[InstalledGame]:
    """扫描 Epic Games Launcher 的清单（ProgramData/Epic/.../*.item，JSON）。"""
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    if not base.is_dir():
        return []
    out: list[InstalledGame] = []
    for item in base.glob("*.item"):
        try:
            import json

            data = json.loads(item.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            continue
        name = data.get("DisplayName") or data.get("MandatoryAppFolderName")
        loc = data.get("InstallLocation")
        if not name:
            continue
        out.append(
            InstalledGame(
                name=str(name),
                platform="Epic",
                install_dir=Path(loc) if loc else None,
                source=str(item),
            )
        )
    return out


def scan_registry_installed(limit: int = 800) -> list[InstalledGame]:
    """扫描注册表 Uninstall 项，抓 GOG / 独立游戏 / 其它平台。"""
    if winreg is None:
        return []
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    out: list[InstalledGame] = []
    for hive, sub in keys:
        try:
            parent = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with parent:
            try:
                count = winreg.QueryInfoKey(parent)[0]
            except OSError:
                continue
            for i in range(min(count, limit)):
                try:
                    sub_name = winreg.EnumKey(parent, i)
                    with winreg.OpenKey(parent, sub_name) as k:
                        name = _reg_value(k, "DisplayName")
                        loc = _reg_value(k, "InstallLocation")
                except OSError:
                    continue
                if not name:
                    continue
                if not _looks_like_game(name):
                    continue
                out.append(
                    InstalledGame(
                        name=name,
                        platform="其它",
                        install_dir=Path(loc) if loc else None,
                        source=f"{sub}\\{sub_name}",
                    )
                )
    return out


def installed_games(include_registry: bool = True) -> list[InstalledGame]:
    """汇总所有来源，按名字去重（Steam 优先）。"""
    seen: set[str] = set()
    out: list[InstalledGame] = []
    sources: list[InstalledGame] = []
    sources += scan_steam_installed()
    sources += scan_epic_installed()
    if include_registry:
        sources += scan_registry_installed()
    for game in sources:
        key = _normalize(game.name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(game)
    return out


# --------------------------------------------------------------------------- #
# 名字归一化与匹配
# --------------------------------------------------------------------------- #

_NON_WORD = re.compile(r"[^0-9a-z]+")


def _normalize(name: str) -> str:
    """小写 + 只留字母数字 + 去掉版本噪音词。中文原样保留。"""
    text = name.lower().strip()
    text = _NON_WORD.sub(" ", text)
    parts = [p for p in text.split() if p and p not in _NOISE]
    return "".join(parts)


def _match_score(target: str, candidate: str) -> int:
    """0 = 不匹配；分数越高越像。"""
    return _match_normed(_normalize(target), _normalize(candidate))


def _match_normed(a: str, b: str) -> int:
    """已归一化名字之间的匹配打分（批量匹配时避免重复归一化）。

    严格性来自实际踩坑：早期版本只要「短名包含于长名」就算命中，
    结果 ``NVIDIA User Container`` 命中了 ``AppData/Code/User``、
    ``NI MAX`` 命中了 ``Wmpvp/config`` —— 通用目录名到处都在，
    必须要求两边都够长，或整名相等。
    """
    if not a or not b:
        return 0
    if a == b:
        return 100
    # 任一侧过短时只认整名相等，否则 "user"/"config"/"data" 会命中一切
    if len(a) < 6 or len(b) < 6:
        return 0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # 整词包含：短名至少 6 字符，且长度占比不能太小（防 "save" 命中 "savegamexyz"）
    if shorter in longer and len(shorter) / len(longer) >= 0.5:
        return 70
    if len(shorter) >= 8 and _ratio(shorter, longer) > 0.8:
        return 50
    return 0


def _ratio(a: str, b: str) -> float:
    """粗粒度相似度：公共前缀长度 / 较长者长度。"""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n / max(len(a), len(b))


# --------------------------------------------------------------------------- #
# 存档根目录与目录索引
# --------------------------------------------------------------------------- #

def save_roots() -> list[Path]:
    """常见的存档根目录（只取真实存在的）。"""
    patterns = [
        "%APPDATA%",
        "%LOCALAPPDATA%",
        "%LOCALAPPDATA%Low",
        "%USERPROFILE%/Documents/My Games",
        "%USERPROFILE%/Documents",
        "%USERPROFILE%/Saved Games",
        "%USERPROFILE%/AppData/Roaming",
    ]
    return paths.existing(patterns)


_INDEX_CACHE: dict[str, object] = {}


def _folder_index() -> list[tuple[str, Path, float, bool]]:
    """(名字, 路径, 最近修改时间, 是否含存档文件) 索引，扫两层，结果缓存。

    深度限制在 2 层：既能覆盖 ``LocalLow/Company/Game`` 这种布局，
    又不会把整个磁盘翻一遍。

    第四项 ``has_save`` 是预计算的：若每次匹配都去递归扫文件，
    260 款游戏 × 2000 个目录会跑到分钟级。
    """
    cached = _INDEX_CACHE.get("index")
    if cached is not None:
        return cached  # type: ignore[return-value]
    index: list[tuple[str, Path, float, bool]] = []
    seen: set[str] = set()
    for root in save_roots():
        for name, path, mtime in _walk_limited(root, max_depth=2):
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            index.append((name, path, mtime, _has_save_files(path)))
    _INDEX_CACHE["index"] = index
    return index


def _normed_full() -> list[tuple[str, str, Path, float, bool]]:
    """(原名, 归一化名, 路径, mtime, 含存档文件)。

    归一化名一次性算好缓存：260 款游戏 × 2000 个目录若每次都做正则替换，
    光这一项就要几十秒。
    """
    cached = _INDEX_CACHE.get("normed")
    if cached is not None:
        return cached  # type: ignore[return-value]
    normed = [(name, _normalize(name), path, mtime, has_save) for name, path, mtime, has_save in _folder_index()]
    _INDEX_CACHE["normed"] = normed
    return normed


def _walk_limited(root: Path, max_depth: int) -> Iterable[tuple[str, Path, float]]:
    """有界广度优先：只进深度 <= max_depth 的目录。"""
    try:
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        try:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        path = Path(entry.path)
                        yield entry.name, path, stat.st_mtime
                        if depth + 1 < max_depth:
                            stack.append((path, depth + 1))
            except OSError:
                continue
    except OSError:  # pragma: no cover
        return


def clear_cache() -> None:
    """清空索引缓存（用户在会话中装了新游戏后调用）。"""
    _INDEX_CACHE.clear()


def _has_save_files(path: Path, days: float | None = None, exts: frozenset[str] | None = None) -> bool:
    """目录内（递归 2 层）是否有存档类文件；给了 days 则要求近期修改过。

    ``exts`` 可换成更严格的集合：``_STRONG_SAVE_EXTS`` 用于"最近存档"嗅探，
    避免把 AppData 里成片的 .json/.db 配置目录当成游戏存档。
    """
    allowed = exts if exts is not None else SAVE_EXTS
    cutoff = (time.time() - days * 86400) if days else None
    try:
        stack: list[tuple[Path, int]] = [(path, 0)]
        while stack:
            cur, depth = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for entry in it:
                        try:
                            if entry.is_file(follow_symlinks=False):
                                if Path(entry.name).suffix.lower() not in allowed:
                                    continue
                                if cutoff is not None and entry.stat().st_mtime < cutoff:
                                    continue
                                return True
                            elif entry.is_dir(follow_symlinks=False) and depth + 1 < 2:
                                stack.append((Path(entry.path), depth + 1))
                        except OSError:
                            continue
            except OSError:
                continue
    except OSError:  # pragma: no cover
        pass
    return False


def _looks_like_game_save_dir(path: Path) -> bool:
    """本地目录是否像游戏存档目录。

    两条判据任一成立即可：
    ① 路径里带 ``LocalLow`` / ``My Games`` / ``Saved Games`` 这类游戏专属位置；
    ② 目录里有强特征存档扩展名（.sav/.sl2/.srm…）。

    没有这一层，``AppData`` 下每个带 .json 的软件目录都会进"最近存档"，
    实测会冒出 Telegram / Claude / 豆包的缓存目录，真正的游戏一条都看不见。
    """
    low = str(path).lower()
    if any(hint in low for hint in _GAME_SAVE_HINTS):
        return True
    return _has_save_files(path, exts=_STRONG_SAVE_EXTS)


# --------------------------------------------------------------------------- #
# Steam 云存档索引
# --------------------------------------------------------------------------- #

# Steam 客户端自用 appid，不是游戏，绝不能当存档目录推荐给用户。
#   7   = 服务器浏览器历史 + sharedconfig.vdf（Steam 自己的配置）
#   760 = 截图目录（remote/<appid>/screenshots）
#   228980 = Steamworks Common Redistributables
# 实测不加这个黑名单，"最近存档"第一名会变成 serverbrowser_hist.vdf。
_STEAM_RESERVED = frozenset({7, 760, 228980, 228990, 250820})

# 存档目录里出现这些文件名，说明是 Steam 自己的元数据，不是游戏存档
_NOT_SAVE_FILES = frozenset({
    "serverbrowser_hist.vdf", "sharedconfig.vdf", "config.vdf",
    "loginusers.vdf", "libraryfolders.vdf", "shortcuts.vdf",
})


def steam_cloud_index() -> dict[int, tuple[Path, float]]:
    """枚举 userdata 下**所有** appid 的 remote 目录 → appid: (路径, mtime)。

    这是最有价值的一条线索：不需要知道游戏名，也不需要内置库。
    会跳过 Steam 保留 appid 与明显不是存档的目录（如截图目录）。
    """
    cached = _INDEX_CACHE.get("cloud")
    if cached is not None:
        return cached  # type: ignore[return-value]
    out: dict[int, tuple[Path, float]] = {}
    root = paths.steam_root()
    if root is None:
        _INDEX_CACHE["cloud"] = out
        return out
    userdata = root / "userdata"
    if not userdata.is_dir():
        _INDEX_CACHE["cloud"] = out
        return out
    for user_dir in _safe_iterdir(userdata):
        for app_dir in _safe_iterdir(user_dir):
            if not app_dir.name.isdigit():
                continue
            appid = int(app_dir.name)
            if appid in _STEAM_RESERVED:
                continue
            for sub in (app_dir / "remote", app_dir):
                if not sub.is_dir():
                    continue
                try:
                    children = [Path(e.path) for e in os.scandir(sub)]
                except OSError:
                    continue
                if not children:
                    continue
                # 全是 Steam 自己的配置/截图时跳过，否则会当成游戏存档
                names = {p.name.lower() for p in children}
                if names and names <= _NOT_SAVE_FILES:
                    continue
                try:
                    mtime = sub.stat().st_mtime
                except OSError:
                    continue
                prev = out.get(appid)
                if prev is None or mtime > prev[1]:
                    out[appid] = (sub, mtime)
                break
    _INDEX_CACHE["cloud"] = out
    return out


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(path) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


# --------------------------------------------------------------------------- #
# 主入口：给一个游戏名 / appid，反查存档目录
# --------------------------------------------------------------------------- #

@dataclass
class DiscoveryResult:
    path: Path | None = None
    reason: str = ""
    confidence: str = "low"  # high / medium / low
    candidates: list[tuple[Path, int, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.path is not None


def discover_save_dir(
    game_name: str,
    appid: int | None = None,
    install_dir: Path | None = None,
    deep: bool = True,
) -> DiscoveryResult:
    """按优先级反查存档目录，返回结果与判定依据。

    永远不抛异常：任何一步失败都只是降低置信度。
    """
    # ① Steam 云存档：有 appid 就直接命中，最可靠
    if appid:
        cloud = steam_cloud_index()
        if appid in cloud:
            path, _mtime = cloud[appid]
            return DiscoveryResult(path, f"Steam 云存档目录（appid {appid}）", "high")

    if not deep:
        return DiscoveryResult(None, "仅查了 Steam 云存档，未命中")

    # ② 名字反查存档根目录
    target_norm = _normalize(game_name)
    scored: list[tuple[int, Path, str]] = []
    for name, norm, path, _mtime, has_save in _normed_full():
        if not has_save:
            continue
        score = _match_normed(target_norm, norm)
        if not score:
            continue
        # 含近期改动 +30 分，优先选正在用的那个存档目录
        if _has_save_files(path, days=30):
            score += 30
        scored.append((score, path, name))
    scored.sort(key=lambda x: (-x[0], str(x[1])))

    if scored:
        best_score, best_path, best_name = scored[0]
        confidence = "high" if best_score >= 130 else ("medium" if best_score >= 70 else "low")
        return DiscoveryResult(
            best_path,
            f"按名字匹配到目录「{best_name}」（匹配度 {best_score}）",
            confidence,
            [(p, s, f"匹配「{n}」") for s, p, n in scored[:5]],
        )

    # ③ 安装目录旁找存档（部分游戏把存档放在安装目录内）
    if install_dir and install_dir.is_dir():
        for sub in ("save", "saves", "Save", "Saves", "savegames", "SaveGames", "profile"):
            cand = install_dir / sub
            if cand.is_dir() and _has_save_files(cand):
                return DiscoveryResult(cand, f"安装在 {install_dir.name} 目录内的 save 子目录", "medium")

    # ④ 最近存档嗅探：给出候选，让用户自己确认
    #    注意：这一步会遍历整个索引递归扫文件，必须走缓存。
    #    早期版本每款未命中的游戏都跑一次，147 款游戏 × 2095 目录
    #    约 30 万次文件系统递归，单次扫描拖到 20 秒以上。
    recent = _recent_cached(14, 5)
    if recent:
        return DiscoveryResult(
            None,
            "没有可靠匹配，下面是最近改动过的存档目录，请人工确认",
            "low",
            [(p, 0, f"{d} 天前改动") for p, d in recent],
        )
    return DiscoveryResult(None, "未找到")


def _recent_cached(days: float, limit: int) -> list[tuple[Path, int]]:
    """recent_save_dirs 的缓存包装：整个扫描会话只算一次。"""
    key = f"recent:{days}"
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    rows = recent_save_dirs(days=days, limit=limit)
    _INDEX_CACHE[key] = rows
    return rows


# 明显不是存档的目录名片段（浏览器缓存 / 临时文件 / 遥测）
_JUNK_DIR = (
    "temp", "tmp", "cache", "cache2", "code cache", "gpucache", "ebwebview",
    "user data", "crashpad", "logs", "log", "telemetry", "updater", "update",
    "installer", "backup", "blob_storage", "indexeddb", "service worker",
    "shadercache", "gpu", "dawncache", "webcache", "component",
)


def _is_junk_dir(path: Path) -> bool:
    """路径任一层命中缓存/临时关键词就判定为垃圾目录。

    只看最后一级不够：``Temp/hindsight-dsh`` 的最后一级不含 temp，
    但父级是 Temp —— 早期版本因此把大量临时目录当成"最近存档"。
    """
    for part in path.parts:
        low = part.lower()
        if low in ("temp", "tmp"):
            return True
        if any(word in low for word in _JUNK_DIR):
            return True
    return False


def recent_save_dirs(days: float = 14, limit: int = 10) -> list[tuple[Path, int]]:
    """扫描常见根目录，找出近 N 天内有存档文件改动的目录。"""
    return [(path, ago) for path, ago, _hint, _kind in recent_activity(days=days, limit=limit)]


def recent_activity(days: float = 14, limit: int = 10) -> list[tuple[Path, int, str, str]]:
    """近 N 天有改动的存档目录 → (路径, 多少天前, 游戏名提示, 来源)。

    合并两个互不覆盖的来源：

    1. **Steam 云存档**（``userdata/<uid>/<appid>``）—— 刚下好、玩过一次的新游戏
       绝大多数落在这里，而它**不在** ``save_roots()`` 的扫描范围里。早期版本
       只扫 AppData/文档，结果全是软件缓存，真正的游戏存档一条都出不来。
    2. 常见存档根目录（AppData / Documents/My Games / Saved Games …）。

    来源 1 还能直接反查出游戏名（acf 清单的 name 字段），比让用户对着
    一串 GUID 目录猜有用得多。
    """
    cutoff = time.time() - days * 86400
    now = time.time()
    hits: list[tuple[Path, float, str, str]] = []
    seen: set[str] = set()

    # ① Steam 云存档：带 appid → 游戏名
    cloud = steam_cloud_index()
    if cloud:
        appid_names = {g.appid: g.name for g in installed_games(include_registry=False) if g.appid}
        for appid, (path, mtime) in cloud.items():
            if mtime < cutoff:
                continue
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            hint = appid_names.get(appid) or f"appid {appid}"
            hits.append((path, mtime, hint, "Steam 云存档"))

    # ② 常见存档根目录（只收真像游戏存档的，软件缓存一律不进）
    for _name, path, mtime, has_save in _folder_index():
        if not has_save or mtime < cutoff:
            continue
        if _is_junk_dir(path):
            continue
        key = str(path).lower()
        if key in seen:
            continue
        if not _looks_like_game_save_dir(path):
            continue
        if not _has_save_files(path, days=days):
            continue
        seen.add(key)
        hits.append((path, mtime, "", "本地目录"))

    hits.sort(key=lambda x: x[1], reverse=True)
    return [(p, int((now - m) / 86400), hint, kind) for p, m, hint, kind in hits[:limit]]


def discover_for_installed(limit: int = 200) -> list[tuple[InstalledGame, DiscoveryResult]]:
    """对全部已安装游戏跑一遍发现，供「扫描已安装游戏」按钮使用。"""
    out: list[tuple[InstalledGame, DiscoveryResult]] = []
    for game in installed_games()[:limit]:
        out.append((game, discover_save_dir(game.name, game.appid, game.install_dir)))
    return out


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _vdf(text: str, field: str) -> str | None:
    match = re.search(rf'"{field}"\s+"([^"]*)"', text)
    return match.group(1) if match else None


def _reg_value(key, name: str) -> str | None:
    try:
        value, _ = winreg.QueryValueEx(key, name)  # type: ignore[union-attr]
    except OSError:
        return None
    return str(value) if value else None
