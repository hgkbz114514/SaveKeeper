"""installscan 的回归测试。

这里的用例全部来自实测踩过的坑，每条都对应一次真实的误判：
把占位模板当存档、把崩溃日志目录当存档、Steam 保留 appid 当游戏。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from savekeeper import installscan


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------- 存档目录排序


def test_placeholder_savedata_loses_to_real_saves(tmp_path: Path) -> None:
    """占位模板目录不能压过真正在写的存档目录。

    实测原型：Hogwarts Legacy / Outer Wilds 的根目录各有一个 ``savedata/``，
    里面是 23 KB 的 ``save0121.dat``，两个游戏的 SHA-256 完全相同 —— 是随
    游戏发布的模板。真正的存档在 ``game/saves/``，十几份 250–310 KB 的
    ``*.save``。早期评分只看"名字像 + 有存档文件"，模板档直接排第一。
    """
    # 占位：1 份 23 KB 的模板
    _write(tmp_path / "savedata" / "save0121.dat", b"\0" * 23648)
    # 真存档：10 份 ~300 KB
    for i in range(1, 11):
        _write(tmp_path / "game" / "saves" / f"auto-{i}-LT1.save", b"\0" * 300_000)

    hits = installscan.save_dirs_in_install(tmp_path)
    assert hits, "应该能找到存档目录"
    assert hits[0][0].name == "saves", f"排第一的应是真存档目录，实际是 {hits[0][0]}"


def test_single_big_save_dir_still_wins(tmp_path: Path) -> None:
    """单份但体量大的存档也要排前面（怪物猎人就只有 1 个 11 MB 的档）。"""
    _write(tmp_path / "backup" / "save0121.dat", b"\0" * 23648)  # 占位 + 泛化名
    _write(tmp_path / "savedata_backup" / "SAVEDATA1000", b"\0" * (11 * 1024 * 1024))

    hits = installscan.save_dirs_in_install(tmp_path)
    assert hits[0][0].name == "savedata_backup"


# ---------------------------------------------------------------- 内容强度


def test_crash_logs_are_not_a_save_dir(tmp_path: Path) -> None:
    """只有崩溃日志与设置的目录，强度不能到"强"（2）。

    实测原型：``AppData\\Local\\Rockstar Games\\Red Dead Redemption 2``
    里面只有 ``CrashLogs\\settings.dat`` 和 ``UserSettings.json``，真存档在
    Documents 下。CrashLogs 整个被 ``_JUNK_PATH`` 跳过，只剩一个弱扩展名
    的 json，所以强度是 1（弱，只能当候选）—— ``_appdata_reverse`` 用
    ``min_strength=2`` 就把它挡在结论之外。
    """
    _write(tmp_path / "CrashLogs" / "settings.dat", b"x" * 100)
    _write(tmp_path / "UserSettings.json", b"{}")
    assert installscan._save_strength(tmp_path) == 1
    # 关键：它够不上 high 置信度要的门槛
    assert installscan._inner_confidence(tmp_path) is False


def test_steam_cloud_marker_makes_dir_a_save(tmp_path: Path) -> None:
    """带 steam_autocloud.vdf 的目录直接认定为存档目录。

    实测原型：``AppData\\Local\\Draw&Guess`` 里全是 ``.dgm``/``.dgwl``
    这类专有扩展名，按扩展名一个都认不出来，但 Steam 那个 vdf 摆在那里。
    """
    _write(tmp_path / "steam_autocloud.vdf", b'"AppState"{}')
    _write(tmp_path / "02f4e5c5.dgm", b"x" * 1000)
    assert installscan._save_strength(tmp_path) == 2


def test_weak_config_only_dir_is_low_strength(tmp_path: Path) -> None:
    """只有普通配置的目录算"弱"，不能当结论。"""
    _write(tmp_path / "config.json", b"{}")
    _write(tmp_path / "settings.xml", b"<x/>")
    assert installscan._save_strength(tmp_path) == 1


def test_save_named_json_counts_as_strong(tmp_path: Path) -> None:
    """``save.json`` 这种名字带 save 的弱扩展名算强证据。"""
    _write(tmp_path / "save.json", b"{}")
    assert installscan._save_strength(tmp_path) == 2


def test_missing_dir_is_zero(tmp_path: Path) -> None:
    assert installscan._save_strength(tmp_path / "nope") == 0
    assert installscan._scan_save_stats(tmp_path / "nope") == (0, 0, 0)


# ---------------------------------------------------------------- 置信度


def test_confidence_requires_more_than_one_tiny_file(tmp_path: Path) -> None:
    """单份小文件给不了 high：那正是占位档的样子。"""
    _write(tmp_path / "saves" / "save0121.dat", b"\0" * 23648)
    assert installscan._inner_confidence(tmp_path / "saves") is False

    for i in range(3):
        _write(tmp_path / "many" / f"s{i}.sav", b"\0" * 1000)
    assert installscan._inner_confidence(tmp_path / "many") is True

    _write(tmp_path / "big" / "SAVEDATA1000", b"\0" * (2 * 1024 * 1024))
    assert installscan._inner_confidence(tmp_path / "big") is True


# ---------------------------------------------------------------- 端到端


def test_scan_install_dir_never_raises_on_missing(tmp_path: Path) -> None:
    scan = installscan.scan_install_dir(tmp_path / "does-not-exist")
    assert scan.ok is False
    assert "不存在" in scan.reason


def test_scan_install_dir_finds_inner_saves(tmp_path: Path) -> None:
    """从"游戏本体目录"直接扫出本体内的存档（绿色版/破解版常用布局）。"""
    game = tmp_path / "SomeGame"
    _write(game / "SomeGame.exe", b"MZ")
    for i in range(4):
        _write(game / "saves" / f"slot{i}.sav", b"\0" * 200_000)

    scan = installscan.scan_install_dir(game)
    assert scan.ok
    assert scan.save_dir is not None
    assert scan.save_dir.name == "saves"
    assert scan.confidence == "high"


def test_scan_install_dir_accepts_str_path(tmp_path: Path) -> None:
    """GUI/CLI 都可能直接传字符串。"""
    game = tmp_path / "G"
    _write(game / "saves" / "a.sav", b"\0" * 1000)
    scan = installscan.scan_install_dir(str(game))
    assert scan.ok


@pytest.mark.parametrize("name", ["save.json", "SAVE01", "savegame.sav", "profile"])
def test_looks_like_save_file(name: str) -> None:
    assert installscan._looks_like_save_file(name)


@pytest.mark.parametrize("name", ["readme.txt", "game.exe", "Player.log", "data.pak"])
def test_not_looks_like_save_file(name: str) -> None:
    assert not installscan._looks_like_save_file(name)


def test_json_is_recognized_by_extension_only(tmp_path: Path) -> None:
    """``_looks_like_save_file`` 只做扩展名判断，.json 一律算"像"。

    区分"像存档"与"真的是存档"由 :func:`_save_strength` 负责：同样是
    ``.json``，``UserSettings.json`` 只算弱证据，``save.json`` 才算强。
    这个分工早期混在一起，导致 AppData 下大量软件配置被当成游戏存档。
    """
    assert installscan._looks_like_save_file("config.json")
    assert installscan._save_strength(_dir_with(tmp_path, "a", "config.json")) == 1
    assert installscan._save_strength(_dir_with(tmp_path, "b", "save.json")) == 2


def _dir_with(root: Path, sub: str, filename: str) -> Path:
    target = root / sub
    _write(target / filename, b"{}")
    return target
