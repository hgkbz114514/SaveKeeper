"""导出快照：给用户「把存档发给别人 / 传网盘」的路子。

只导出 data/ 下的存档本体，不导出 manifest.json 与内部目录结构 —— 对方要的是
能直接丢进游戏目录的文件。目标已有同名文件时落进子目录，绝不覆盖用户自己的东西。
"""

from __future__ import annotations

import pytest

from savekeeper import core


def _make_snapshot(tmp_path):
    save = tmp_path / "save"
    save.mkdir()
    (save / "a.sl2").write_bytes(b"alpha")
    (save / "slot").mkdir()
    (save / "slot" / "b.sl2").write_bytes(b"beta")
    return core.create_snapshot(save, tmp_path / "bk", "g", "G")


def test_export_flattens_data_only(tmp_path):
    snap = _make_snapshot(tmp_path)
    out = tmp_path / "out"
    out.mkdir()

    report = core.export_snapshot_to(snap, out)

    assert report.ok
    assert report.restored_files == 2
    assert (out / "a.sl2").read_bytes() == b"alpha"
    assert (out / "slot" / "b.sl2").read_bytes() == b"beta"
    # 不带内部结构
    assert not (out / "manifest.json").exists()
    assert not (out / "data").exists()


def test_export_avoids_clobbering_user_files(tmp_path):
    snap = _make_snapshot(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.sl2").write_bytes(b"mine")

    report = core.export_snapshot_to(snap, out)

    assert (out / "a.sl2").read_bytes() == b"mine"
    assert report.target == out / snap.name
    assert (out / snap.name / "a.sl2").read_bytes() == b"alpha"


def test_export_refuses_corrupted_snapshot(tmp_path):
    snap = _make_snapshot(tmp_path)
    (snap / core.DATA_DIR / "a.sl2").write_bytes(b"tampered")
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(core.IntegrityError):
        core.export_snapshot_to(snap, out)
    assert not (out / "a.sl2").exists()
