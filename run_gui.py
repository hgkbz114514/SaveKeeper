"""PyInstaller 打包入口。

gui.py 用的是包内相对导入（from . import ...），直接被 PyInstaller 当脚本执行会失败。
这个入口用绝对导入，并处理打包后的资源路径。
"""

from __future__ import annotations

import sys


def main() -> None:
    from savekeeper.gui import SaveKeeperApp

    app = SaveKeeperApp()
    app.mainloop()


if __name__ == "__main__":
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
