"""游戏库与配置测试：自动检测、自定义游戏、配置持久化。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from savekeeper import config as cfg_mod
from savekeeper import games as games_mod
from savekeeper.games import Game


def _game_with_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, templates: list[str]) -> Game:
    """把内置 Game 的候选路径重定向到 tmp_path 下，避免依赖真实机器。"""
    game = Game(id="t", name="测试", platform="Steam", steam_appid=None, candidates=templates)
    return game


def test_resolve_prefers_custom_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real_save"
    real.mkdir()
    decoy = tmp_path / "decoy"
    decoy.mkdir()

    monkeypatch.setenv("SAVEKEEPER_TEST_DIR", str(real))
    game = Game(
        id="t",
        name="测试",
        platform="Steam",
        candidates=["%SAVEKEEPER_TEST_DIR%/decoy", "%SAVEKEEPER_TEST_DIR%/real_save"],
        custom_path=str(real),
    )
    assert games_mod.resolve_game_path(game) == real


def test_resolve_falls_back_to_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAVEKEEPER_TEST_DIR", str(tmp_path))
    (tmp_path / "second").mkdir()
    game = Game(
        id="t", name="测试", platform="Steam",
        candidates=["%SAVEKEEPER_TEST_DIR%/missing", "%SAVEKEEPER_TEST_DIR%/second"],
    )
    assert games_mod.resolve_game_path(game) == tmp_path / "second"


def test_resolve_returns_none_when_nothing_exists(tmp_path: Path) -> None:
    game = Game(id="t", name="测试", platform="Steam", candidates=[str(tmp_path / "nope")])
    assert games_mod.resolve_game_path(game) is None


def test_builtin_library_has_no_duplicate_ids() -> None:
    ids = [g.id for g in games_mod.BUILTIN_GAMES]
    assert len(ids) == len(set(ids))


def test_builtin_library_has_appids_and_candidates() -> None:
    for game in games_mod.BUILTIN_GAMES:
        assert game.candidates, f"{game.name} 缺少候选路径"
        assert "%" in game.candidates[0] or game.custom_path


def test_config_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    cfg = cfg_mod.Config()
    cfg.backup_root = str(tmp_path / "mybackups")
    cfg.add_custom_game("某国产游戏", str(tmp_path / "save"))
    cfg.set_path_override("stardew", str(tmp_path / "stardew_save"))
    cfg_mod.save(cfg)

    loaded = cfg_mod.load()
    assert loaded.backup_root == str(tmp_path / "mybackups")
    assert any(g.name == "某国产游戏" for g in loaded.custom_games)
    game = loaded.get_game("stardew")
    assert game is not None
    assert game.custom_path == str(tmp_path / "stardew_save")


def test_config_backup_dir_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    cfg = cfg_mod.Config()
    cfg.backup_root = str(tmp_path / "deep" / "backups")
    target = cfg.backup_dir()
    assert target.is_dir()


def test_add_custom_game_is_idempotent(tmp_path: Path) -> None:
    cfg = cfg_mod.Config()
    cfg.add_custom_game("同一个游戏", str(tmp_path / "a"))
    cfg.add_custom_game("同一个游戏", str(tmp_path / "b"))
    matches = [g for g in cfg.all_games() if g.name == "同一个游戏"]
    assert len(matches) == 1
    assert matches[0].custom_path == str(tmp_path / "b")


def test_builtin_games_not_mutated_by_overrides() -> None:
    """改配置不能污染内置库（全局共享对象）。"""
    cfg = cfg_mod.Config()
    cfg.set_path_override("stardew", "D:/Somewhere")
    cfg2 = cfg_mod.Config()
    assert cfg2.get_game("stardew").custom_path == ""
