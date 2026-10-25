"""GUI 截图脚本。

用 Win32 API 找到 SaveKeeper 窗口，只截该窗口区域（不是整个屏幕）。
输出到 assets/screenshot-*.png。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

from PIL import ImageGrab

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def find_window(title_part: str) -> int | None:
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_part in buf.value:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_enum, 0)
    return found[0] if found else None


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """返回窗口外框（含标题栏阴影）的屏幕坐标。"""
    rect = wt.RECT()
    # 9 = DWMWA_EXTENDED_FRAME_BOUNDS，能避开 Win10 的透明阴影边
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)
        )
    except Exception:  # noqa: BLE001
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def capture(title_part: str, out_path: Path, delay: float = 1.0, inset: int = 3) -> bool:
    hwnd = find_window(title_part)
    if hwnd is None:
        print(f"[x] 找不到标题含「{title_part}」的窗口")
        return False

    # 置前并激活
    user32.ShowWindow(hwnd, 9)   # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(delay)

    left, top, right, bottom = window_rect(hwnd)
    # DWM 的 EXTENDED_FRAME_BOUNDS 把不可见的调整边框也算进去了，
    # 直接按它截会把窗口背后的内容带进画面（角落出现别的窗口的碎片）。
    left, top, right, bottom = left + inset, top + inset, right - inset, bottom - inset
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        print(f"[x] 窗口尺寸异常: {w}x{h}")
        return False

    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    print(f"[+] {out_path.name}  {img.width}x{img.height}px")
    return True


def main() -> int:
    out_dir = Path(__file__).parent / "assets"
    targets = sys.argv[1:] or ["SaveKeeper"]
    ok = capture(targets[0], out_dir / "screenshot-main.png")
    return 0 if ok else 1


def crop_status_bar(src: Path, dst: Path, trim_bottom: int = 26) -> None:
    """去掉底部状态栏再存一份，README 里更干净。"""
    from PIL import Image

    img = Image.open(src)
    img.crop((0, 0, img.width, img.height - trim_bottom)).save(dst, format="PNG")
    print(f"[+] {dst.name}  {img.width}x{img.height - trim_bottom}px")


if __name__ == "__main__":
    raise SystemExit(main())
