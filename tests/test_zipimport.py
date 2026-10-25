"""zipimport 单元测试：不碰用户文件、路径穿越防护、剥壳、差异预览。"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from savekeeper import zipimport as zi


def _make_zip(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    zp = tmp_path / name
    with zipfile.ZipFile(zp, "w") as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return zp


# ---------------------------------------------------------------- inspect -- #

def test_inspect_flat_zip(tmp_path):
    zp = _make_zip(tmp_path, "a.zip", {
        "slot1.sav": b"x" * 100,
        "slot2.sav": b"y" * 50,
    })
    info = zi.inspect_zip(zp)
    assert info.root_files == ["slot1.sav", "slot2.sav"]
    assert info.root_dirs == []
    assert info.wrapped is False
    assert info.total_bytes == 150


def test_inspect_wrapped_zip(tmp_path):
    zp = _make_zip(tmp_path, "w.zip", {
        "EldenRing 存档/ER0000.sl2": b"a" * 10,
        "EldenRing 存档/bak/ER0000.bak": b"b" * 5,
    })
    info = zi.inspect_zip(zp)
    assert info.root_dirs == ["EldenRing 存档"]
    assert info.wrapped is True


# ---------------------------------------------------------------- extract -- #

def test_extract_flat(tmp_path):
    zp = _make_zip(tmp_path, "a.zip", {"slot1.sav": b"AAA", "sub/slot2.sav": b"BBB"})
    dest = tmp_path / "out"
    out = zi.extract_zip(zp, dest)
    assert (out / "slot1.sav").read_bytes() == b"AAA"
    assert (out / "sub" / "slot2.sav").read_bytes() == b"BBB"


def test_extract_rejects_zip_slip(tmp_path):
    # 成员名带 .. 穿越，必须拒绝
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("../evil.txt", b"boom")
    dest = tmp_path / "out"
    with pytest.raises(zi.ArchiveError, match="路径穿越"):
        zi.extract_zip(zp, dest)


def test_extract_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"this is not a zip")
    with pytest.raises(zi.ArchiveError):
        zi.inspect_zip(bad)
    with pytest.raises(zi.ArchiveError):
        zi.extract_zip(bad, tmp_path / "out2")


# ------------------------------------------------------- pick_save_subdir -- #

def test_pick_flat_with_saves(tmp_path):
    base = tmp_path / "pkg"
    base.mkdir(parents=True)
    (base / "slot1.sav").write_bytes(b"x" * 300)
    (base / "slot2.sav").write_bytes(b"y" * 300)
    picked, why = zi.pick_save_subdir(base)
    assert picked == base
    assert "存档文件" in why


def test_pick_best_among_siblings(tmp_path):
    base = tmp_path / "pkg"
    base.mkdir(parents=True)
    (base / "screenshots").mkdir(parents=True)
    (base / "screenshots" / "a.jpg").write_bytes(b"j" * 100)
    good = base / "savegames"
    good.mkdir()
    (good / "slot1.sav").write_bytes(b"s" * 2000)
    (good / "slot2.sav").write_bytes(b"s" * 2000)
    picked, why = zi.pick_save_subdir(base)
    assert picked == good
    assert "savegames" in why


def test_pick_falls_back_to_base(tmp_path):
    base = tmp_path / "pkg"
    base.mkdir()
    (base / "readme.txt").write_text("nothing here")
    picked, _why = zi.pick_save_subdir(base)
    assert picked == base


# ------------------------------------------------------------------ diff -- #

def test_preview_diff_replace_and_new(tmp_path):
    src = tmp_path / "src"
    tgt = tmp_path / "tgt"
    src.mkdir(parents=True)
    tgt.mkdir(parents=True)
    (src / "slot1.sav").write_bytes(b"NEW")
    (src / "extra.sav").write_bytes(b"brand new")
    (tgt / "slot1.sav").write_bytes(b"OLDDATA")   # 长度不同 → replace
    (tgt / "old_only.sav").write_bytes(b"only in target")

    pv = zi.preview_diff(src, tgt)
    assert pv.replace_n == 1
    assert pv.new_n == 1
    assert pv.missing_n == 1
    kinds = {e.rel: e.kind for e in pv.entries}
    assert kinds["slot1.sav"] == "replace"
    assert kinds["extra.sav"] == "new"
    assert "替换 1" in pv.summary()


def test_preview_diff_same_size_counts_neither(tmp_path):
    src = tmp_path / "src"
    tgt = tmp_path / "tgt"
    src.mkdir(parents=True)
    tgt.mkdir(parents=True)
    (src / "slot1.sav").write_bytes(b"AAAA")
    (tgt / "slot1.sav").write_bytes(b"BBBB")   # 同大小不同内容 → replace（保守）
    pv = zi.preview_diff(src, tgt)
    # 大小相同时预览无法区分「内容相同」和「内容不同」，保守标为替换
    assert pv.new_n == 0
    assert pv.replace_n == 1
    assert pv.entries[0].kind == "replace"
