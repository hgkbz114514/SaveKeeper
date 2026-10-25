"""GUI 截图脚本。

用 Win32 API 找到 SaveKeeper 窗口，只截该窗口区域（不是整个屏幕）。
输出到 assets/screenshot-*.png。

两个必须绕开的坑：

1. **不能只按标题子串匹配**：浏览器里开着 GitHub 仓库页时，页面标题
   ``GitHub - someone/SaveKeeper: ...`` 也含 "SaveKeeper"，纯标题匹配会
   抢到浏览器窗口，截出来是网页而不是程序界面。所以按**进程名**匹配。
2. **不能靠 SetForegroundWindow + ImageGrab**：Windows 会拒绝把窗口
   提到前台（前台被别的程序占着），于是 ImageGrab 按坐标抓到的还是
   压在它上面的那个窗口。所以用 ``PrintWindow(PW_RENDERFULLCONTENT)``
   让窗口自己渲染一份位图，与被遮挡无关。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import time
from pathlib import Path

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()

TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002
# 最小化窗口的坐标是 -48000 之类的虚拟值
_ICONIC_THRESHOLD = -10_000


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def _exe_name_for_pid(pid: int) -> str:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return ""
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID == pid:
                return str(entry.szExeFile).lower()
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return ""
    finally:
        kernel32.CloseHandle(snapshot)


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return (rect.left, rect.top, rect.right, rect.bottom)


def list_candidates(title_part: str = "SaveKeeper") -> list[tuple[int, str, str]]:
    """列出所有标题含 title_part 的可见顶层窗口：(hwnd, exe 名, 标题)。"""
    out: list[tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title or title_part not in title:
            return True
        out.append((hwnd, _exe_name_for_pid(_window_pid(hwnd)), title))
        return True

    user32.EnumWindows(_enum, 0)
    return out


def find_window(title_part: str, exe_filter: str = "") -> int | None:
    """按标题关键字 + 进程名找窗口，取面积最大的那个（排除工具栏/浮窗）。"""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _enum(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title or title_part not in title:
            return True
        if exe_filter:
            if _exe_name_for_pid(_window_pid(hwnd)) != exe_filter.lower():
                return True
        left, top, _r, _b = _window_rect(hwnd)
        if left <= _ICONIC_THRESHOLD or top <= _ICONIC_THRESHOLD:
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(_enum, 0)
    if not found:
        return None

    def _area(hwnd: int) -> int:
        left, top, right, bottom = _window_rect(hwnd)
        return max(0, right - left) * max(0, bottom - top)

    return max(found, key=_area)


def _grab_via_printwindow(hwnd: int) -> Image.Image | None:
    """用 PrintWindow 让窗口自己渲染，不受遮挡影响。失败返回 None。"""
    left, top, right, bottom = _window_rect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        return None

    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    gdi32.SelectObject(mem_dc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        if not ok:
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h  # 负数 = top-down，省得再翻转
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(info), DIB_RGB_COLORS)
        if not got:
            return None
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def _grab_via_screen(hwnd: int, inset: int) -> Image.Image | None:
    """兜底：把窗口提到最前再按坐标抓屏。"""
    from PIL import ImageGrab

    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # 设为 TOPMOST 是唯一能绕过「前台被别的程序占着」的办法
    HWND_TOPMOST = -1
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    )
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    left, top, right, bottom = _window_rect(hwnd)
    left, top, right, bottom = left + inset, top + inset, right - inset, bottom - inset
    if right - left <= 0 or bottom - top <= 0:
        return None
    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    # 还原成普通窗口，别一直压着别人
    HWND_NOTOPMOST = -2
    user32.SetWindowPos(
        hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
    )
    return img


def _autocrop_black(img: Image.Image, threshold: int = 8) -> Image.Image:
    """裁掉四周的纯黑边缘。

    ``PrintWindow`` 按 ``GetWindowRect`` 申请位图，但某些窗口（尤其是
    tkinter 这类自绘客户区的）实际只渲染右上角一部分，其余留黑。
    直接存下来底部三分之一是黑的，看着像图挂了。
    """
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()

    def _row_has_content(y: int) -> bool:
        return any(px[x, y] > threshold for x in range(0, w, 3))

    def _col_has_content(x: int) -> bool:
        return any(px[x, y] > threshold for y in range(0, h, 3))

    top = 0
    while top < h and not _row_has_content(top):
        top += 1
    bottom = h - 1
    while bottom > top and not _row_has_content(bottom):
        bottom -= 1
    left = 0
    while left < w and not _col_has_content(left):
        left += 1
    right = w - 1
    while right > left and not _col_has_content(right):
        right -= 1

    if (left, top, right + 1, bottom + 1) == (0, 0, w, h):
        return img
    return img.crop((left, top, right + 1, bottom + 1))


def capture(
    title_part: str,
    out_path: Path,
    exe_filter: str = "",
    method: str = "printwindow",
    autocrop: bool = True,
) -> bool:
    hwnd = find_window(title_part, exe_filter=exe_filter)
    if hwnd is None:
        print(f"[x] 找不到标题含「{title_part}」的窗口（进程过滤：{exe_filter or '无'}）")
        for h, exe, t in list_candidates(title_part):
            print(f"    候选: hwnd={h} exe={exe or '?'} title={t!r}")
        return False

    img: Image.Image | None = None
    used = method
    if method == "printwindow":
        img = _grab_via_printwindow(hwnd)
        if img is None:
            print("[!] PrintWindow 失败，改用抓屏兜底")
            used = "screen"
    if img is None:
        img = _grab_via_screen(hwnd, inset=3)
        used = "screen"
    if img is None:
        print("[x] 截图失败")
        return False

    if autocrop and used == "printwindow":
        before = img.size
        img = _autocrop_black(img)
        if img.size != before:
            print(f"[i] 裁黑边 {before[0]}x{before[1]} -> {img.width}x{img.height}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    exe = _exe_name_for_pid(_window_pid(hwnd))
    print(f"[+] {out_path.name}  {img.width}x{img.height}px  "
          f"(hwnd={hwnd} exe={exe} via {used})")
    return True


def crop_status_bar(src: Path, dst: Path, trim_bottom: int = 26) -> None:
    """去掉底部状态栏再存一份，README 里更干净。"""
    img = Image.open(src)
    img.crop((0, 0, img.width, img.height - trim_bottom)).save(dst, format="PNG")
    print(f"[+] {dst.name}  {img.width}x{img.height - trim_bottom}px")


def main() -> int:
    out_dir = Path(__file__).parent / "assets"
    parser = argparse.ArgumentParser(description="截取 SaveKeeper 窗口")
    parser.add_argument("title", nargs="?", default="SaveKeeper", help="窗口标题关键字")
    parser.add_argument(
        "--exe",
        default="savekeeper.exe",
        help="只匹配该进程名（默认 savekeeper.exe；跑源码时用 python.exe）",
    )
    parser.add_argument("--out", default="screenshot-main.png")
    parser.add_argument("--list", action="store_true", help="只列出候选窗口，不截图")
    parser.add_argument(
        "--method",
        choices=("printwindow", "screen"),
        default="printwindow",
        help="printwindow 不受遮挡影响；screen 是提窗口到最前后抓屏",
    )
    args = parser.parse_args()

    if args.list:
        for h, exe, t in list_candidates(args.title):
            print(f"hwnd={h} exe={exe or '?'} title={t!r}")
        return 0

    ok = capture(
        args.title,
        out_dir / args.out,
        exe_filter=args.exe,
        method=args.method,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
