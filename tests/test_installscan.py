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

# ------------------------------------------------- 深度 / 模板档 / InternalName


def test_deep_save_profile_layout_is_reached(tmp_path: Path) -> None:
    """真存档藏在 profiles/<id>/cloud/ 时要扫得到（深度 off-by-one 回归）。

    实测原型：E:/300TIAN/C2993「高三最后300天」（Goldberg 破解壳），
    存档在 %APPDATA%/LIANG/Gaosan300/profiles/steam_<id>/cloud/auto.sav。
    从根算 cloud 是第 3 层，早期 `if depth + 1 < max_depth` 让第 3 层
    目录里的文件永远不被统计 —— 统计结果是 (1, 0, 0)，0 个文件。
    """
    root = tmp_path / "Gaosan300"
    _write(root / "profiles" / "steam_76561198123519615" / "cloud" / "auto.sav", b"\0" * 1_100_000)
    _write(root / "profiles" / "steam_76561198123519615" / "cloud" / "profile.sav", b"\0" * 44_000)

    strength, nfiles, nbytes = installscan._scan_save_stats(root)
    assert nfiles == 2, f"第 3 层的存档文件必须被统计到，实际 {nfiles} 个"
    assert nbytes > 1_000_000
    assert strength == 2


def test_auto_only_saves_are_flagged_as_template(tmp_path: Path) -> None:
    """只有 auto-*.save、没有任何玩家手动存档 → 判为随包分发的模板档。

    实测原型：and Roger / Outer Wilds / Hogwarts Legacy / MillenniumDream
    四款不同游戏的 game/saves/auto-1-LT1.save 逐字节完全相同
    （SHA-256 3ce21cb384e46d86068f94e7...），且都只有 auto-* + persistent。
    """
    saves = tmp_path / "game" / "saves"
    for i in range(1, 4):
        _write(saves / f"auto-{i}-LT1.save", b"PK\x03\x04" + b"\0" * 300_000)
    _write(saves / "persistent", b"\0" * 1000)

    assert installscan._looks_like_bundled_template(saves) is True


def test_manual_save_disables_template_flag(tmp_path: Path) -> None:
    """有玩家手动存档（Ren'Py 的 <n>-<n>-LT1.save）就不再是模板。"""
    saves = tmp_path / "game" / "saves"
    _write(saves / "auto-1-LT1.save", b"\0" * 300_000)
    _write(saves / "1-1-LT1.save", b"\0" * 300_000)

    assert installscan._looks_like_bundled_template(saves) is False


def test_engine_info_carries_internal_name() -> None:
    """PE 的 InternalName 要能被 EngineInfo 携带，供反查名字候选使用。

    实测原型：「高三最后300天」的 CompanyName=LIANG、InternalName=Gaosan300，
    显示名与产品名都猜不到目录，只有 InternalName 对得上
    AppData/Roaming/LIANG/Gaosan300。
    """
    info = installscan.EngineInfo(company="LIANG", product="高三最后300天")
    info.internal = "Gaosan300"
    assert info.internal == "Gaosan300"
    assert installscan.EngineInfo().internal == ""

# --------------------------------------- 主程序识别 / 空壳目录如实告知


def test_mod_manager_exe_is_not_the_main_exe(tmp_path: Path) -> None:
    """ModManager.UI.exe 不能被当成游戏主程序。

    实测原型：Red Dead Redemption 2 目录只剩 LML mod 残留，唯一"正常"的 exe 是
    ModManager/ModManager.UI.exe（154 KB），它被选成主程序后 product 变成
    'ModManager.UILauncher'，后续 AppData 反查名字全错。
    """
    game = tmp_path / "RDR2"
    _write(game / "ModManager" / "ModManager.UI.exe", b"MZ" + b"\0" * 150_000)
    _write(game / "RedDeadRedemption2.exe", b"MZ" + b"\0" * 40_000_000)

    picked = installscan.guess_main_exe(game)
    assert picked is not None
    assert picked.name.lower() == "reddeadredemption2.exe", (
        f"应选真正的主程序，实际选了 {picked}"
    )


def test_redist_exe_is_skipped_by_path(tmp_path: Path) -> None:
    """运行库目录里的 exe 靠**目录名**排除，不只看文件名。

    实测：Red Dead Redemption 2 里 ``FOR ERRORS\\REDIST\\vcredist2015_2017_2019_x64.exe``
    有 15 MB，比 ModManager.UI.exe 还大，且文件名不含 _NOT_MAIN_EXE 里的常见词以外
    匹配不稳——必须用相对路径判断。
    """
    game = tmp_path / "G"
    _write(game / "FOR ERRORS" / "REDIST" / "vcredist2015_2017_2019_x64.exe", b"MZ" + b"\0" * 15_000_000)
    _write(game / "Game.exe", b"MZ" + b"\0" * 25_000_000)

    picked = installscan.guess_main_exe(game)
    assert picked is not None
    assert picked.name.lower() == "game.exe"


def test_empty_shell_dir_is_reported_honestly(tmp_path: Path) -> None:
    """空壳目录要说「这里没有游戏本体」，而不是含糊的「未识别出引擎」。

    实测：本机 steamapps/common 下 TSUGUNOHI / WormsWMD 各只有 1 个空目录，
    高三最后300天 是 0 个文件。
    """
    empty = tmp_path / "TSugunohi"
    (empty / "gamedata").mkdir(parents=True)

    scan = installscan.scan_install_dir(empty)
    assert scan.ok is False
    assert "没有游戏本体" in scan.reason, f"实际原因：{scan.reason}"


def test_real_game_body_is_not_flagged_as_shell(tmp_path: Path) -> None:
    """有 20 MB 以上主程序的目录不能被误判成空壳。"""
    game = tmp_path / "G"
    _write(game / "Game.exe", b"MZ" + b"\0" * 25_000_000)

    ok, why = installscan._dir_looks_like_game_body(game)
    assert ok is True, why


def test_summary_prints_reason_when_not_determined(tmp_path: Path) -> None:
    """未确定时 summary 必须把原因打出来（早期只打「未确定」，诊断白做）。"""
    empty = tmp_path / "E"
    empty.mkdir()
    scan = installscan.scan_install_dir(empty)
    text = scan.summary()
    assert "存档目录：未确定" in text
    assert "原因：" in text
