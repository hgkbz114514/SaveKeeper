"""从**游戏本体目录**反推存档位置。

为什么需要这个模块
------------------
``discover.py`` 依赖 Steam/Epic 清单与内置游戏库。但用户手上常见的还有：

- 绿色免安装版 / 直接复制的游戏目录；
- 非 Steam 平台（Epic 之外的独立发行、WeGame、GOG 离线包）；
- 破解版 —— 这类**没有 appmanifest.acf**，但它照样会在
  ``%LOCALAPPDATA%`` / ``%APPDATA%`` / ``LocalLow`` 下写存档。

所以不能靠"游戏名 → 内置表"，只能靠**读懂游戏本体自己**：

1. **引擎特征**（最可靠）：Unity 有 ``*_Data/``，UE 有 ``Binaries/Win64`` 或
   ``Engine/``，Godot 有 ``*.pck``，GameMaker 有 ``data.win``，
   RPG Maker MV 有 ``www/`` + ``js/``，Ren'Py 有 ``renpy/``。
   每种引擎的存档落点是有定式的（见 ``ENGINE_RULES``）。
2. **PE 版本信息**：exe 的 ``CompanyName`` / ``ProductName`` 正是引擎拼存档
   目录时用的字符串（Unity 用它拼 ``LocalLow/<Company>/<Product>``）。
3. **安装目录内的存档文件夹**：不少日式/欧美游戏把存档直接放本体里
   （``savedata/`` ``win64_save/`` ``savegames/`` ……），实测命中率很高。
4. **steam_appid.txt / 破解 ini**：有 appid 就能走 Steam 云存档那条精确链路。

四个线索交叉，任意一条命中就能给出路径，且**每条都写清依据**，
让用户能判断对不对 —— 而不是甩一个猜出来的路径。
"""

from __future__ import annotations

import configparser
import ctypes
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

# 游戏本体里常见的"存档文件夹"名字（大小写不敏感，去空格比较）
SAVE_DIR_NAMES = frozenset({
    "save", "saves", "savedata", "savedatas", "savegame", "savegames",
    "save data", "save_data", "save-data", "saved games", "savedgames",
    "saved", "userdata", "user data", "profile", "profiles", "playerdata",
    "player data", "slot", "slots", "win64_save", "savedata_backup",
    "storage", "backup",
})

# 这些目录名**太泛**，即使里面有存档文件也不能单独作为结论
_TOO_GENERIC = frozenset({"storage", "backup", "saved", "profile", "profiles", "slot", "slots"})

_ENGINE_MARKERS = {
    "unity": ("unityplayer.dll", "unitycrashhandler64.exe", "unitycrashhandler32.exe"),
    "unreal": (),  # 用目录结构判断，见 detect_engine
    "godot": (),   # *.pck
    "gamemaker": (),  # data.win
    "rpgmaker": (),  # www/js
    "renpy": (),   # renpy/
}

# 各引擎的存档落点模板。{company} {product} {name} {appid} 会被替换。
ENGINE_RULES: dict[str, tuple[str, ...]] = {
    # Unity: Application.persistentDataPath = LocalLow/<Company>/<Product>
    "unity": (
        "%USERPROFILE%/AppData/LocalLow/{company}/{product}",
        "%USERPROFILE%/AppData/LocalLow/{product}",
        "%USERPROFILE%/AppData/LocalLow/{company}/{name}",
    ),
    # UE4/UE5: FPaths::ProjectSavedDir() = LocalAppData/<Project>/Saved/SaveGames
    "unreal": (
        "%LOCALAPPDATA%/{product}/Saved/SaveGames",
        "%LOCALAPPDATA%/{name}/Saved/SaveGames",
        "%LOCALAPPDATA%/{product}/Saved",
        "%LOCALAPPDATA%/{name}/Saved",
    ),
    # Godot: user:// = AppData/Roaming/Godot/app_userdata/<name>
    "godot": (
        "%APPDATA%/Godot/app_userdata/{name}",
        "%APPDATA%/Godot/app_userdata/{product}",
    ),
    # RPG Maker MV/MZ (NW.js): 常在本体 www/save 或 Local/<name>
    "rpgmaker": (
        "%LOCALAPPDATA%/{name}/save",
        "%LOCALAPPDATA%/{product}/save",
        "%APPDATA%/{name}/save",
    ),
    # Ren'Py: %APPDATA%/RenPy/<name>
    "renpy": (
        "%APPDATA%/RenPy/{name}",
        "%APPDATA%/RenPy/{product}",
    ),
    # GameMaker Studio: %LOCALAPPDATA%/<name>
    "gamemaker": (
        "%LOCALAPPDATA%/{name}",
        "%LOCALAPPDATA%/{product}",
    ),
}

# 破解组 / 通用 ini 里可能出现 appid 的键
_APPID_KEYS = ("appid", "app_id", "steamappid", "steam_appid", "gameid")

# 可能是"本体 exe"的名字特征（排除这些明显不是主程序的）
_NOT_MAIN_EXE = (
    "unins", "setup", "crash", "report", "handler", "launcher_installer",
    "vcredist", "dxsetup", "dotnet", "activation", "register", "eulaupdate",
    "unitycrashhandler", "fmod", "easyanticheat", "be_service", "battleye",
)


# --------------------------------------------------------------------------- #
# PE 版本信息（纯 ctypes，零依赖）
# --------------------------------------------------------------------------- #

class _VSFIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def pe_version_info(exe: Path) -> dict[str, str]:
    """读 exe 的版本资源 → {'CompanyName': ..., 'ProductName': ...}。

    用 ``version.dll`` 的 VerQueryValueW，不依赖 pywin32 / pefile。
    读不到就返回空字典（不能因为一个 exe 没版本资源就中断整个探测）。
    """
    out: dict[str, str] = {}
    try:
        ver = ctypes.WinDLL("version")
    except OSError:  # pragma: no cover - 非 Windows
        return out
    try:
        size = ver.GetFileVersionInfoSizeW(str(exe), None)
        if not size:
            return out
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(exe), 0, size, buf):
            return out

        ptr = ctypes.c_void_p()
        length = ctypes.c_uint()
        translations: list[tuple[int, int]] = []
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)):
            count = length.value // 4
            arr = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_ushort))
            translations = [(arr[i * 2], arr[i * 2 + 1]) for i in range(count)]
        if not translations:
            translations = [(0x0409, 0x04B0)]

        for lang, codepage in translations:
            for key in ("CompanyName", "ProductName", "FileDescription", "InternalName", "OriginalFilename"):
                sub = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}"
                if not ver.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)):
                    continue
                if not ptr or not length.value:
                    continue
                try:
                    value = ctypes.wstring_at(ptr, length.value).rstrip("\x00").strip()
                except (ValueError, OSError):
                    continue
                if value:
                    out.setdefault(key, value)
            if out:
                break
    except OSError:
        return out
    return out


# --------------------------------------------------------------------------- #
# 从本体读 appid（破解版也常常留着）
# --------------------------------------------------------------------------- #

def read_steam_appid(install_dir: Path) -> tuple[int | None, str]:
    """从本体里找 Steam appid → (appid, 依据)。

    覆盖面（按可靠性排序）：

    1. ``steam_appid.txt`` —— 官方 SDK 要求放在 exe 旁边的文件，破解版通常也保留；
    2. ``steam_settings/steam_appid.txt`` —— Goldberg 模拟器的布局；
    3. 各种破解组 ini 里的 ``AppId=``（CODEX / RUNE / EMPRESS / TENOKE / FLT…）；
    4. ``steam_api*.dll`` 同名 ini。

    找不到就返回 ``(None, "")``。
    """
    # ① 官方 steam_appid.txt
    for rel in ("steam_appid.txt", "steam_settings/steam_appid.txt"):
        f = install_dir / rel
        if f.is_file():
            appid = _parse_appid_text(f)
            if appid:
                return appid, f"本体内的 {rel}"

    # ② 破解组 ini
    try:
        entries = list(os.scandir(install_dir))
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
        except OSError:
            continue
        low = entry.name.lower()
        if not low.endswith(".ini"):
            continue
        path = Path(entry.path)
        appid = _parse_appid_ini(path)
        if appid:
            return appid, f"本体内的 {entry.name}（破解/模拟器配置）"
    return None, ""


def _parse_appid_text(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    match = re.search(r"\b(\d{3,8})\b", text)
    return int(match.group(1)) if match else None


def _parse_appid_ini(path: Path) -> int | None:
    """不依赖 configparser 严格的段落语法，直接按行扫 key=value。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#", "[")):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip().lower().replace(" ", "") in _APPID_KEYS:
            value = value.strip().strip('"')
            if value.isdigit() and 3 <= len(value) <= 8:
                return int(value)
    return None


# --------------------------------------------------------------------------- #
# 引擎识别
# --------------------------------------------------------------------------- #

@dataclass
class EngineInfo:
    name: str = ""            # unity / unreal / godot / gamemaker / rpgmaker / renpy / unknown
    evidence: str = ""        # 人话依据，直接进 UI
    exe: Path | None = None   # 猜测的主程序
    company: str = ""
    product: str = ""


def _safe_dirs(path: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(path) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []


def _safe_files(path: Path) -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(path) if e.is_file(follow_symlinks=False)]
    except OSError:
        return []


def guess_main_exe(install_dir: Path, max_depth: int = 2) -> Path | None:
    """挑一个最像主程序的 exe：优先本体根目录、体积大、名字不含 unins/crash。"""
    best: tuple[int, Path] | None = None
    stack: list[tuple[Path, int]] = [(install_dir, 0)]
    while stack:
        cur, depth = stack.pop()
        for f in _safe_files(cur):
            low = f.name.lower()
            if not low.endswith(".exe"):
                continue
            if any(bad in low for bad in _NOT_MAIN_EXE):
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            # 体积分 + 越浅越好
            score = size // 1024 - depth * 4096
            if best is None or score > best[0]:
                best = (score, f)
        if depth + 1 < max_depth:
            for d in _safe_dirs(cur):
                stack.append((d, depth + 1))
    return best[1] if best else None


def detect_engine(install_dir: Path) -> EngineInfo:
    """按目录结构与标志文件识别引擎。"""
    dirs = {d.name.lower(): d for d in _safe_dirs(install_dir)}
    files = {f.name.lower(): f for f in _safe_files(install_dir)}

    # Unity：*_Data/ 或 UnityPlayer.dll
    unity_data = [d for k, d in dirs.items() if k.endswith("_data") and d.is_dir()]
    if unity_data or "unityplayer.dll" in files:
        info = EngineInfo("unity", "Unity（_Data 目录 / UnityPlayer.dll）")
        if unity_data:
            info.product = unity_data[0].name[: -len("_data")]
        return _fill_version(info, install_dir)

    # Unreal：Engine/ 或 */Binaries/Win64
    if "engine" in dirs:
        return _fill_version(EngineInfo("unreal", "Unreal Engine（Engine 目录）"), install_dir)
    for sub in _safe_dirs(install_dir):
        if (sub / "Binaries" / "Win64").is_dir() or (sub / "Binaries").is_dir():
            info = EngineInfo("unreal", f"Unreal Engine（{sub.name}/Binaries）")
            info.product = sub.name
            return _fill_version(info, install_dir)
    if (install_dir / "Binaries" / "Win64").is_dir():
        return _fill_version(EngineInfo("unreal", "Unreal Engine（Binaries/Win64）"), install_dir)

    # GameMaker
    if "data.win" in files:
        return _fill_version(EngineInfo("gamemaker", "GameMaker（data.win）"), install_dir)

    # Godot
    if any(k.endswith(".pck") for k in files):
        return _fill_version(EngineInfo("godot", "Godot（*.pck）"), install_dir)

    # RPG Maker MV/MZ：www/ 里有 js/ 与 data/
    if "www" in dirs or (install_dir / "js" / "rpg_core.js").is_file():
        return _fill_version(EngineInfo("rpgmaker", "RPG Maker MV/MZ（www 目录）"), install_dir)

    # Ren'Py
    if "renpy" in dirs or (install_dir / "game" / "renpy").is_dir():
        return _fill_version(EngineInfo("renpy", "Ren'Py（renpy 目录）"), install_dir)

    # 兜底：用 exe 的 ProductName 判断
    info = _fill_version(EngineInfo("", ""), install_dir)
    return info


def _fill_version(info: EngineInfo, install_dir: Path) -> EngineInfo:
    """补 exe / company / product（版本资源是免费且高价值的元数据）。"""
    exe = guess_main_exe(install_dir)
    info.exe = exe
    if exe is not None:
        ver = pe_version_info(exe)
        if ver:
            info.company = ver.get("CompanyName", "") or ""
            info.product = info.product or ver.get("ProductName", "") or ""
            if not info.name and "unity" in (ver.get("FileDescription", "") + ver.get("InternalName", "")).lower():
                info.name = "unity"
            if not info.name and "unreal" in ver.get("FileDescription", "").lower():
                info.name = "unreal"
    if not info.product:
        info.product = install_dir.name
    if not info.name:
        info.name = "unknown"
    return info


# --------------------------------------------------------------------------- #
# 本体内的存档文件夹
# --------------------------------------------------------------------------- #

def save_dirs_in_install(install_dir: Path, max_depth: int = 3) -> list[tuple[Path, str]]:
    """在本体内部找存档文件夹 → [(路径, 依据)]。

    实测价值很高：``Monster Hunter World/savedata_backup``、
    ``Devil May Cry 5/win64_save`` 这类目录直接就在本体里。

    排序不能只看"名字像不像 + 有没有存档文件"：实测 ``Hogwarts Legacy``
    与 ``Outer Wilds`` 根目录下都有 ``savedata/save0121.dat``，两者 SHA-256
    完全相同（23 KB 的模板占位档），而真正在写的是 ``game/saves/`` 下
    十几个 250–310 KB 的 ``*.save``。只看有无文件会让占位档排第一，
    所以把**存档数量与总体量**也算进分数。
    """
    hits: list[tuple[Path, str, tuple[int, ...]]] = []
    stack: list[tuple[Path, int]] = [(install_dir, 0)]
    while stack:
        cur, depth = stack.pop()
        for d in _safe_dirs(cur):
            key = d.name.lower().strip()
            if key in SAVE_DIR_NAMES:
                files = _safe_files(d)
                # 空目录不算（很多游戏装了但还没玩过）
                if not files and not _safe_dirs(d):
                    continue
                strength, nfiles, nbytes = _scan_save_stats(d)
                has_save = strength > 0
                strong = key not in _TOO_GENERIC
                # 排序键（从主到次）：
                #   1. 目录名够具体（saves/win64_save 优于 backup/storage）
                #   2. 内容真的是存档
                #   3. 存档文件多（占位档通常只有 1 个）
                #   4. 总体量大（占位档通常几十 KB，真存档几百 KB 起步）
                #   5. 层级浅
                score = (
                    2 if strong else 0,
                    1 if has_save else 0,
                    min(nfiles, 20),
                    min(nbytes // 65536, 50),  # 每 64 KB 记一分，封顶 50
                    -depth,
                )
                reason = f"本体内的 {d.name}/ 目录"
                if has_save:
                    reason += f"（{nfiles} 个存档文件，{nbytes / 1024:.0f} KB）"
                hits.append((d, reason, score))
            if depth + 1 < max_depth:
                stack.append((d, depth + 1))
    hits.sort(key=lambda x: x[2], reverse=True)
    return [(p, r) for p, r, _s in hits]


# 常见存档扩展名。"强证据"是那些几乎只用于存档的，"弱证据"是 .json/.dat/.xml
# 这类配置与日志也大量使用的 —— 二者混在一起会直接导致误报：
#   · AppData\Local\Rockstar Games\Red Dead Redemption 2 只有 CrashLogs\settings.dat
#   · AppData\Local\CD Projekt Red\Cyberpunk 2077 只有 UserSettings.json
# 真存档其实在 Documents\...\Profiles 与 Saved Games\...\AutoSave-*，
# 早期版本把上面两个 crash/配置目录当成了存档目录。
_STRONG_SAVE_EXTS = frozenset({
    ".sav", ".save", ".savegame", ".srm", ".dsv", ".sl2", ".erp", ".es3",
    ".rw3", ".slot", ".zds", ".sfs", ".state", ".sav0", ".sgd",
})
_WEAK_SAVE_EXTS = frozenset({".dat", ".bin", ".profile", ".json", ".xml"})

_SAVE_EXTS = _STRONG_SAVE_EXTS | _WEAK_SAVE_EXTS

# 无扩展名的存档：很多日式/主机移植游戏直接用 SAVEDATA1000、SAVE01 这类名字。
# 实测「Monster Hunter World/savedata_backup/SAVEDATA1000」就属于这类，
# 早期版本只认扩展名，把它整个漏掉了。
_NO_EXT_SAVE = re.compile(r"^(save|savedata|profile|user|slot|data|dat|game|player)\d*$", re.I)

# 「AutoSave-0」「ManualSave-12」这类没有扩展名的档位目录（实测 CP2077）
_SLOT_DIR = re.compile(r"^(auto|manual|quick)?saves?[-_ ]?\d+$", re.I)

# 文件名里带这些词，即使是 .json/.dat 也算强证据：
# 实测 ``Scam Line/save.json``、``Hogwarts Legacy`` 的 ``*.sav`` 都是这样。
# 反过来说 ``UserSettings.json`` / ``settings.dat`` 不带这些词，仍算弱。
_SAVE_NAME_HINT = re.compile(r"(save|存档|slot|profile|progress|checkpoint)", re.I)

# 崩溃转储 / 日志目录：名字再像也不需要推荐给用户。
# 实测 ``AppData\Local\Rockstar Games\Red Dead Redemption 2\CrashLogs\settings.dat``
# 就靠这条被压到候选末尾，让 ``Documents\Rockstar Games\...`` 排在前面。
_JUNK_PATH = re.compile(r"(crash|crashlog|dmp|log|telemetry|diagnostic|dump)", re.I)

# Steam 会自动往它云同步的存档目录里塞一个 steam_autocloud.vdf。
# 文件本身就等于"这个目录被 Steam 当成存档同步" —— 比扩展名可靠得多：
# 实测 ``AppData\Local\Draw&Guess`` 里全是 .dgm/.dgwl 这类专有扩展名，
# 按扩展名判断完全认不出来，但那个 vdf 在，就能确定是存档目录。
_STEAM_CLOUD_MARK = "steam_autocloud.vdf"


def _looks_like_save_file(name: str) -> bool:
    """文件名是否像存档（含无扩展名的情况）。"""
    low = name.lower()
    if "." in low:
        if Path(low).suffix in _SAVE_EXTS:
            return True
        return False
    return bool(_NO_EXT_SAVE.match(low)) or low.isdigit()


def _scan_save_stats(path: Path, max_depth: int = 3) -> tuple[int, int, int]:
    """递归统计存档证据 → (强度 0/1/2, 存档文件数, 存档总字节)。

    强度口径与 :func:`_save_strength` 完全一致（它是本函数的薄封装），
    额外返回**数量与体量**，因为只看"有没有存档文件"根本不够：

    实测 ``Hogwarts Legacy/savedata`` 与 ``Outer Wilds/savedata`` 里各有一个
    ``save0121.dat``，两者 SHA-256 一模一样（23 KB 的模板占位档），而真正的
    存档在 ``game/saves`` 下，是十几个 250–310 KB 的 ``*.save`` —— 名字像、
    扩展名像、都含存档文件，只有数量和体量能把占位档和真存档分开。
    """
    if not path.is_dir():
        return (0, 0, 0)
    strong = weak = 0
    slot_dirs = 0
    nfiles = 0
    nbytes = 0
    cloud_marked = False
    stack: list[tuple[Path, int]] = [(path, 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            low = entry.name.lower()
                            suffix = Path(low).suffix
                            # Steam 云同步标记：直接认定这个目录就是存档目录
                            if low == _STEAM_CLOUD_MARK:
                                cloud_marked = True
                                continue
                            # 崩溃转储 / 日志文件同样不算存档
                            if _JUNK_PATH.search(Path(low).stem):
                                continue
                            hit = False
                            if suffix in _STRONG_SAVE_EXTS:
                                strong += 1
                                hit = True
                            elif suffix in _WEAK_SAVE_EXTS:
                                # .json/.dat 只有名字里带 save/profile 时才可信
                                if _SAVE_NAME_HINT.search(Path(low).stem):
                                    strong += 1
                                    hit = True
                                else:
                                    weak += 1
                            elif "." not in low and _looks_like_save_file(entry.name):
                                strong += 1
                                hit = True
                            if hit:
                                nfiles += 1
                                try:
                                    nbytes += entry.stat(follow_symlinks=False).st_size
                                except OSError:
                                    pass
                        elif entry.is_dir(follow_symlinks=False):
                            # 崩溃转储/日志子目录直接跳过：里面常有一堆 .dat/.dmp，
                            # 会把"只有崩溃日志"的目录误判成有存档
                            if _JUNK_PATH.search(entry.name):
                                continue
                            if _SLOT_DIR.match(entry.name):
                                slot_dirs += 1
                            if depth + 1 < max_depth:
                                stack.append((Path(entry.path), depth + 1))
                    except OSError:
                        continue
        except OSError:
            continue
    if strong or slot_dirs or cloud_marked:
        strength = 2
    else:
        strength = 1 if weak else 0
    return (strength, nfiles, nbytes)


def _save_strength(path: Path, max_depth: int = 3) -> int:
    """目录里存档证据的强度：0 = 没有，1 = 弱（配置/日志也会命中），2 = 强。

    反查命中只按"路径名对不对得上"是不够的 —— 同一款游戏在
    ``AppData\\Local`` 下往往有同名目录，装的却是崩溃日志与用户设置。
    必须再看内容，否则会把用户带到 crash 目录去（实测真实踩坑：

      · ``AppData\\Local\\Rockstar Games\\Red Dead Redemption 2`` → 只有
        ``CrashLogs\\settings.dat``，真存档在 ``Documents\\Rockstar Games\\...``
      · ``AppData\\Local\\CD Projekt Red\\Cyberpunk 2077`` → 只有
        ``UserSettings.json``，真存档在 ``Saved Games\\CD Projekt Red\\...``
    """
    return _scan_save_stats(path, max_depth=max_depth)[0]


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #

@dataclass
class InstallScan:
    """一次"从游戏目录反推"的完整结果。"""

    install_dir: Path
    game_name: str = ""
    appid: int | None = None
    appid_evidence: str = ""
    engine: EngineInfo = field(default_factory=EngineInfo)
    save_dir: Path | None = None
    reason: str = ""
    confidence: str = "low"
    candidates: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.save_dir is not None

    def summary(self) -> str:
        lines = [f"游戏目录：{self.install_dir}"]
        if self.appid:
            lines.append(f"Steam appid：{self.appid}（{self.appid_evidence}）")
        if self.engine.name and self.engine.name != "unknown":
            lines.append(f"引擎：{self.engine.evidence}")
            if self.engine.company or self.engine.product:
                lines.append(f"发行信息：{self.engine.company or '?'} / {self.engine.product or '?'}")
        if self.ok:
            lines.append(f"存档目录：{self.save_dir}（{self.confidence} · {self.reason}）")
        else:
            lines.append("存档目录：未确定")
        if self.candidates:
            lines.append("候选：")
            for p, why in self.candidates[:6]:
                lines.append(f"  · {p}  ← {why}")
        return "\n".join(lines)


def _appdata_reverse(names: list[str], min_strength: int = 2) -> list[tuple[Path, str]]:
    """已知游戏名，去所有存档根目录反查 ``<公司>/<游戏>`` → [(路径, 依据)]。

    为什么必须做这件事：一大批游戏的存档落点是 ``<公司>/<产品>`` 两级布局，
    而公司名既不在游戏目录里、也不是可枚举的 —— 正着拼路径必然漏。
    实测必须靠反查才能命中的：
      · ``LocalLow/semiwork/Repo``（Unity，exe 无版本资源）
      · ``LocalLow/Ravenjm/Scam Line``
      · ``Documents/Rockstar Games/Red Dead Redemption 2``
      · ``Saved Games/CD Projekt Red/Cyberpunk 2077``

    直接复用 ``discover._normed_full()``：那已经是"扫遍所有存档根目录、
    深度 2、归一化名预计算"的缓存索引，再扫一遍纯属浪费
    （实测重建一次索引要 1 秒以上）。

    ``min_strength`` 控制内容过滤强度，见 :func:`_save_strength`：
    传 2 只返回真有存档的目录；传 1 会把"名字对上但只有配置/日志"的
    也带上，用于给用户当候选。
    """
    from . import discover  # 延迟导入，避免循环

    targets = [discover._normalize(n) for n in names if n]
    if not targets:
        return []

    out: list[tuple[int, int, Path, str]] = []
    seen: set[str] = set()
    for _orig, normed, path, _mtime, _has in discover._normed_full():
        if not normed:
            continue
        best = 0
        for t in targets:
            # 整名相等优先：`_match_normed` 对 <6 字符的名字一律不匹配
            # （防 "user"/"data" 命中一切），但 "Repo"→"repo" 这种
            # 短产品名正是我们想要的精确命中。
            if normed == t:
                best = 100
                break
            best = max(best, discover._match_normed(normed, t))
        if best < 70:
            continue
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        # 名字对上还不够：同一款游戏在 AppData\Local 下常有个同名目录
        # 装的却是崩溃日志/用户设置。按内容强度过滤并排序，弱的靠后。
        strength = _save_strength(path)
        if strength < min_strength:
            continue
        # 强度相同时（都还没存过档）优先推荐不在 AppData\Local 下的：
        # AppData\Local 的同名目录十有八九是日志与配置（实测 RDR2、
        # Cyberpunk 2077 都是这个套路），而 Documents / Saved Games
        # / LocalLow 下的同名目录才是存档。
        penalty = 1 if "\\appdata\\local\\" in key else 0
        out.append((strength, -penalty, path, f"存档根目录反查命中 {path.parent.name}/{path.name}"))
    # 强证据（真有存档文件）排前面，空目录/配置目录排后面
    out.sort(key=lambda x: (-x[0], -x[1]))
    return [(path, why) for _s, _p, path, why in out]


def _expand_templates(engine: EngineInfo, game_name: str) -> list[tuple[Path, str]]:
    """按引擎规则展开模板 → [(存在的路径, 依据)]。"""
    rules = ENGINE_RULES.get(engine.name, ())
    if not rules:
        return []
    values = {
        "company": engine.company,
        "product": engine.product,
        "name": game_name or engine.product,
    }
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for tpl in rules:
        try:
            filled = tpl.format(**{k: (v or "") for k, v in values.items()})
        except (KeyError, IndexError):
            continue
        if "//" in filled or filled.endswith("/"):
            continue  # 关键字段为空，跳过
        p = paths.expand(filled)
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_dir():
            out.append((p, f"{engine.evidence} 的存档落点"))
    return out


def _inner_confidence(path: Path) -> bool:
    """本体内部命中的存档目录是否够格给 high 置信度。

    判据是"像真的在用"而不是"有个文件"：占位模板永远只有 1 份、几十 KB，
    真实存档要么多份（自动存档轮转），要么单份但体量明显（怪物猎人 11 MB）。
    """
    strength, nfiles, nbytes = _scan_save_stats(path)
    if strength < 2:
        return False
    return nfiles >= 2 or nbytes >= 1024 * 1024


def scan_install_dir(install_dir: Path | str, game_name: str = "") -> InstallScan:
    """从游戏本体目录反推存档位置。永不抛异常。"""
    root = Path(install_dir)
    scan = InstallScan(install_dir=root, game_name=game_name)
    if not root.is_dir():
        scan.reason = "目录不存在"
        return scan

    # ① appid —— 有它就能走 Steam 云存档的精确链路
    scan.appid, scan.appid_evidence = read_steam_appid(root)
    if scan.appid:
        from . import discover  # 延迟导入，避免循环
        cloud = discover.steam_cloud_index()
        if scan.appid in cloud:
            scan.save_dir = cloud[scan.appid][0]
            scan.reason = f"本体带 appid {scan.appid}，命中 Steam 云存档目录"
            scan.confidence = "high"
            return scan

    # ② 引擎 + 版本信息
    scan.engine = detect_engine(root)
    name = game_name or scan.engine.product

    # ③ 本体内部的存档文件夹（实测命中率最高）
    inner = save_dirs_in_install(root)
    for path, why in inner:
        if _has_any_save(path):
            scan.save_dir = path
            scan.reason = why
            # 目录名具体 + 内容是多份存档 → high；否则 medium。
            # 早期用 `if "含存档文件" in why` 判断，改成带数量的说法后
            # 这句话不再出现，置信度会整体掉到 medium，所以改成看分数。
            scan.confidence = "high" if _inner_confidence(path) else "medium"
            scan.candidates = inner[1:7]
            return scan

    # ④ 存档根目录反查：Unity/大厂游戏的 company 名常读不到，
    #    靠产品名去 <公司>/<游戏> 里反查最可靠（实测覆盖 LocalLow、
    #    Documents/Rockstar Games、Saved Games 三大布局）
    reverse_names = [name, scan.engine.product, root.name]
    reverse = _appdata_reverse(reverse_names, min_strength=2)
    for path, why in reverse:
        scan.save_dir = path
        scan.reason = why
        scan.confidence = "high"
        scan.candidates = inner[:6] + reverse[:6]
        return scan

    # ⑤ 按引擎规则到 AppData 下展开
    expanded = _expand_templates(scan.engine, name)
    for path, why in expanded:
        if _has_any_save(path):
            scan.save_dir = path
            scan.reason = why
            scan.confidence = "medium"
            scan.candidates = inner[:6] + expanded[:6]
            return scan

    # ⑥ 什么都没命中：给出候选让用户自己点。
    #    分三档：强证据 → 弱证据（只有配置/日志）→ 空目录（还没存过档）。
    #    RDR2 就是第三档：Documents\Rockstar Games\... 存在但 Profiles 是空的，
    #    它比 AppData\Local 下那个只有 CrashLogs 的同名目录更该被推荐。
    reverse_all = _appdata_reverse(reverse_names, min_strength=0)
    tried = {str(p).lower() for p, _ in reverse}
    weak_reverse = [(p, w) for p, w in reverse_all if str(p).lower() not in tried]
    scan.candidates = reverse[:6] + inner[:6] + weak_reverse[:6] + expanded[:6]
    if scan.candidates:
        scan.reason = "未发现确定项，下面是可用候选，请人工确认"
    elif scan.engine.name == "unknown":
        scan.reason = "未识别出引擎，也没找到存档目录；请手动指定"
    else:
        scan.reason = "已识别引擎但存档目录尚未创建（先进游戏存一次档）"
    return scan


def _has_any_save(path: Path, max_depth: int = 3) -> bool:
    """目录下（递归 max_depth 层）是否有像存档的文件。"""
    return _save_strength(path, max_depth=max_depth) > 0
