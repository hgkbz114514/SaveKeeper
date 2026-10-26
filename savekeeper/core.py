"""存档快照引擎：导出（备份）/ 校验 / 还原（导入）。

快照目录结构：
    <备份根>/<游戏id>/<标签>_<时间戳>/
        manifest.json      清单：每个文件的 sha256 / 大小 / 相对路径
        data/              存档目录的完整镜像

安全性约束：
- 还原前强制校验 manifest（sha256 逐文件比对），失败即中止，不覆盖任何文件；
- 还原前自动把当前存档再打一份带时间戳的快照，任何还原都可回退；
- 只读写存档目录内的文件，绝不触碰游戏本体与注册表。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
DATA_DIR = "data"
FORMAT_ID = "savekeeper/1"
_CHUNK = 1024 * 1024


class IntegrityError(Exception):
    """存档完整性校验失败。"""


# --------------------------------------------------------------------------- #
# 哈希与清单
# --------------------------------------------------------------------------- #

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _scan_tree(root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(dirpath)
        for name in sorted(filenames):
            full = base / name
            if full.is_symlink() and not full.exists():
                continue
            try:
                stat = full.stat()
                files.append(
                    {
                        "path": full.relative_to(root).as_posix(),
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "sha256": sha256_file(full),
                    }
                )
            except OSError:
                continue
    return files


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _unique_dir(base: Path) -> Path:
    """同一秒内创建多个快照时自动追加序号，避免 FileExistsError。"""
    if not base.exists():
        return base
    for i in range(2, 200):
        candidate = base.with_name(f"{base.name}_{i}")
        if not candidate.exists():
            return candidate
    raise OSError(f"无法创建唯一快照目录：{base}")


_ILLEGAL = set('<>:"/\\|?*')


def _safe_label(label: str) -> str:
    """把备注压成合法文件夹名。

    保留 Unicode（NTFS 用 UTF-16 存路径，中文目录名完全合法且用户更易辨认），
    只做三件事：替换 Windows 非法字符、压掉控制字符、限制长度。
    原文另外完整保存在 manifest 的 label 字段里，文件夹名只是给人看的。
    """
    cleaned = []
    for ch in label.strip():
        if ch in _ILLEGAL or ord(ch) < 32:
            cleaned.append("_")
        else:
            cleaned.append(ch)
    name = "".join(cleaned).strip(" .")  # 尾部的点和空格 Windows 不接受
    name = "_".join(part for part in name.split("_") if part)
    return name[:40]


# --------------------------------------------------------------------------- #
# 导出（备份）
# --------------------------------------------------------------------------- #

def create_snapshot(
    source_dir: Path,
    backup_root: Path,
    game_id: str,
    game_name: str,
    label: str = "",
) -> Path:
    """把 source_dir 完整复制成快照，返回快照目录。"""
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"存档目录不存在：{source_dir}")

    files = _scan_tree(source_dir)
    if not files:
        raise IntegrityError(f"存档目录为空，未导出任何文件：{source_dir}")

    safe = _safe_label(label)
    folder = f"{safe}_{_timestamp()}" if safe else _timestamp()
    snap_dir = _unique_dir(Path(backup_root) / game_id / folder)
    data_dir = snap_dir / DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=False)

    for item in files:
        src = source_dir / item["path"]
        dst = data_dir / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    manifest = {
        "format": FORMAT_ID,
        "game_id": game_id,
        "game_name": game_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "source_path": str(source_dir),
        "file_count": len(files),
        "total_bytes": sum(f["size"] for f in files),
        "files": files,
    }
    (snap_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return snap_dir


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

@dataclass
class VerifyReport:
    ok: bool
    snapshot: Path
    file_count: int = 0
    total_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        state = "校验通过" if self.ok else "校验失败"
        return (
            f"{state}：{self.file_count} 个文件 / {self.total_bytes} 字节；"
            f"错误 {len(self.errors)} 项，警告 {len(self.warnings)} 项"
        )


def verify_snapshot(snapshot_dir: Path) -> VerifyReport:
    """逐文件比对 sha256。任何缺失/哈希不符/大小不符 → 校验失败。"""
    snapshot_dir = Path(snapshot_dir)
    report = VerifyReport(ok=False, snapshot=snapshot_dir)

    manifest_path = snapshot_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        report.errors.append(f"缺少 {MANIFEST_NAME}，这不是一个有效的 SaveKeeper 快照")
        return report

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        report.errors.append(f"manifest.json 解析失败：{exc}")
        return report

    if manifest.get("format") != FORMAT_ID:
        report.warnings.append(f"清单格式标识为 {manifest.get('format')}，与当前版本不完全一致")

    data_dir = snapshot_dir / DATA_DIR
    if not data_dir.is_dir():
        report.errors.append(f"缺少 {DATA_DIR}/ 数据目录")
        return report

    entries = manifest.get("files", [])
    report.file_count = len(entries)
    report.total_bytes = int(manifest.get("total_bytes") or 0)

    for entry in entries:
        rel = entry.get("path", "")
        target = data_dir / rel
        if not target.is_file():
            report.errors.append(f"文件缺失：{rel}")
            continue
        actual = sha256_file(target)
        if actual != entry.get("sha256"):
            report.errors.append(
                f"哈希不符：{rel}（清单 {str(entry.get('sha256'))[:12]}… / 实际 {actual[:12]}…）"
            )
        elif target.stat().st_size != entry.get("size"):
            report.errors.append(f"大小不符：{rel}")

    listed = {e.get("path") for e in entries}
    for dirpath, _dirs, filenames in os.walk(data_dir):
        base = Path(dirpath)
        for name in filenames:
            rel = (base / name).relative_to(data_dir).as_posix()
            if rel not in listed:
                report.warnings.append(f"清单外多出文件：{rel}")

    report.ok = not report.errors
    return report


def verify_directory(source_dir: Path) -> VerifyReport:
    """校验任意文件夹是否可作为存档导入（非空、可读、内容完整）。"""
    source_dir = Path(source_dir)
    report = VerifyReport(ok=False, snapshot=source_dir)
    if not source_dir.is_dir():
        report.errors.append(f"目录不存在：{source_dir}")
        return report
    files = _scan_tree(source_dir)
    if not files:
        report.errors.append("目录为空，没有可导入的存档文件")
        return report
    report.ok = True
    report.file_count = len(files)
    report.total_bytes = sum(f["size"] for f in files)
    report.warnings.append("外部文件夹导入：无 manifest，仅校验了非空与可读性")
    return report


# --------------------------------------------------------------------------- #
# 还原（导入）
# --------------------------------------------------------------------------- #

@dataclass
class RestoreReport:
    ok: bool
    target: Path
    safety_backup: Path | None = None
    restored_files: int = 0
    restored_bytes: int = 0
    message: str = ""


def restore_snapshot(
    snapshot_dir: Path,
    target_dir: Path,
    backup_root: Path,
    game_id: str,
    game_name: str,
) -> RestoreReport:
    """校验 → 自动备份当前存档 → 覆盖还原。校验失败直接抛 IntegrityError。"""
    snapshot_dir = Path(snapshot_dir)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    report = verify_snapshot(snapshot_dir)
    if not report.ok:
        raise IntegrityError(
            "存档完整性校验失败，已阻止覆盖：\n- " + "\n- ".join(report.errors[:10])
        )

    # 导入前自动备份当前存档（可回退）
    current = [f for f in _scan_tree(target_dir)]
    if current:
        safety = create_snapshot(
            target_dir, backup_root, game_id, f"{game_name} · 导入前自动备份",
            label="导入前自动备份",
        )
        report_safety: Path | None = safety
    else:
        report_safety = None

    data_dir = snapshot_dir / DATA_DIR
    copied = 0
    copied_bytes = 0
    for dirpath, _dirs, filenames in os.walk(data_dir):
        base = Path(dirpath)
        for name in filenames:
            rel = (base / name).relative_to(data_dir)
            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / name, dst)
            copied += 1
            copied_bytes += dst.stat().st_size

    return RestoreReport(
        ok=True,
        target=target_dir,
        safety_backup=report_safety,
        restored_files=copied,
        restored_bytes=copied_bytes,
        message=f"已还原 {copied} 个文件（{copied_bytes} 字节）到 {target_dir}",
    )


def import_directory(
    source_dir: Path,
    target_dir: Path,
    backup_root: Path,
    game_id: str,
    game_name: str,
) -> RestoreReport:
    """从外部文件夹导入存档：先校验非空，再自动备份，最后覆盖。"""
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)

    check = verify_directory(source_dir)
    if not check.ok:
        raise IntegrityError(
            "待导入的存档校验失败，已阻止覆盖：\n- " + "\n- ".join(check.errors[:10])
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    safety = None
    if _scan_tree(target_dir):
        safety = create_snapshot(
            target_dir, backup_root, game_id, f"{game_name} · 导入前自动备份",
            label="导入前自动备份",
        )

    copied = 0
    copied_bytes = 0
    for dirpath, _dirs, filenames in os.walk(source_dir):
        base = Path(dirpath)
        for name in filenames:
            rel = (base / name).relative_to(source_dir)
            dst = target_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / name, dst)
            copied += 1
            copied_bytes += dst.stat().st_size

    return RestoreReport(
        ok=True,
        target=target_dir,
        safety_backup=safety,
        restored_files=copied,
        restored_bytes=copied_bytes,
        message=f"已从 {source_dir} 导入 {copied} 个文件（{copied_bytes} 字节）",
    )


# --------------------------------------------------------------------------- #
# 备份管理
# --------------------------------------------------------------------------- #

def list_snapshots(backup_root: Path, game_id: str) -> list[dict[str, Any]]:
    """列出某游戏的全部快照（新的在前），附带实时校验结果。"""
    root = Path(backup_root) / game_id
    if not root.is_dir():
        return []
    # 按目录 mtime 倒序（最新的在前），同一时刻再按名称倒序
    dirs = [d for d in root.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: (d.stat().st_mtime, d.name), reverse=True)
    items: list[dict[str, Any]] = []
    for snap in dirs:
        manifest_path = snap / MANIFEST_NAME
        info: dict[str, Any] = {
            "path": snap,
            "name": snap.name,
            "has_manifest": manifest_path.is_file(),
        }
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                info["created_at"] = manifest.get("created_at", "")
                info["label"] = manifest.get("label", "")
                info["game_name"] = manifest.get("game_name", "")
                info["file_count"] = manifest.get("file_count", 0)
                info["total_bytes"] = manifest.get("total_bytes", 0)
            except (json.JSONDecodeError, OSError):
                info["created_at"] = ""
        verify = verify_snapshot(snap)
        info["ok"] = verify.ok
        info["verify_summary"] = verify.summary()
        items.append(info)
    return items


def delete_snapshot(snapshot_dir: Path) -> None:
    shutil.rmtree(Path(snapshot_dir))


_TS_IN_NAME = re.compile(r"(\d{8}_\d{6})(?:_\d+)?$")


def snapshot_timestamp(snapshot_dir: Path) -> str:
    """从快照文件夹名里取出时间戳部分；取不到就退化到 manifest 的 created_at。"""
    match = _TS_IN_NAME.search(Path(snapshot_dir).name)
    if match:
        return match.group(1)
    manifest_path = Path(snapshot_dir) / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw = str(manifest.get("created_at", ""))
        stamp = datetime.fromisoformat(raw)
        return stamp.strftime("%Y%m%d_%H%M%S")
    except (OSError, json.JSONDecodeError, ValueError):
        return _timestamp()


def rename_snapshot(snapshot_dir: Path, new_label: str) -> Path:
    """改快照的备注名，同时把文件夹名跟着改，返回新的快照目录。

    名字的权威来源是 manifest 里的 ``label``（可以存中文原文）；
    文件夹名只是给人看的，所以要跟着一起改，否则备份目录里一堆
    ``20261025_211235`` 根本分不清哪个是哪个。
    """
    snapshot_dir = Path(snapshot_dir)
    manifest_path = snapshot_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise IntegrityError(f"缺少 {MANIFEST_NAME}，这不是一个有效的 SaveKeeper 快照")

    label = new_label.strip()
    safe = _safe_label(label)
    stamp = snapshot_timestamp(snapshot_dir)
    new_name = f"{safe}_{stamp}" if safe else stamp

    target = snapshot_dir.parent / new_name
    # 同名已存在就退到 _unique_dir，绝不动别人的快照
    if target.resolve() != snapshot_dir.resolve():
        target = _unique_dir(snapshot_dir.parent / new_name)

    if target != snapshot_dir:
        snapshot_dir.rename(target)
        manifest_path = target / MANIFEST_NAME

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["label"] = label
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, json.JSONDecodeError) as exc:
        # 清单写不进去就把文件夹改回去，别留一个改了一半的状态
        if target != snapshot_dir:
            try:
                target.rename(snapshot_dir)
            except OSError:
                pass
        raise IntegrityError(f"写入 {MANIFEST_NAME} 失败：{exc}") from exc

    return target


def export_snapshot_to(snapshot_dir: Path, dest_dir: Path) -> RestoreReport:
    """把快照里的存档文件摊平复制到 dest_dir，用于发给别人 / 传网盘。

    只复制 ``data/`` 下的存档本体，不复制 manifest.json —— 对方要的是能直接
    丢进游戏目录的文件，不是 SaveKeeper 的内部结构。目标重名时落到
    ``<快照名>/`` 子目录，避免把用户自己挑的文件夹搅乱。
    """
    snapshot_dir = Path(snapshot_dir)
    dest_dir = Path(dest_dir)
    check = verify_snapshot(snapshot_dir)
    if not check.ok:
        raise IntegrityError(
            "快照已损坏，已阻止导出：\n- " + "\n- ".join(check.errors[:10])
        )

    data_dir = snapshot_dir / DATA_DIR
    if not data_dir.is_dir():
        raise IntegrityError(f"快照里没有 {DATA_DIR}/ 目录：{snapshot_dir}")

    # 目标目录已有同名文件就把东西放进子目录，别去覆盖用户别的东西
    top_names = {p.name for p in data_dir.iterdir()}
    clashes = any((dest_dir / n).exists() for n in top_names)
    out_dir = dest_dir / snapshot_dir.name if clashes else dest_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    copied_bytes = 0
    for dirpath, _dirs, filenames in os.walk(data_dir):
        base = Path(dirpath)
        for name in filenames:
            rel = (base / name).relative_to(data_dir)
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base / name, dst)
            copied += 1
            copied_bytes += dst.stat().st_size

    return RestoreReport(
        ok=True,
        target=out_dir,
        restored_files=copied,
        restored_bytes=copied_bytes,
        message=f"已导出 {copied} 个文件（{copied_bytes} 字节）到 {out_dir}",
    )
