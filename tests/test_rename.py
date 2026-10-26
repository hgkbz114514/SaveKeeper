"""快照重命名的回归测试：改名要同时改文件夹名和 manifest 里的 label。"""

import json
import shutil

import pytest

from savekeeper import core


def _make_snapshot(tmp_path, files=None, label=""):
    files = files or {"slot1.sl2": b"alpha", "sub/slot2.sl2": b"beta"}
    src = tmp_path / "save"
    src.mkdir()
    for rel, data in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return core.create_snapshot(src, tmp_path / "backups", "game", "游戏", label)


def test_rename_changes_folder_and_label(tmp_path):
    snap = _make_snapshot(tmp_path, label="旧名字")
    new = core.rename_snapshot(snap, "打女武神前")

    assert not snap.exists(), "旧目录应该已经不存在"
    assert new.is_dir()
    assert new.name.startswith("打女武神前")
    # 时间戳必须保留，否则同一天的两个快照会分不清先后
    assert core._TS_IN_NAME.search(new.name)

    manifest = json.loads((new / core.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["label"] == "打女武神前"
    # 改名不能动存档内容
    assert (new / core.DATA_DIR / "slot1.sl2").read_bytes() == b"alpha"
    assert core.verify_snapshot(new).ok


def test_rename_keeps_manifest_of_unnamed_snapshot(tmp_path):
    snap = _make_snapshot(tmp_path)
    assert core._TS_IN_NAME.search(snap.name), "未命名快照名应该只有时间戳"
    new = core.rename_snapshot(snap, "第一次通关")
    assert new.name.startswith("第一次通关")
    assert core._TS_IN_NAME.search(new.name)


def test_rename_to_empty_keeps_timestamp_only(tmp_path):
    snap = _make_snapshot(tmp_path, label="要删掉的名字")
    new = core.rename_snapshot(snap, "   ")
    assert core._TS_IN_NAME.search(new.name)
    assert new.name == core._TS_IN_NAME.search(new.name).group(0)
    manifest = json.loads((new / core.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["label"] == ""


def test_rename_strips_illegal_chars_but_keeps_chinese(tmp_path):
    snap = _make_snapshot(tmp_path)
    new = core.rename_snapshot(snap, 'a/b:c*打?女<武>神|前"')
    assert not any(ch in new.name for ch in '<>:"/\\|?*')
    assert "打" in new.name and "女" in new.name
    # 原文完整保留在 manifest 里，文件夹名只是给人看的
    manifest = json.loads((new / core.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["label"] == 'a/b:c*打?女<武>神|前"'


def test_rename_collision_does_not_clobber_other_snapshot(tmp_path):
    first = _make_snapshot(tmp_path, label="甲")
    # 同秒撞名不会稳定复现，直接造一个同时间戳的第二个快照更可靠
    second_dir = first.parent / ("乙_" + core._TS_IN_NAME.search(first.name).group(1))
    shutil.copytree(first, second_dir)
    new = core.rename_snapshot(second_dir, "甲")

    assert first.is_dir(), "先存在的同名快照不能被覆盖"
    assert new != second_dir, "撞名时要自动加后缀"
    assert new.is_dir()
    assert (new / core.DATA_DIR / "slot1.sl2").read_bytes() == b"alpha"


def test_rename_rejects_non_snapshot_dir(tmp_path):
    bogus = tmp_path / "not-a-snapshot"
    bogus.mkdir()
    with pytest.raises(core.IntegrityError):
        core.rename_snapshot(bogus, "随便")


def test_snapshot_timestamp_falls_back_to_manifest(tmp_path):
    # 手工把文件夹改成没有时间戳的名字，应能从 manifest 的 created_at 还原
    snap = _make_snapshot(tmp_path)
    odd = snap.parent / "no-timestamp-here"
    snap.rename(odd)
    assert core.snapshot_timestamp(odd) == core._TS_IN_NAME.search(snap.name).group(1)
