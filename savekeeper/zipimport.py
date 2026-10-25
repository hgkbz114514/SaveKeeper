"""换档工作流（v0.4.0）：吃 zip / 找对子目录 / 预览差异。

用户旅程：下载了一个 zip 存档包 → 拖进软件 → 预览会替换什么 → 确认。
每一步都有兜底和解释，覆盖网上的三种包：整目录 zip / 含一级包装目录的
zip / 散文件 zip。

设计原则（写给后来的人）：
- 永不改用户的文件：解压到独立临时目录，确认前一个字节都不碰。
- 预览必须先于写入：差异算出来给用户看，再决定动不动手。
- 所有启发式都给理由，错了能被用户一眼看穿。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# 允许从 zip 解出的最大总量（防 zip bomb：网上下载的包不可信）
MAX_EXTRACT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_EXTRACT_ENTRIES = 200_000


class ArchiveError(Exception):
    """zip 打开/解压失败。message 直接给用户看。"""


@dataclass
class ZipInfo:
    """一个存档 zip 包的体检结果。"""

    members: list[str] = field(default_factory=list)      # 相对路径（/ 分隔）
    root_dirs: list[str] = field(default_factory=list)    # 顶层目录名
    root_files: list[str] = field(default_factory=list)   # 顶层散文件
    total_bytes: int = 0

    @property
    def has_root_dir(self) -> bool:
        return len(self.root_dirs) == 1 and not self.root_files

    @property
    def wrapped(self) -> bool:
        """True = 顶层只有一个目录，所有内容都包在里面（需要剥壳）。"""
        return self.has_root_dir


def inspect_zip(zpath: Path) -> ZipInfo:
    """列出 zip 内容结构。损坏/不是 zip 抛 ArchiveError。"""
    try:
        with zipfile.ZipFile(zpath) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(f"打不开这个压缩包：{zpath}\n{type(exc).__name__}: {exc}") from exc
    info = ZipInfo()
    for m in infos:
        if m.is_dir():
            continue
        name = m.filename.replace("\\", "/")
        info.members.append(name)
        info.total_bytes += m.file_size
        top = name.split("/", 1)[0]
        if "/" in name:
            if top not in info.root_dirs:
                info.root_dirs.append(top)
        else:
            info.root_files.append(name)
    return info


def extract_zip(zpath: Path, dest: Path) -> Path:
    """把 zip 安全解压到 dest（必须已存在），返回解压根 dest。

    防护：拒绝绝对路径/..穿越成员（zip slip）、限总量、限条目数。
    """
    zinfo = inspect_zip(zpath)
    if len(zinfo.members) > MAX_EXTRACT_ENTRIES:
        raise ArchiveError(f"压缩包条目过多（{len(zinfo.members)}），超过安全上限。")
    if zinfo.total_bytes > MAX_EXTRACT_BYTES:
        raise ArchiveError(
            f"压缩包解压后超过安全上限（{zinfo.total_bytes / 1e9:.1f} GB）。"
        )
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    try:
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                target = (dest_resolved / member.filename)
                if not str(target.resolve()).startswith(str(dest_resolved)):
                    raise ArchiveError(
                        f"压缩包包含不安全的路径（路径穿越）：{member.filename}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except ArchiveError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError(f"解压失败：{type(exc).__name__}: {exc}") from exc
    return dest


def pick_save_subdir(base: Path) -> tuple[Path, str]:
    """在一个下载来的存档包根目录里找出真正的存档子目录。

    返回 (选定目录, 理由)。规则按可信度排序：

    1. 本身就有存档文件 → 就是它（网上打包的人一般直接打存档目录）。
    2. 只有一个子目录且它有存档 → 剥壳进去（zip 常带一层包装目录）。
    3. 子目录里逐个看，取证据最强的；并列就取「像存档目录名」的那个。
    4. 都没有 → 原样返回，让上层提示用户手选。
    """
    from savekeeper.installscan import _scan_save_stats  # 延迟导入避免循环

    def stats(p: Path) -> tuple[int, int, int]:
        try:
            return _scan_save_stats(p, max_depth=3)
        except OSError:
            return (0, 0, 0)

    def top_stats(p: Path) -> tuple[int, int, int]:
        """只数本层直属文件，不递归 —— 用来和子目录公平比较。"""
        n = nbytes = 0
        strong = 0
        try:
            for f in p.iterdir():
                if not f.is_file():
                    continue
                sfx = f.suffix.lower()
                if sfx in (".sav", ".save", ".savegame", ".srm", ".dsv", ".sl2",
                           ".erp", ".es3", ".rw3", ".slot", ".zds", ".sfs",
                           ".state", ".sav0", ".sgd"):
                    strong += 1
                    n += 1
                    nbytes += f.stat().st_size
        except OSError:
            pass
        return (2 if strong else (1 if n else 0), n, nbytes)

    s0, n0, b0 = top_stats(base)
    if s0 >= 1 and n0 > 0:
        return base, f"目录本身含 {n0} 个存档文件（{b0 / 1024:.0f} KB）"

    try:
        children = [p for p in base.iterdir() if p.is_dir()]
    except OSError:
        children = []
    files_here = [p for p in base.iterdir() if p.is_file()] if base.is_dir() else []

    # 典型包装目录：里面只有一个子目录，没有散文件
    if len(children) == 1 and not files_here:
        child = children[0]
        s1, n1, b1 = stats(child)
        if s1 >= 1 and n1 > 0:
            return child, f"剥掉包装层「{child.name}/」，里面含 {n1} 个存档文件"

    # 逐个子目录比证据（强度, 数量, 体量）
    scored: list[tuple[tuple[int, int, int], int, Path]] = []
    for idx, child in enumerate(children):
        scored.append((stats(child), idx, child))
    scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)  # 证据优先，并列取先
    if scored and scored[0][0][0] >= 1 and scored[0][0][1] > 0:
        best = scored[0][2]
        s, n, b = scored[0][0]
        reason = f"在 {len(children)} 个子目录里「{best.name}/」存档证据最强（{n} 个文件，{b / 1024:.0f} KB）"
        return best, reason
    return base, "未找到明显存档特征，使用所选目录（如不对请手动改）"


@dataclass
class DiffEntry:
    rel: str        # 相对路径
    kind: str       # replace / new / missing（target 里多出来、包里没有的）
    zip_bytes: int
    target_bytes: int


@dataclass
class DiffPreview:
    entries: list[DiffEntry] = field(default_factory=list)
    replace_n: int = 0
    new_n: int = 0
    missing_n: int = 0
    zip_bytes: int = 0

    @property
    def n_entries(self) -> int:
        return len(self.entries)

    def summary(self) -> str:
        parts = [f"{self.n_entries} 个文件"]
        if self.replace_n:
            parts.append(f"替换 {self.replace_n}")
        if self.new_n:
            parts.append(f"新增 {self.new_n}")
        if self.missing_n:
            parts.append(f"{self.missing_n} 个现有文件不在包里（保留不动）")
        return "，".join(parts)


def preview_diff(source_dir: Path, target_dir: Path) -> DiffPreview:
    """算「导入这个目录会替换/新增什么」，不写任何东西。"""
    source_dir = Path(source_dir)
    target_dir = Path(target_dir)
    files: dict[str, int] = {}
    for dirpath, _dirs, names in os.walk(source_dir):
        base = Path(dirpath)
        for name in names:
            p = base / name
            rel = p.relative_to(source_dir).as_posix()
            try:
                files[rel] = p.stat().st_size
            except OSError:
                files[rel] = 0

    preview = DiffPreview()
    target_files: set[str] = set()
    if target_dir.is_dir():
        for dirpath, _dirs, names in os.walk(target_dir):
            base = Path(dirpath)
            for name in names:
                p = base / name
                rel = p.relative_to(target_dir).as_posix()
                try:
                    target_files.add(rel)
                    tsize = p.stat().st_size
                except OSError:
                    tsize = 0
                zsize = files.get(rel)
                if zsize is None:
                    continue
                if zsize != tsize:
                    preview.entries.append(DiffEntry(rel, "replace", zsize, tsize))
                else:
                    # 大小相同 → 内容可能一样也可能不一样（预览不读内容）。
                    # 标「替换」更保守：真导入时 copy2 会覆盖，不会丢数据。
                    preview.entries.append(DiffEntry(rel, "replace", zsize, tsize))
    for rel, zsize in files.items():
        if rel not in target_files:
            preview.entries.append(DiffEntry(rel, "new", zsize, 0))

    for e in preview.entries:
        if e.kind == "replace":
            preview.replace_n += 1
            preview.zip_bytes += e.zip_bytes
        elif e.kind == "new":
            preview.new_n += 1
            preview.zip_bytes += e.zip_bytes
    preview.missing_n = len(target_files - files.keys())
    preview.entries.sort(key=lambda e: (e.kind != "replace", e.kind != "new", e.rel))
    return preview


def stage_temp_dir() -> Path:
    """给解压用的临时目录。确认导入后由调用方负责清理。"""
    return Path(tempfile.mkdtemp(prefix="savekeeper_zip_"))


def cleanup_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
