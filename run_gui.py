"""PyInstaller 打包入口。

gui.py 用的是包内相对导入（from . import ...），直接被 PyInstaller 当脚本执行会失败。
这个入口用绝对导入，并处理打包后的资源路径。

另外支持一个隐藏的自检模式 ``--selftest``：在**打包后的 exe 里**实测
「打开目录」这条路到底通不通，把结果写进日志文件。
为什么需要它：无控制台的 exe 里异常 traceback 全丢了，界面上表现就是
「点了没反应」，在源码里跑是复现不出来的。
"""

from __future__ import annotations

import os
import sys
import traceback


def _selftest() -> int:
    """在打包环境里实测打开目录，输出结果。"""
    import ctypes
    import ctypes.wintypes as wt
    import subprocess
    import tempfile
    import time
    from pathlib import Path

    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        print(msg)

    user32 = ctypes.windll.user32

    def explorer_windows() -> list[str]:
        out: list[str] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def _enum(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value in ("CabinetWClass", "ExploreWClass"):
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                out.append(buf.value)
            return True

        user32.EnumWindows(_enum, 0)
        return out

    log(f"frozen={getattr(sys, 'frozen', False)}  cwd={os.getcwd()}")
    log(f"python={sys.executable}")

    # 1) 不存在的路径会怎样（这是「没反应」的最大嫌疑）
    missing = Path(tempfile.gettempdir()) / "sk_definitely_missing_dir_xyz"
    log(f"\n[1] 不存在的路径: {missing}  exists={missing.exists()}")
    try:
        os.startfile(str(missing))
        log("    os.startfile 没抛异常（意外）")
    except Exception as exc:  # noqa: BLE001
        log(f"    os.startfile 抛异常 -> {type(exc).__name__}: {exc}")
        log("    >>> 这就是界面上的「点了没反应」：traceback 在无控制台 exe 里没人看得到")

    # 2) 真实存在的唯一目录
    unique = Path(tempfile.gettempdir()) / f"sk_selftest_{os.getpid()}"
    unique.mkdir(parents=True, exist_ok=True)
    log(f"\n[2] 真实目录: {unique}  exists={unique.exists()}")
    before = set(explorer_windows())
    try:
        os.startfile(str(unique))
        log("    os.startfile ok")
    except Exception as exc:  # noqa: BLE001
        log(f"    os.startfile 抛异常 -> {type(exc).__name__}: {exc}")
    time.sleep(2.0)
    new = [t for t in explorer_windows() if t not in before]
    log(f"    新增 Explorer 窗口: {len(new)} {new}")

    # 3) 兜底路径
    if not new:
        log("    试 explorer.exe 兜底 ...")
        try:
            subprocess.Popen(["explorer.exe", str(unique)])
            time.sleep(2.0)
            new2 = [t for t in explorer_windows() if t not in before]
            log(f"    兜底后新增窗口: {len(new2)} {new2}")
        except Exception as exc:  # noqa: BLE001
            log(f"    兜底也失败 -> {type(exc).__name__}: {exc}")

    # 4) 真实存档目录能不能打开
    try:
        from savekeeper import config as cfg_mod
        from savekeeper import games as games_mod

        cfg = cfg_mod.load()
        log(f"\n[3] 真实配置 backup_root={cfg.backup_root} overrides={cfg.path_overrides}")
        hits = 0
        for g in cfg.all_games():
            p = games_mod.resolve_game_path(g)
            if not p:
                continue
            hits += 1
            try:
                os.startfile(str(p))
                log(f"    ok  {g.name} -> {p}")
            except Exception as exc:  # noqa: BLE001
                log(f"    FAIL {g.name} -> {p} : {type(exc).__name__}: {exc}")
            if hits >= 3:
                break
    except Exception:  # noqa: BLE001
        log("\n[3] 配置读取失败:\n" + traceback.format_exc())

    # [3] 备份写入实测：exe 身份下，各候选目录谁真的能写？
    log("\n[3] 备份目录写入实测（exe 身份）")
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from savekeeper import config as _cfg
        for name, p in [
            ("Documents", Path.home() / "Documents" / "SaveKeeper-Backups"),
            ("LocalAppData", _cfg.local_appdata_dir() / "backups"),
            ("exe_dir", Path(_sys.executable).resolve().parent / "SaveKeeper-Backups"),
            ("home", Path.home() / "SaveKeeper-Backups"),
        ]:
            log(f"    {name}: {p}")
            try:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / ".sk_probe"
                probe.write_bytes(b"ok")
                probe.unlink()
                log("        write: OK")
            except Exception as _e:
                log(f"        write: FAIL {_e}")
    except Exception as e:
        log(f"    备份实测段失败: {e}")

    out = Path(os.environ.get("TEMP", tempfile.gettempdir())) / "savekeeper_selftest.log"
    out.write_text("\n".join(log_lines), encoding="utf-8")
    print(f"\n日志已写入: {out}")
    return 0


def main() -> None:
    from savekeeper.gui import SaveKeeperApp

    app = SaveKeeperApp()
    app.mainloop()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        try:
            raise SystemExit(_selftest())
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            import tempfile
            from pathlib import Path

            out = Path(tempfile.gettempdir()) / "savekeeper_selftest.log"
            out.write_text(traceback.format_exc(), encoding="utf-8")
            raise
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        # --windowed 模式下没有控制台，异常必须用弹窗暴露，否则用户只看到闪退
        try:
            import tkinter.messagebox as mb

            mb.showerror("SaveKeeper 启动失败", f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
        raise
