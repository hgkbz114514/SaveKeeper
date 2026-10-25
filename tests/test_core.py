"""核心引擎测试：导出 / 校验 / 还原 / 完整性阻断。

用 tmp_path 造真实存档目录，不依赖任何已安装游戏。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from savekeeper import core


def make_save(tmp_path: Path, name: str = "save") -> Path:
    root = tmp_path / "games" / name
    (root / "saves" / "profile1").mkdir(parents=True)
    (root / "config.ini").write_text("[graphics]\nfullscreen=1\n", encoding="utf-8")
    (root / "saves" / "profile1" / "slot1.dat").write_bytes(os.urandom(4096))
    (root / "saves" / "profile1" / "notes.txt").write_text("通关存档 2026", encoding="utf-8")
    return root


def test_export_creates_manifest_and_data(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    snap = core.create_snapshot(src, tmp_path / "backups", "testgame", "测试游戏")

    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "savekeeper/1"
    assert manifest["file_count"] == 3
    assert manifest["game_name"] == "测试游戏"
    assert (snap / "data" / "config.ini").is_file()
    assert (snap / "data" / "saves" / "profile1" / "slot1.dat").is_file()
    # 原目录不被改动
    assert src.joinpath("config.ini").read_text(encoding="utf-8").startswith("[graphics]")


def test_verify_ok(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    snap = core.create_snapshot(src, tmp_path / "backups", "g", "G")
    report = core.verify_snapshot(snap)
    assert report.ok, report.errors
    assert report.file_count == 3


@pytest.mark.parametrize("mutation", ["delete", "corrupt", "append", "empty"])
def test_verify_detects_tampering(tmp_path: Path, mutation: str) -> None:
    src = make_save(tmp_path)
    snap = core.create_snapshot(src, tmp_path / "backups", "g", "G")
    victim = snap / "data" / "config.ini"

    if mutation == "delete":
        victim.unlink()
    elif mutation == "corrupt":
        victim.write_text("hacked", encoding="utf-8")
    elif mutation == "append":
        with victim.open("ab") as fh:
            fh.write(b"extra")
    elif mutation == "empty":
        victim.write_bytes(b"")

    report = core.verify_snapshot(snap)
    assert not report.ok
    assert report.errors


def test_restore_roundtrip_no_data_loss(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    backups = tmp_path / "backups"
    snap = core.create_snapshot(src, backups, "g", "G")

    # 玩家继续游戏，存档变了
    (src / "saves" / "profile1" / "slot1.dat").write_bytes(os.urandom(2048))
    (src / "new_file.txt").write_text("新周目", encoding="utf-8")
    before = {p.name for p in src.rglob("*") if p.is_file()}

    report = core.restore_snapshot(snap, src, backups, "g", "G")
    assert report.ok
    assert report.restored_files == 3

    after = {p.name for p in src.rglob("*") if p.is_file()}
    assert after == before  # 没有文件丢失

    verify = core.verify_snapshot(snap)
    # 还原后当前存档与快照逐字节一致
    for entry in json.loads((snap / "manifest.json").read_text(encoding="utf-8"))["files"]:
        current = src / entry["path"]
        assert current.is_file()
        assert core.sha256_file(current) == entry["sha256"], entry["path"]


def test_restore_blocked_when_snapshot_corrupt(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    backups = tmp_path / "backups"
    snap = core.create_snapshot(src, backups, "g", "G")
    (snap / "data" / "config.ini").write_text("tampered", encoding="utf-8")

    original = (src / "config.ini").read_text(encoding="utf-8")
    with pytest.raises(core.IntegrityError) as excinfo:
        core.restore_snapshot(snap, src, backups, "g", "G")
    assert "校验失败" in str(excinfo.value)
    # 关键约束：校验失败时当前存档一个字节都没被覆盖
    assert (src / "config.ini").read_text(encoding="utf-8") == original


def test_restore_creates_safety_backup(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    backups = tmp_path / "backups"
    snap = core.create_snapshot(src, backups, "g", "G")

    (src / "config.ini").write_text("[graphics]\nfullscreen=0\n", encoding="utf-8")
    report = core.restore_snapshot(snap, src, backups, "g", "G")
    assert report.safety_backup is not None
    assert "导入前自动备份" in json.loads(
        (report.safety_backup / "manifest.json").read_text(encoding="utf-8")
    )["label"]

    # 自动备份里保存的是导入前的旧状态
    restored = core.verify_snapshot(report.safety_backup)
    assert restored.ok
    safety_data = report.safety_backup / "data" / "config.ini"
    assert "fullscreen=0" in safety_data.read_text(encoding="utf-8")


def test_import_directory_roundtrip(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    backups = tmp_path / "backups"
    external = tmp_path / "external_save"
    external.mkdir()
    (external / "config.ini").write_text("[graphics]\nfullscreen=1\n", encoding="utf-8")
    (external / "slot1.dat").write_bytes(b"X" * 512)

    report = core.import_directory(external, src, backups, "g", "G")
    assert report.ok
    assert report.restored_files == 2
    assert report.safety_backup is not None
    assert (src / "config.ini").read_text(encoding="utf-8").startswith("[graphics]")


def test_import_empty_directory_blocked(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(core.IntegrityError):
        core.import_directory(empty, src, tmp_path / "backups", "g", "G")
    assert (src / "config.ini").exists()


def test_list_and_delete_snapshot(tmp_path: Path) -> None:
    src = make_save(tmp_path)
    backups = tmp_path / "backups"
    snap = core.create_snapshot(src, backups, "g", "G", label="通关前")
    time.sleep(0.01)
    core.create_snapshot(src, backups, "g", "G", label="二周目")

    snaps = core.list_snapshots(backups, "g")
    assert len(snaps) == 2
    assert snaps[0].get("label") == "二周目"  # 新的在前

    core.delete_snapshot(snap)
    assert len(core.list_snapshots(backups, "g")) == 1


def test_empty_source_dir_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty_src"
    empty.mkdir()
    with pytest.raises(core.IntegrityError):
        core.create_snapshot(empty, tmp_path / "backups", "g", "G")


def test_chinese_label_kept_in_folder_name(tmp_path: Path) -> None:
    """中文备注必须保留在文件夹名里——否则中文用户所有快照都叫 `-_时间戳`。"""
    src = make_save(tmp_path)
    snap = core.create_snapshot(src, tmp_path / "backups", "g", "G", label="打女武神前")
    assert "打女武神前" in snap.name
    # 快照仍然可被正常列出与校验
    (snap_info,) = core.list_snapshots(tmp_path / "backups", "g")
    assert snap_info["label"] == "打女武神前"
    assert snap_info["ok"] is True


@pytest.mark.parametrize(
    "raw, must_not_contain",
    [
        ("a/b", "/"),
        ("a\\b", "\\"),
        ('a:b*c?d"e<f>g|h', ":"),
        ("trailing...  ", " "),
    ],
)
def test_safe_label_strips_illegal_chars(raw: str, must_not_contain: str) -> None:
    out = core._safe_label(raw)
    assert must_not_contain not in out
    assert out == out.strip(" .")
