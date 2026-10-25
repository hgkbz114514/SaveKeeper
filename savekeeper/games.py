"""内置游戏库 + 自动检测 + 用户自定义游戏。

路径来源优先级：
1. Steam 云存档目录（userdata/<uid>/<appid>/remote）—— 最可靠
2. 硬编码候选路径模板（%APPDATA% / %LOCALAPPDATA% / Documents / Steam userdata）
3. Steam 安装目录下搜索（兜底）
4. 用户手动配置
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths


@dataclass
class Game:
    id: str
    name: str
    platform: str
    steam_appid: int | None = None
    candidates: list[str] = field(default_factory=list)  # 路径模板，%VAR% 展开
    notes: str = ""
    # 用户配置项
    custom_path: str = ""
    custom: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "steam_appid": self.steam_appid,
            "candidates": self.candidates,
            "notes": self.notes,
            "custom_path": self.custom_path,
            "custom": self.custom,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Game":
        known = {k: data.get(k) for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


# --------------------------------------------------------------------------- #
# 内置库 —— 覆盖中英文玩家最常备份存档的游戏
# --------------------------------------------------------------------------- #

BUILTIN_GAMES: list[Game] = [
    # ---- Steam / PC 大作 ----
    Game("eldenring", "艾尔登法环", "Steam", 1245620,
         ["%APPDATA%/EldenRing", "%APPDATA%/EldenRing/Save"],
         "第一次进入会分配随机 SteamID 目录"),
    Game("sekiro", "只狼：影逝二度", "Steam", 814380,
         ["%APPDATA%/Sekiro"], ""),
    Game("dark_souls3", "黑暗之魂3", "Steam", 374320,
         ["%APPDATA%/DarkSoulsIII"], ""),
    Game("cyberpunk2077", "赛博朋克2077", "Steam", 1091500,
         ["%LOCALAPPDATA%/CD Projekt Red/Cyberpunk 2077"], ""),
    Game("witcher3", "巫师3：狂猎", "Steam", 292030,
         ["%USERPROFILE%/Documents/The Witcher 3"], ""),
    Game("baldurs_gate3", "博德之门3", "Steam", 1086940,
         ["%LOCALAPPDATA%/Larian Studios/Baldur's Gate 3"], "云同步候选优先"),
    Game("bg3_early", "博德之门3（抢先体验）", "Steam", 1086940,
         ["%LOCALAPPDATA%/Larian Studios/Baldur's Gate 3 - Early Access"], ""),
    Game("stardew", "星露谷物语", "Steam", 413150,
         ["%APPDATA%/StardewValley"], ""),
    Game("terraria", "泰拉瑞亚", "Steam", 105600,
         ["%USERPROFILE%/Documents/My Games/Terraria"], ""),
    Game("rimworld", "边缘世界", "Steam", 294100,
         ["%USERPROFILE%/AppData/LocalLow/Ludeon Studios/RimWorld by Ludeon Studios"], ""),
    Game("dave_the_diver", "潜水员戴夫", "Steam", 1868140,
         ["%USERPROFILE%/AppData/LocalLow/Mintrocket/dave the diver"], ""),
    Game("hollow_knight", "空洞骑士", "Steam", 367520,
         ["%USERPROFILE%/AppData/LocalLow/Team Cherry/Hollow Knight"], ""),
    Game("hades", "哈迪斯", "Steam", 1145360,
         ["%USERPROFILE%/Documents/Saved Games/Hades", "%APPDATA%/Supergiant Games/Hades"], ""),
    Game("celeste", "蔚蓝", "Steam", 504230,
         ["%USERPROFILE%/AppData/LocalLow/Matt Makes Games/Celeste"], ""),
    Game("slay_the_spire", "杀戮尖塔", "Steam", 646570,
         ["%APPDATA%/SlayTheSpire"], ""),
    Game("rainworld", "雨世界", "Steam", 312520,
         ["%USERPROFILE%/AppData/LocalLow/Akupara Games/Rain World"], ""),
    Game("noita", "女巫", "Steam", 881100,
         ["%APPDATA%/noita"], "存档位于 noita/save00"),
    Game("valheim", "英灵神殿", "Steam", 892970,
         ["%APPDATA%/Valheim", "%USERPROFILE%/AppData/LocalLow/IronGate/Valheim"], ""),
    Game("blackmyth", "黑神话：悟空", "Steam", 2358720,
         ["%USERPROFILE%/Saved Games/b1/Saved"], "可能需要以管理员运行"),
    Game("helldivers2", "地狱潜者2", "Steam", 553850,
         ["%APPDATA%/Arrowhead/Helldivers 2"], ""),
    Game("nfs_body", "极品飞车：热度", "Steam", 1335080,
         ["%USERPROFILE%/Documents/NFS Heat"], ""),
    # ---- 独立 / 国产 ----
    Game("mhw", "怪物猎人：世界", "Steam", 582010,
         ["%USERPROFILE%/Documents/Monster Hunter World"], "Steam 版存档在 Savedata"),
    Game("mhrise", "怪物猎人：崛起", "Steam", 1446780,
         ["%USERPROFILE%/Documents/Monster Hunter Rise"], ""),
    Game("animalwell", "动物井", "Steam", 2132040,
         ["%USERPROFILE%/AppData/LocalLow/Bigmode/ANIMAL WELL"], ""),
    Game("v_rising", "夜族崛起", "Steam", 1604030,
         ["%APPDATA%/Stunlock Studios/VRisingServer", "%USERPROFILE%/AppData/LocalLow/Stunlock Studios/VRising"], ""),
    Game("factorio", "异星工厂", "Steam", 427520,
         ["%APPDATA%/Factorio", "%USERPROFILE%/Documents/Factorio"], ""),
    Game("satisfactory", "幸福工厂", "Steam", 526870,
         ["%USERPROFILE%/AppData/Local/FactoryGameSteam-Shipping/Saved/SaveGames"], ""),
    Game("dyson_sphere", "戴森球计划", "Steam", 1366540,
         ["%USERPROFILE%/Documents/Dyson Sphere Program"], "国产，云存档位置特殊"),
    Game("chained_together", "链在一起", "Steam", 2124490,
         ["%LOCALAPPDATA%/Anegar Games/Chained Together/Saved/SaveGames"], ""),
    Game("palworld", "幻兽帕鲁", "Steam", 1623730,
         ["%LOCALAPPDATA%/Pal/Saved/SaveGames", "%APPDATA%/Pal"], ""),
    Game("dredge", "渔帆暗涌", "Steam", 1563060,
         ["%USERPROFILE%/AppData/LocalLow/Black Salt Games/DREDGE"], ""),
    Game("cocoon", "茧 COCOON", "Steam", 1497440,
         ["%USERPROFILE%/AppData/LocalLow/Geometric Interactive/COCOON"], ""),
    Game("loop_hero", "循环英雄", "Steam", 1282730,
         ["%APPDATA%/Four Quarters/Loop Hero"], ""),
    Game("night_of_killer", "杀戮之夜", "Steam", 2215430,
         ["%USERPROFILE%/AppData/LocalLow/RM Game/KillNight"], ""),
    # ---- Rockstar / Epic / GOG ----
    Game("gta5", "GTA5 / GTA Online", "Rockstar/Steam", 271590,
         ["%USERPROFILE%/Documents/Rockstar Games/GTA V/Profiles", "%USERPROFILE%/OneDrive/Documents/Rockstar Games/GTA V/Profiles"],
         "注意 OneDrive 重定向"),
    Game("rdr2", "荒野大镖客2", "Rockstar/Steam", 1174180,
         ["%USERPROFILE%/Documents/Rockstar Games/Red Dead Redemption 2/Profiles"], ""),
    Game("se_ultrakill", "ULTRAKILL", "Steam", 1229490,
         ["%APPDATA%/../LocalLow/NewBlood Interactive/ULTRAKILL"], ""),
    Game("sh_game", "寂静岭2 重制版", "Steam", 2124490,
         ["%LOCALAPPDATA%/Silent Hill 2/Saved/SaveGames"], ""),
    Game("rune_factory", "符文工房", "Steam", 2198150,
         ["%USERPROFILE%/AppData/LocalLow/Marvelous/Rune Factory"], ""),
    # ---- 国产 / 亚服热门 ----
    Game("genshin", "原神", "官方客户端", None,
         ["%USERPROFILE%/AppData/LocalLow/miHoYo/Genshin Impact"],
         "路径随安装位置变化，检测不到请手动指定"),
    Game("starrail", "崩坏：星穹铁道", "官方客户端", None,
         ["%USERPROFILE%/AppData/LocalLow/Cognosphere/Star Rail"], ""),
    Game("zzz", "绝区零", "官方客户端", None,
         ["%USERPROFILE%/AppData/LocalLow/miHoYo/ZenlessZoneZero"], ""),
    Game("honkai3", "崩坏3", "官方客户端", None,
         ["%USERPROFILE%/AppData/LocalLow/miHoYo/Honkai Impact 3rd"], ""),
    Game("naraka", "永劫无间", "Steam", 1206650,
         ["%APPDATA%/Naraka Bladepoint"], ""),
    Game("sifu", "师父 SIFU", "Steam", 2131750,
         ["%LOCALAPPDATA%/Sifu/Saved/SaveGames"], ""),
    Game("atomic_heart", "原子之心", "Steam", 668580,
         ["%USERPROFILE%/AppData/Local/AtomicHeart/Saved/SaveGames"], ""),
    Game("wo_long", "卧龙：苍天陨落", "Steam", 1448440,
         ["%USERPROFILE%/AppData/Local/WoLong/Saved/SaveGames"], ""),
    Game("lies_of_p", "匹诺曹的谎言", "Steam", 1627720,
         ["%LOCALAPPDATA%/LiesOfP/Saved/SaveGames"], ""),
    Game("tekken8", "铁拳8", "Steam", 1778820,
         ["%USERPROFILE%/AppData/Local/TekkenGame/Saved/SaveGames"], ""),
    Game("street_fighter6", "街头霸王6", "Steam", 1364780,
         ["%USERPROFILE%/AppData/Local/StreetFighter6/Saved/SaveGames"], ""),
    Game("mortal_kombat1", "真人快打1", "Steam", 1971870,
         ["%USERPROFILE%/AppData/Local/MK12/Saved/SaveGames"], ""),
    Game("armored_core6", "装甲核心6", "Steam", 1888160,
         ["%APPDATA%/ArmoredCore6"], ""),
    Game("alan_wake2", "心灵杀手2", "Steam", 1717520,
         ["%USERPROFILE%/AppData/Local/AlanWake2/Saved/SaveGames"], ""),
    Game("dead_space", "死亡空间 重制版", "Steam", 1693980,
         ["%USERPROFILE%/AppData/Local/DeadSpace/Saved/SaveGames"], ""),
    Game("xenoblade3", "异度之刃3", "Steam", 2426640,
         ["%USERPROFILE%/Documents/My Games/Xenoblade Chronicles 3"], ""),
    Game("final_fantasy16", "最终幻想16", "Steam", 2515020,
         ["%USERPROFILE%/Documents/My Games/FINAL FANTASY XVI"], ""),
    Game("octopath2", "歧路旅人2", "Steam", 1971650,
         ["%USERPROFILE%/AppData/LocalLow/SquareEnix/OCTOPATH TRAVELER II"], ""),
    Game("dragon_dogma2", "龙之信条2", "Steam", 2054970,
         ["%USERPROFILE%/AppData/Local/DD2/Saved/SaveGames"], ""),
    Game("risk_of_rain2", "雨中冒险2", "Steam", 632360,
         ["%USERPROFILE%/AppData/LocalLow/Hopoo Games/Risk of Rain 2"], ""),
    Game("deep_rock", "深岩银河", "Steam", 548430,
         ["%USERPROFILE%/Saved Games/Deep Rock Galactic"], ""),
    Game("phasmophobia", "恐鬼症", "Steam", 739630,
         ["%USERPROFILE%/AppData/LocalLow/Kinetic Games/Phasmophobia"], ""),
    Game("lethal_company", "致命公司", "Steam", 1966720,
         ["%USERPROFILE%/AppData/LocalLow/ZeekerssRBLX/Lethal Company"], ""),
    Game("content_warning", "内容警告", "Steam", 2881650,
         ["%USERPROFILE%/AppData/Local/Landfall/ContentWarning"], ""),
    Game("palia", "帕利亚 Palia", "Steam", 2707930,
         ["%USERPROFILE%/AppData/Local/Palia/Saved/SaveGames"], ""),
    Game("enshrouded", "雾锁王国", "Steam", 1203620,
         ["%USERPROFILE%/AppData/Local/Enshrouded/Saved/SaveGames"], ""),
    Game("megabonk", "Megabonk", "Steam", 3287140,
         ["%USERPROFILE%/AppData/LocalLow/Ved/Megabonk"], "若检测失败请手动指定"),
    Game("cuphead", "茶杯头", "Steam", 268910,
         ["%USERPROFILE%/AppData/LocalLow/StudioMDHR/Cuphead"], ""),
    Game("dead_cells", "死亡细胞", "Steam", 588650,
         ["%APPDATA%/Dead Cells"], ""),
    Game("hollow_knight_silksong", "空洞骑士：丝之歌", "Steam", 1030300,
         ["%USERPROFILE%/AppData/LocalLow/Team Cherry/Hollow Knight Silksong"], "如路径不同请手动指定"),
    Game("balatro", "小丑牌 Balatro", "Steam", 2379780,
         ["%APPDATA%/Balatro"], ""),
    Game("cloverpit", "四叶草坑", "Steam", 3018320,
         ["%USERPROFILE%/AppData/LocalLow/Panik/CloverPit"], "若检测失败请手动指定"),
    Game("peak", "PEAK", "Steam", 3527290,
         ["%USERPROFILE%/AppData/LocalLow/Landfall/PEAK"], "若检测失败请手动指定"),
    Game("arc_raiders", "Arc Raiders", "Steam", 3164500,
         ["%USERPROFILE%/AppData/Local/ArcRaiders/Saved/SaveGames"], ""),
]


# --------------------------------------------------------------------------- #
# 自动检测
# --------------------------------------------------------------------------- #

def resolve_game_path(game: Game) -> Path | None:
    """按优先级解析存档目录。命中后不写入配置（只读探测）。"""
    # 1) Steam 云存档
    if game.steam_appid:
        cloud = paths.steam_cloud_saves({game.steam_appid: game.name})
        if game.steam_appid in cloud:
            return cloud[game.steam_appid]

    # 2) 硬编码候选路径
    hits = paths.existing(game.candidates)
    if hits:
        return hits[0]

    # 3) Steam userdata 目录（兼容非 remote 布局）
    if game.steam_appid:
        root = paths.steam_root()
        if root is not None:
            userdata = root / "userdata"
            if userdata.is_dir():
                for user_dir in userdata.iterdir():
                    cand = user_dir / str(game.steam_appid)
                    if cand.is_dir() and any(cand.iterdir()):
                        return cand

    # 4) 用户手动配置
    if game.custom_path:
        p = paths.expand(game.custom_path)
        if p.is_dir():
            return p
    return None


def detect_all(games: list[Game]) -> dict[str, Path | None]:
    return {g.id: resolve_game_path(g) for g in games}


def search_steam_appid(name_hint: str, installed: dict[int, str]) -> int | None:
    """按名字在已安装 Steam 游戏里模糊找 appid。"""
    key = name_hint.lower().strip()
    for appid, name in installed.items():
        if key and (key in name.lower() or name.lower() in key):
            return appid
    return None
