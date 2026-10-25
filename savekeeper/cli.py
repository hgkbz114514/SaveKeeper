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

    sp = sub.add_parser("add", help="添加自定义游戏")
    sp.add_argument("name")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("setpath", help="手动指定存档目录")
    sp.add_argument("game")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_setpath)

    sub.add_parser("config", help="查看当前配置").set_defaults(func=cmd_config)
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
