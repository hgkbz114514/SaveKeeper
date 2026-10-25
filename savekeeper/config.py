"""配置持久化：JSON 文件，位于 %APPDATA%/SaveKeeper/config.json。

只保存用户自有数据：自定义游戏、手动路径、备份根目录。
绝不写游戏目录、注册表。
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .games import BUILTIN_GAMES, Game

APP_DIR_NAME = "SaveKeeper"
DEFAULT_BACKUP_ROOT = "%USERPROFILE%/Documents/SaveKeeper-Backups"


def app_dir() -> Path:
    """配置目录，带兜底：APPDATA 不可写时退到用户主目录。

    设置环境变量 SAVEKEEPER_HOME 可把配置与备份全部指向该目录，
    用于便携模式（U 盘随身带）或隔离测试。
    """
    override = os.environ.get("SAVEKEEPER_HOME")
    if override:
        path = Path(override)
        path.mkdir(parents=True, exist_ok=True)
        return path
    bases = [
        os.environ.get("APPDATA"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for base in [b for b in bases if b]:
        path = Path(base) / APP_DIR_NAME
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    fallback = Path.home() / f".{APP_DIR_NAME.lower()}"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def local_appdata_dir() -> Path:
    """本地（非漫游）应用数据目录，用作备份根目录的兜底。"""
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("APPDATA")
        or str(Path.home() / ".local" / "share")
    )
    path = Path(base) / APP_DIR_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        path = Path.home() / f".{APP_DIR_NAME.lower()}"
        path.mkdir(parents=True, exist_ok=True)
    return path


def _writable(path: Path) -> bool:
    """实测目录可写：能建目录、能落一个临时文件。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".sk_write_probe"
        probe.write_bytes(b"ok")
        probe.unlink(missing_ok=True)
        return True
    except (OSError, PermissionError):
        return False


def pick_backup_root(preferred: str) -> Path:
    """选一个真正可写的备份根目录。

    「文档」目录常被 OneDrive 重定向、企业策略或受控文件夹访问锁住
    （WinError 5），所以必须实测可写，不行就层层兜底，绝不让启动崩在这里。
    """
    candidates = [
        Path(os.path.expandvars(preferred)),
        Path(os.path.expandvars(DEFAULT_BACKUP_ROOT)),
        local_appdata_dir() / "backups",
        Path.home() / "SaveKeeper-Backups",
    ]
    for cand in candidates:
        if _writable(cand):
            return cand
    # 全部失败也要返回一个路径，让上层给出明确错误提示而不是崩溃
    return local_appdata_dir() / "backups"


def backup_root_default() -> Path:
    return Path(os.path.expandvars(DEFAULT_BACKUP_ROOT))


@dataclass
class Config:
    backup_root: str = DEFAULT_BACKUP_ROOT
    custom_games: list[Game] = field(default_factory=list)
    path_overrides: dict[str, str] = field(default_factory=dict)  # game_id -> 路径
    auto_verify_on_export: bool = True
    last_game_id: str = ""  # 上次选中的游戏，下次打开自动定位

    # ---------------- 序列化 ----------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "backup_root": self.backup_root,
            "custom_games": [g.to_dict() for g in self.custom_games],
            "path_overrides": self.path_overrides,
            "auto_verify_on_export": self.auto_verify_on_export,
            "last_game_id": self.last_game_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        cfg.backup_root = data.get("backup_root", DEFAULT_BACKUP_ROOT)
        cfg.custom_games = [Game.from_dict(g) for g in data.get("custom_games", [])]
        cfg.path_overrides = dict(data.get("path_overrides", {}))
        cfg.auto_verify_on_export = bool(data.get("auto_verify_on_export", True))
        cfg.last_game_id = str(data.get("last_game_id", "") or "")
        return cfg

    # ---------------- 游戏列表 ----------------
    def all_games(self) -> list[Game]:
        """内置库 + 用户自定义；用户自定义优先，且继承手动路径覆盖。

        返回深拷贝，避免把用户的手动路径写回模块级的内置库单例。
        """
        merged: dict[str, Game] = {g.id: copy.deepcopy(g) for g in BUILTIN_GAMES}
        for g in self.custom_games:
            merged[g.id] = copy.deepcopy(g)
        for gid, path in self.path_overrides.items():
            if gid in merged:
                merged[gid].custom_path = path
        return [merged[k] for k in sorted(merged, key=lambda i: merged[i].name.lower())]

    def get_game(self, game_id: str) -> Game | None:
        for g in self.all_games():
            if g.id == game_id:
                return g
        return None

    def add_custom_game(self, name: str, path: str) -> Game:
        gid = _slug(name)
        existing = self.get_game(gid)
        if existing is not None:
            existing.custom_path = path
            existing.custom = True
            self.path_overrides[gid] = path
            return existing
        game = Game(id=gid, name=name, platform="自定义", candidates=[], custom_path=path, custom=True)
        self.custom_games.append(game)
        return game

    def set_path_override(self, game_id: str, path: str) -> None:
        self.path_overrides[game_id] = path
        game = self.get_game(game_id)
        if game is not None:
            game.custom_path = path

    def backup_dir(self) -> Path:
        """返回可写的备份根目录（缓存，避免每次探测都建文件）。"""
        cached = getattr(self, "_backup_dir_cache", None)
        if cached is not None:
            return cached
        root = pick_backup_root(self.backup_root)
        object.__setattr__(self, "_backup_dir_cache", root)
        # 实际生效的路径与配置不一致时，记下真实值，界面与 CLI 都能看到
        self.backup_root = str(root)
        return root


def _slug(name: str) -> str:
    out = []
    for ch in name.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or "custom"


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #

def config_file() -> Path:
    return app_dir() / "config.json"


def load() -> Config:
    f = config_file()
    if not f.is_file():
        return Config()
    try:
        return Config.from_dict(json.loads(f.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return Config()


def save(cfg: Config) -> None:
    f = config_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
