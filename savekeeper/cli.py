"""命令行入口：无 GUI 环境下同样可用全部功能。

示例：
    python -m savekeeper list
    python -m savekeeper path eldenring
    python -m savekeeper open eldenring
    python -m savekeeper export eldenring --label 通关前
    python -m savekeeper snapshots eldenring
    python -m savekeeper import eldenring --snapshot 20260101_120000
    python -m savekeeper import eldenring --from D:/some/folder
    python -m savekeeper add "我的游戏" "D:/Save/MyGame"
    python -m savekeeper setpath eldenring "D:/Save/EldenRing"
    python -m savekeeper config
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config as cfg_mod
from . import core, games as games_mod, paths


def _fix_console() -> None:
    """Windows GBK 控制台下安全输出中文：失败也无所谓，只是不崩。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _load() -> tuple[cfg_mod.Config, dict[str, games_mod.Game]]:
    cfg = cfg_mod.load()
    return cfg, {g.id: g for g in cfg.all_games()}


def _resolve(cfg: cfg_mod.Config, catalog: dict[str, games_mod.Game], game_id: str) -> Path:
    game = catalog.get(game_id)
    if game is None:
        raise SystemExit(f"未知游戏 id：{game_id}（用 `list` 查看全部）")
    resolved = games_mod.resolve_game_path(game)
    if resolved is None:
        raise SystemExit(f"未检测到 {game.name} 的存档目录，请先 setpath 指定")
    return resolved


def cmd_list(args: argparse.Namespace) -> int:
    cfg, catalog = _load()
    print(f"备份根目录：{cfg.backup_dir()}\n")
    print(f"{'ID':<16}{'游戏':<28}{'平台':<12}{'存档目录'}")
    print("-" * 90)
    for game in cfg.all_games():
        resolved = games_mod.resolve_game_path(game)
        mark = "✓" if resolved else " "
        print(f"{game.id:<16}{game.name:<28}{game.platform:<12}{mark} {resolved or '未检测到'}")
    return 0


def cmd_path(args: argparse.Namespace) -> int:
    cfg, catalog = _load()
    resolved = _resolve(cfg, catalog, args.game)
    print(resolved)
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    cfg, catalog = _load()
    resolved = _resolve(cfg, catalog, args.game)
    paths.open_in_explorer(resolved)
    print(f"已打开 {resolved}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    cfg, catalog = _load()
    resolved = _resolve(cfg, catalog, args.game)
    snap = core.create_snapshot(
        resolved, cfg.backup_dir(), args.game, catalog[args.game].name, args.label or ""
    )
    report = core.verify_snapshot(snap)
    print(f"已导出：{snap}")
    print(report.summary())
    if not report.ok:
        print("\n".join(report.errors[:10]), file=sys.stderr)
        return 2
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    cfg, _ = _load()
    snaps = core.list_snapshots(cfg.backup_dir(), args.game)
    if not snaps:
        print("（暂无备份）")
        return 0
    print(f"{'快照':<44}{'时间':<22}{'文件':>6}{'大小':>10}  校验")
    print("-" * 96)
    for s in snaps:
        print(
            f"{s['name']:<44}{(s.get('created_at') or '').replace('T', ' ')[:19]:<22}"
            f"{s.get('file_count', 0):>6}{paths.human_size(s.get('total_bytes', 0)):>10}  "
            f"{'通过' if s['ok'] else '损坏'}"
        )
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    cfg, catalog = _load()
    resolved = _resolve(cfg, catalog, args.game)
    if args.snapshot:
        snap = cfg.backup_dir() / args.game / args.snapshot
        if not snap.is_dir():
            raise SystemExit(f"快照不存在：{snap}")
        report = core.restore_snapshot(snap, resolved, cfg.backup_dir(), args.game, catalog[args.game].name)
    elif args.from_dir:
        report = core.import_directory(
            Path(args.from_dir), resolved, cfg.backup_dir(), args.game, catalog[args.game].name
        )
    else:
        raise SystemExit("请指定 --snapshot <快照名> 或 --from <文件夹>")
    print(report.message)
    if report.safety_backup:
        print(f"导入前自动备份：{report.safety_backup}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    report = core.verify_snapshot(Path(args.path))
    print(report.summary())
    for line in report.errors[:20]:
        print("  [错误]", line)
    for line in report.warnings[:20]:
        print("  [警告]", line)
    return 0 if report.ok else 2


def _restore_one(game_id: str, snap_name: str) -> int:
    """校验 + 自动备份 + 覆盖。swap / latest 共用。"""
    cfg, catalog = _load()
    resolved = _resolve(cfg, catalog, game_id)
    snap = cfg.backup_dir() / game_id / snap_name
    if not snap.is_dir():
        print(f"[中止] 快照不存在：{snap}", file=sys.stderr)
        return 2
    try:
        report = core.restore_snapshot(
            snap, resolved, cfg.backup_dir(), game_id, catalog[game_id].name
        )
    except core.IntegrityError as exc:
        print(f"[中止] {exc}", file=sys.stderr)
        return 2
    print(report.message)
    if report.safety_backup:
        print(f"导入前自动备份：{report.safety_backup.name}（可回退）")
    return 0


def cmd_swap(args: argparse.Namespace) -> int:
    return _restore_one(args.game, args.snapshot)


def cmd_latest(args: argparse.Namespace) -> int:
    cfg, _ = _load()
    snaps = core.list_snapshots(cfg.backup_dir(), args.game)
    if not snaps:
        print(f"[中止] {args.game} 还没有任何快照，先跑一次 export", file=sys.stderr)
        return 2
    usable = [s for s in snaps if s["ok"]]
    if not usable:
        print(f"[中止] {args.game} 的快照全部校验失败，不敢覆盖", file=sys.stderr)
        return 2
    print(f"→ 使用最新可用快照：{usable[0]['name']}")
    return _restore_one(args.game, usable[0]["name"])


def cmd_export_snapshot(args: argparse.Namespace) -> int:
    cfg, _ = _load()
    snap = cfg.backup_dir() / args.game / args.snapshot
    if not snap.is_dir():
        print(f"[中止] 快照不存在：{snap}", file=sys.stderr)
        return 2
    try:
        report = core.export_snapshot_to(snap, Path(args.dest))
    except core.IntegrityError as exc:
        print(f"[中止] {exc}", file=sys.stderr)
        return 2
    print(report.message)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load()
    cfg.add_custom_game(args.name, args.path)
    cfg_mod.save(cfg)
    print(f"已添加：{args.name} → {args.path}")
    return 0


def cmd_setpath(args: argparse.Namespace) -> int:
    cfg, catalog = _load()
    if args.game not in catalog:
        raise SystemExit(f"未知游戏 id：{args.game}")
    cfg.set_path_override(args.game, args.path)
    cfg_mod.save(cfg)
    print(f"已设置 {catalog[args.game].name} 存档目录：{args.path}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load()
    print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n配置文件：{cfg_mod.config_file()}")
    return 0


def cmd_scan_install(args: argparse.Namespace) -> int:
    """从一个游戏本体目录反推存档位置（绿色版/破解版/新游戏都用得上）。"""
    from . import installscan

    install = Path(args.path)
    if not install.is_dir():
        print(f"[中止] 目录不存在：{install}")
        return 2

    scan = installscan.scan_install_dir(install, args.name or "")
    print(scan.summary())

    if not scan.ok:
        print("\n未自动确定。可加 --pick 交互选择候选，或用：")
        print(f'  savekeeper add "{scan.game_name or install.name}" "D:/存档目录"')
        return 1

    if args.save:
        cfg = cfg_mod.load()
        cfg.add_custom_game(scan.game_name or install.name, str(scan.save_dir))
        cfg_mod.save(cfg)
        print(f"\n已写入配置：{scan.game_name or install.name}")
    if args.open and scan.save_dir:
        paths.open_in_explorer(scan.save_dir)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """扫描已安装游戏并自动探测存档目录 —— 解决内置库装不下的新游戏。"""
    from . import discover

    if args.refresh:
        discover.clear_cache()

    print("正在扫描已安装游戏……")
    found = discover.installed_games()
    if not found:
        print("未扫描到任何已安装游戏（可能未装 Steam/Epic，或清单不可读）。")
        print("可直接用：savekeeper add \"游戏名\" \"D:/存档目录\"")
        return 0

    print(f"共扫描到 {len(found)} 款已安装游戏，开始探测存档目录……\n")
    rows: list[tuple[str, str, str, str]] = []
    hit = 0
    for game in found:
        result = discover.discover_save_dir(game.name, game.appid, game.install_dir)
        if result.ok:
            hit += 1
            rows.append((game.name, game.platform, str(result.path), f"{result.confidence} · {result.reason}"))
        elif args.all:
            rows.append((game.name, game.platform, "未找到", result.reason))

    if not rows:
        print("没有自动探测到存档目录。可加 --all 查看每款游戏的失败原因。")
        return 0

    print(f"{'游戏':<34}{'平台':<8}{'存档目录'}")
    print("-" * 110)
    for name, platform, path, reason in rows:
        print(f"{name:<34}{platform:<8}{path}")
        if args.verbose:
            print(f"{'':<42}└─ {reason}")
    print("-" * 110)
    print(f"自动命中 {hit} / {len(found)} 款。命中的可直接 export / snapshots 使用。")
    print("未命中的用：savekeeper add \"游戏名\" \"D:/存档目录\" 手动补充。")

    if args.save:
        cfg = cfg_mod.load()
        saved = 0
        for name, _platform, path, _reason in rows:
            if path == "未找到":
                continue
            cfg.add_custom_game(name, path)
            saved += 1
        if saved:
            cfg_mod.save(cfg)
            print(f"已把 {saved} 款游戏写入配置，之后可直接用游戏名操作。")
    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    """列出最近改动过的存档目录 —— 刚下载的游戏玩一次后用它定位。"""
    from . import discover

    discover.clear_cache()
    rows = discover.recent_activity(days=args.days, limit=args.limit)
    if not rows:
        print(f"最近 {args.days} 天内没有发现改动过的存档目录。")
        print("提示：先进游戏玩一会、存一次档，再回来跑这条命令。")
        return 0
    print(f"最近 {args.days} 天内有存档改动的目录（按时间倒序）：\n")
    for path, days, hint, kind in rows:
        label = f"{hint} " if hint else ""
        print(f"  {days:>3} 天前  {label}{path}")
        print(f"{'':<14}└─ {kind}")
    print("\n确认哪个是你要的，用：savekeeper add \"游戏名\" \"上面对应路径\"")
    print("Steam 来源的条目其实已被自动识别，直接在 GUI 里选游戏即可。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="savekeeper", description="游戏存档一键管理工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出全部游戏与检测结果").set_defaults(func=cmd_list)

    sp = sub.add_parser("path", help="打印存档目录")
    sp.add_argument("game")
    sp.set_defaults(func=cmd_path)

    sp = sub.add_parser("open", help="资源管理器打开存档目录")
    sp.add_argument("game")
    sp.set_defaults(func=cmd_open)

    sp = sub.add_parser("export", help="导出（备份）当前存档")
    sp.add_argument("game")
    sp.add_argument("--label", default="", help="快照备注，如「通关前」")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("snapshots", help="列出某游戏的备份快照")
    sp.add_argument("game")
    sp.set_defaults(func=cmd_snapshots)

    sp = sub.add_parser("import", help="导入（还原）存档")
    sp.add_argument("game")
    sp.add_argument("--snapshot", help="快照目录名（snapshots 命令输出的第一列）")
    sp.add_argument("--from", dest="from_dir", help="从外部文件夹导入")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("verify", help="校验快照完整性")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser(
        "swap",
        help="一步换档：校验 → 自动备份当前存档 → 覆盖（就是「导入」的短路径）",
    )
    sp.add_argument("game")
    sp.add_argument("snapshot", help="快照目录名（snapshots 输出的第一列）")
    sp.set_defaults(func=cmd_swap)

    sp = sub.add_parser("latest", help="直接换到最新一个快照（回滚用）")
    sp.add_argument("game")
    sp.set_defaults(func=cmd_latest)

    sp = sub.add_parser("export-snapshot", help="把快照导成一个独立文件夹（发给别人/传网盘）")
    sp.add_argument("game")
    sp.add_argument("snapshot")
    sp.add_argument("dest", help="导出到哪个文件夹")
    sp.set_defaults(func=cmd_export_snapshot)

    sp = sub.add_parser("add", help="添加自定义游戏")
    sp.add_argument("name")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("setpath", help="手动指定存档目录")
    sp.add_argument("game")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_setpath)

    sub.add_parser("config", help="查看当前配置").set_defaults(func=cmd_config)

    sp = sub.add_parser(
        "scan-install",
        help="从一个游戏本体文件夹反推存档位置（绿色版/破解版/新游戏靠这个）",
    )
    sp.add_argument("path", help="游戏本体目录（包含 .exe 的那一层）")
    sp.add_argument("--name", default="", help="自定义游戏名，默认用目录名")
    sp.add_argument("--save", action="store_true", help="把结果写入配置")
    sp.add_argument("--open", action="store_true", help="定位到存档目录")
    sp.set_defaults(func=cmd_scan_install)

    sp = sub.add_parser("scan", help="扫描已安装游戏并自动探测存档目录（新游戏靠这个）")
    sp.add_argument("--all", action="store_true", help="同时列出未命中的游戏")
    sp.add_argument("--verbose", action="store_true", help="显示每款的判定依据")
    sp.add_argument("--save", action="store_true", help="把命中的结果写入配置")
    sp.add_argument("--refresh", action="store_true", help="忽略缓存重新扫描")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("recent", help="列出最近改动过的存档目录")
    sp.add_argument("--days", type=int, default=14, help="回看天数，默认 14")
    sp.add_argument("--limit", type=int, default=20, help="最多返回条数，默认 20")
    sp.set_defaults(func=cmd_recent)
    return p


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (core.IntegrityError, FileNotFoundError) as exc:
        print(f"[中止] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
