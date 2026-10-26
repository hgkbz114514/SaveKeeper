"""tkinter 主界面。设计约束：主界面操作路径 ≤ 2 步（选择游戏 → 点击功能按钮）。"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import config as cfg_mod
from . import core, games as games_mod, installscan, paths, zipimport as zip_mod


class SaveKeeperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SaveKeeper · 游戏存档一键管理")
        self.geometry("900x600")
        self.minsize(820, 520)

        # 避免 GBK 控制台下 UnicodeEncodeError：按当前代码页重编码
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
        self.cfg = cfg_mod.load()
        self._resolved: dict[str, Path | None] = {}
        self._busy = False

        self._build_ui()
        self.refresh_games()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="游戏：").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_games())
        search = ttk.Entry(top, textvariable=self.search_var, width=22)
        search.pack(side="left", padx=(0, 8))
        ttk.Label(top, text="（输入名称过滤）").pack(side="left")

        ttk.Button(top, text="＋ 自定义游戏", command=self.add_custom_game).pack(side="right")
        ttk.Button(top, text="＋ 从游戏文件夹添加", command=self.add_from_install_dir).pack(
            side="right", padx=(0, 4)
        )
        ttk.Button(top, text="刷新", command=self.refresh_games).pack(side="right", padx=4)

        body = ttk.Frame(self, padding=(8, 0, 8, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ---------------- 左：游戏列表 ----------------
        left = ttk.LabelFrame(body, text="① 选择游戏（双击 = 打开存档目录）", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.game_list = tk.Listbox(left, exportselection=False)
        self.game_list.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.game_list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.game_list.configure(yscrollcommand=sb.set)
        self.game_list.bind("<<ListboxSelect>>", lambda _e: self.on_select())
        # 双击游戏 = 直接打开存档目录，不用再去右边找按钮
        self.game_list.bind("<Double-Button-1>", lambda _e: self.open_dir())

        # ---------------- 右：当前游戏 + 操作 ----------------
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        info = ttk.LabelFrame(right, text="当前游戏", padding=6)
        info.grid(row=0, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        self.lbl_name = ttk.Label(info, text="—", font=("", 11, "bold"))
        self.lbl_name.grid(row=0, column=0, columnspan=2, sticky="w")
        self.lbl_path = ttk.Label(info, text="存档目录：未检测到", foreground="#a00")
        self.lbl_path.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.lbl_stat = ttk.Label(info, text="")
        self.lbl_stat.grid(row=2, column=0, columnspan=2, sticky="w")

        btns = ttk.Frame(right, padding=(0, 6))
        btns.grid(row=1, column=0, sticky="ew")
        for i in range(3):
            btns.columnconfigure(i, weight=1)
        self.btn_open = ttk.Button(btns, text="📂 打开存档目录", command=self.open_dir)
        self.btn_export = ttk.Button(btns, text="⬇ 导出存档", command=self.export)
        self.btn_import = ttk.Button(btns, text="⬆ 导入存档", command=self.restore)
        self.btn_open.grid(row=0, column=0, sticky="ew", padx=2)
        self.btn_export.grid(row=0, column=1, sticky="ew", padx=2)
        self.btn_import.grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(btns, text="设置存档路径", command=self.set_path).grid(
            row=1, column=0, sticky="ew", padx=2, pady=(4, 0)
        )
        ttk.Button(btns, text="恢复自动探测", command=self.clear_path).grid(
            row=1, column=1, sticky="ew", padx=2, pady=(4, 0)
        )
        ttk.Button(btns, text="打开备份目录", command=self.open_backup_dir).grid(
            row=1, column=2, sticky="ew", padx=2, pady=(4, 0)
        )

        # ---------------- 备份列表 ----------------
        bframe = ttk.LabelFrame(right, text="② 备份快照（双击 = 直接换档，右键更多）", padding=6)
        bframe.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        bframe.rowconfigure(0, weight=1)
        bframe.columnconfigure(0, weight=1)

        cols = ("name", "time", "files", "size", "state")
        self.snap_tree = ttk.Treeview(bframe, columns=cols, show="headings", height=8)
        for col, text, w in (
            ("name", "快照", 200),
            ("time", "时间", 130),
            ("files", "文件数", 60),
            ("size", "大小", 80),
            ("state", "校验", 90),
        ):
            self.snap_tree.heading(col, text=text)
            self.snap_tree.column(col, width=w, anchor="center" if col != "name" else "w")
        self.snap_tree.grid(row=0, column=0, sticky="nsew")
        sb2 = ttk.Scrollbar(bframe, orient="vertical", command=self.snap_tree.yview)
        sb2.grid(row=0, column=1, sticky="ns")
        self.snap_tree.configure(yscrollcommand=sb2.set)

        # 双击快照 = 直接换档。这是最高频的动作，不该让人先选中再找按钮。
        self.snap_tree.bind("<Double-Button-1>", lambda _e: self.restore(confirm=False))
        self._build_snap_menu()

        bottom = ttk.Frame(bframe)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(bottom, text="重新校验", command=self.refresh_snapshots).pack(side="left")
        ttk.Button(bottom, text="删除选中快照", command=self.delete_snapshot).pack(side="left", padx=4)
        ttk.Button(bottom, text="从外部文件夹导入…", command=self.import_from_dir).pack(side="right")

        self.status = ttk.Label(self, text="就绪", anchor="w", relief="sunken")
        self.status.pack(fill="x", side="bottom")

    # -------------------------------------------------------------- helpers
    def set_status(self, text: str) -> None:
        self.status.configure(text=text)
        self.update_idletasks()

    def current_game(self) -> games_mod.Game | None:
        sel = self.game_list.curselection()
        if not sel:
            return None
        return self._visible[int(sel[0])]

    def current_path(self) -> Path | None:
        game = self.current_game()
        if game is None:
            return None
        return self._resolved.get(game.id)

    def refresh_games(self) -> None:
        keyword = self.search_var.get().strip().lower()
        self._visible = [g for g in self.cfg.all_games() if keyword in g.name.lower()]

        # 先解析路径再填列表：反过来的话 ✓ 标记会用上一轮的旧结果，永远慢一拍
        self._resolved = {}
        for g in self._visible:
            self._resolved[g.id] = games_mod.resolve_game_path(g)

        self.game_list.delete(0, "end")
        for g in self._visible:
            mark = "✓ " if self._resolved.get(g.id) else ""
            self.game_list.insert("end", f"{mark}{g.name}（{g.platform}）")

        if self._visible:
            # 优先回到上次选中的游戏，其次回到第一个已检测到存档目录的游戏
            index = 0
            ids = [g.id for g in self._visible]
            if self.cfg.last_game_id in ids:
                index = ids.index(self.cfg.last_game_id)
            else:
                for i, g in enumerate(self._visible):
                    if self._resolved.get(g.id):
                        index = i
                        break
            self.game_list.selection_set(index)
            self.game_list.see(index)
        self.on_select()

    def on_select(self) -> None:
        import os

        game = self.current_game()
        if game is None:
            self.lbl_name.configure(text="—")
            self.lbl_path.configure(text="存档目录：未检测到", foreground="#a00")
            self.lbl_stat.configure(text="")
            self.snap_tree.delete(*self.snap_tree.get_children())
            for b in (self.btn_open, self.btn_export, self.btn_import):
                b.state(["disabled"])
            return

        for b in (self.btn_open, self.btn_export, self.btn_import):
            b.state(["!disabled"])

        # 记住当前选择，下次启动直接定位到这里
        if game.id != self.cfg.last_game_id:
            self.cfg.last_game_id = game.id
            try:
                cfg_mod.save(self.cfg)
            except OSError:
                pass

        self.lbl_name.configure(text=f"{game.name}　{game.platform}")
        resolved = self._resolved.get(game.id)
        manual = games_mod.manual_path_state(game)
        if resolved:
            file_count = 0
            total_size = 0
            for dirpath, _dirs, filenames in os.walk(resolved):
                for name in filenames:
                    try:
                        total_size += (Path(dirpath) / name).stat().st_size
                        file_count += 1
                    except OSError:
                        continue
            suffix = "　（手动指定）" if manual == "ok" else ""
            self.lbl_path.configure(
                text=f"存档目录：{resolved}{suffix}", foreground="#060"
            )
            self.lbl_stat.configure(text=f"{file_count} 个文件 · {paths.human_size(total_size)}")
        elif manual == "stale":
            # 设过手动路径但目录没了 —— 必须说清楚，否则用户以为还在用自己的路径
            self.lbl_path.configure(
                text=f"存档目录：手动指定的路径已不存在（{game.custom_path}）", foreground="#a00"
            )
            self.lbl_stat.configure(text="可重新点「设置存档路径」指定，或点右键菜单恢复自动探测")
        else:
            self.lbl_path.configure(
                text="存档目录：未检测到（可点「设置存档路径」手动指定）", foreground="#a00"
            )
            self.lbl_stat.configure(text="")
        self.refresh_snapshots()

    def refresh_snapshots(self) -> None:
        self.snap_tree.delete(*self.snap_tree.get_children())
        game = self.current_game()
        if game is None:
            return
        for snap in core.list_snapshots(self.cfg.backup_dir(), game.id):
            self.snap_tree.insert(
                "",
                "end",
                iid=str(snap["path"]),
                values=(
                    snap.get("label") or snap["name"],
                    (snap.get("created_at") or "").replace("T", " ")[:19],
                    snap.get("file_count", 0),
                    paths.human_size(snap.get("total_bytes", 0)),
                    "通过" if snap["ok"] else "损坏",
                ),
                tags=("ok" if snap["ok"] else "bad",),
            )
        self.snap_tree.tag_configure("ok", foreground="#060")
        self.snap_tree.tag_configure("bad", foreground="#a00")

    def selected_snapshot(self) -> Path | None:
        sel = self.snap_tree.selection()
        return Path(sel[0]) if sel else None

    def _build_snap_menu(self) -> None:
        """快照右键菜单。双击已经能换档，这里补上「导出成文件夹」这类不常用但必要的动作。"""
        self.snap_menu = tk.Menu(self, tearoff=0)
        self.snap_menu.add_command(label="导入这个存档（换档）", command=self.restore)
        self.snap_menu.add_command(label="重命名这个快照…", command=self.rename_snapshot)
        self.snap_menu.add_command(label="导出成一个文件夹…", command=self.export_snapshot)
        self.snap_menu.add_separator()
        self.snap_menu.add_command(label="在资源管理器里查看", command=self.reveal_snapshot)
        self.snap_menu.add_command(label="删除这个快照", command=self.delete_snapshot)

        def popup(event: tk.Event) -> None:
            row = self.snap_tree.identify_row(event.y)
            if not row:
                return
            self.snap_tree.selection_set(row)
            self.snap_menu.tk_popup(event.x_root, event.y_root)

        self.snap_tree.bind("<Button-3>", popup)

    # -------------------------------------------------------------- actions
    def _error_box(self, title: str, message: str) -> None:
        messagebox.showerror(title, message, parent=self)

    def _ui(self, fn, *args) -> None:
        """把回调排进 UI 线程；窗口已经销毁时安静丢弃。

        后台线程跑完一定要回主线程改界面，但用户可能中途就把窗口关了 ——
        这时 ``self.after`` 会抛 ``RuntimeError: main thread is not in main loop``，
        线程里的 traceback 帮不上任何人，反而吓人。所有跨线程回调都走这里。
        """
        try:
            self.after(0, lambda: fn(*args))
        except (RuntimeError, tk.TclError):
            pass

    def _run(self, title: str, fn) -> None:
        if self._busy:
            return
        self._busy = True
        self.set_status(f"{title}…")

        def work() -> None:
            try:
                message = fn()
            except PermissionError as exc:
                # 安全软件拦截写盘时分流自救：
                # - 拦的是备份目录 → 弹「选备份位置」（换成可写的地方，自动重试）
                # - 拦的是别处（几乎总是游戏存档目录，比如 Steam userdata）→
                #   备份目录搬到哪都没用，唯一出路是提权重试 —— 引导「以管理员身份运行」。
                # exc 必须先固定进局部变量：except 块结束后 Python 会清掉
                # 异常绑定，延迟到 after 回调再读会 NameError。
                blocked_exc = exc
                blocked_path = getattr(exc, "filename", "") or ""
                backup_root = ""
                try:
                    backup_root = str(self.cfg.backup_dir())
                except Exception:  # noqa: BLE001
                    pass
                if blocked_path and backup_root and blocked_path.startswith(backup_root):
                    self._ui(self._backup_root_blocked, title, fn, blocked_exc)
                else:
                    self._ui(self._archive_dir_blocked, title, blocked_exc, blocked_path)
            except Exception as exc:  # noqa: BLE001
                self._ui(self._error_box, title, str(exc))
                self._ui(self.set_status, f"{title}失败")
            else:
                self._ui(self.set_status, message)
                self._ui(self.refresh_snapshots)
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()

    def _backup_root_blocked(self, title: str, fn, exc: PermissionError) -> None:
        """备份目录写入被拒：引导用户换位置，选中后自动重试原操作。"""
        sidecar = cfg_mod.exe_sidecar_dir()
        extra = ""
        if sidecar is not None:
            extra = (
                f"\n实测：本程序只稳定写得了「程序自己所在的目录」。\n"
                f"推荐直接选：{sidecar / 'SaveKeeper-Backups'}\n"
                f"（或者把整个程序文件夹挪到 D 盘等位置再用）\n"
            )
        messagebox.showwarning(
            "备份目录被拒绝访问",
            f"写入备份目录时被 Windows 拒绝：\n{exc}\n\n"
            "常见原因：杀毒软件（如火绒、Defender「受控文件夹访问」）"
            "拦截了新程序的写盘行为。\n"
            f"{extra}\n"
            "选一个其它位置放备份（比如 D:\\SaveKeeper-Backups），选完自动重试。",
            parent=self,
        )
        directory = filedialog.askdirectory(
            title="选择备份存放位置", parent=self, initialdir="C:/"
        )
        if not directory:
            self.set_status(f"{title}失败：备份目录被拒绝访问")
            return
        self.cfg.backup_root = directory
        object.__setattr__(self.cfg, "_backup_dir_cache", None)
        cfg_mod.save(self.cfg)
        self.set_status(f"备份位置已改为 {directory}，重试中…")
        self._run(title, fn)

    def _archive_dir_blocked(self, title: str, exc: PermissionError, path: str) -> None:
        """游戏存档目录写入被拒（拦的不是备份目录）——引导以管理员身份重启。

        换备份位置救不了这一种：存档目录是游戏的地盘（如 Steam userdata），
        没法搬。实测火绒对未信任 exe 写用户目录一律拦，提权后的进程通常会放行。
        """
        where = f"\n被拒绝的位置：{path}" if path else ""
        choice = messagebox.askyesno(
            f"{title}被拒绝",
            "写入游戏存档目录时被 Windows 拒绝：\n"
            f"{exc}{where}\n\n"
            "这是杀毒软件在拦截本程序写系统里的游戏存档（常见于火绒/受控文件夹访问）。\n"
            "备份位置怎么换都救不了这一步 —— 需要给本程序管理员权限再试。\n\n"
            "现在以管理员身份重新启动 SaveKeeper 吗？\n"
            "（弹出 UAC 窗口请点「是」，重启后再操作一次即可）",
            parent=self,
        )
        if not choice:
            self.set_status(f"{title}失败：存档目录被拒绝访问")
            return
        try:
            # ShellExecuteW runas = 右键「以管理员身份运行」的等价调用。
            # 源码模式跑的是 python.exe，必须带上主模块路径，否则只开个空解释器；
            # 打包 exe 模式 sys.executable 就是程序本身，不带参数。
            if getattr(sys, "frozen", False):
                exe, params = sys.executable, None
            else:
                main_py = Path(__file__).resolve().parent.parent / "__main__.py"
                exe, params = sys.executable, f'"{main_py}"'
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
            if ret > 32:
                self.set_status("已请求管理员权限，正在重启…")
                self.after(600, self.destroy)
                return
            raise OSError(f"ShellExecute 返回 {ret}")
        except Exception as restart_exc:  # noqa: BLE001
            messagebox.showerror(
                "重启失败",
                f"没能以管理员身份重启（{restart_exc}）。\n\n"
                "手动操作：右键 SaveKeeper.exe → 「以管理员身份运行」。",
                parent=self,
            )
            self.set_status(f"{title}失败：需要管理员权限")

    def open_dir(self) -> None:
        path = self.current_path()
        if path is None:
            self._no_path_help("打开存档目录")
            return
        paths.open_in_explorer(path)
        self.set_status(f"已打开 {path}")

    def _no_path_help(self, action: str) -> None:
        """没找到存档目录时不该只是「没反应」或一句话弹窗。

        messageboxes 不带 parent 时可能被主窗口压在后面，看起来就是点了没反应；
        这里统一带 parent=self，并直接给出下一步能做的事。
        """
        game = self.current_game()
        name = game.name if game else "该游戏"
        if game is None:
            return
        go_set = messagebox.askyesno(
            "SaveKeeper",
            f"还没有找到「{name}」的存档目录。\n\n"
            "现在选一下存档在哪个文件夹吗？\n"
            "（也可以点「📁 从游戏文件夹添加」自动反推）",
            parent=self,
        )
        if go_set:
            self.set_path()
        else:
            self.set_status(f"{action}已取消：未设置存档目录")

    def export(self) -> None:
        game = self.current_game()
        path = self.current_path()
        if game is None or path is None:
            self._no_path_help("导出存档")
            return

        # 先问名字：备份完再改就要多一步，而「这个存档是什么"时候"的」正是
        # 以后能不能找回来的关键。留空也能备份，取消则整个中止。
        label = self._ask_label()
        if label is None:
            self.set_status("已取消导出")
            return

        def job() -> str:
            snap = core.create_snapshot(
                path, self.cfg.backup_dir(), game.id, game.name, label
            )
            report = core.verify_snapshot(snap)
            if not report.ok:
                raise core.IntegrityError(f"已备份但校验失败：\n- " + "\n- ".join(report.errors[:5]))
            return f"已导出 {snap}（{report.file_count} 个文件，校验通过）"

        self._run("导出存档", job)

    def rename_snapshot(self) -> None:
        """给已有快照改名字。文件夹名跟着改，备份目录里才不会一堆时间戳。"""
        snap = self.selected_snapshot()
        if snap is None:
            messagebox.showwarning("SaveKeeper", "请先选中一个快照", parent=self)
            return

        current = ""
        for info in core.list_snapshots(self.cfg.backup_dir(), self.current_game().id):
            if Path(info["path"]) == snap:
                current = info.get("label") or ""
                break

        dialog = tk.Toplevel(self)
        dialog.title("重命名快照")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        ttk.Label(dialog, text="新名字：", font=("", 10, "bold")).pack(
            anchor="w", padx=12, pady=(12, 4)
        )
        var = tk.StringVar(value=current)
        entry = ttk.Entry(dialog, textvariable=var, width=42)
        entry.pack(fill="x", padx=12)
        entry.focus_set()
        entry.select_range(0, "end")
        ttk.Label(
            dialog,
            text="留空 = 去掉名字，只保留时间戳。文件夹会一起改名，不会动存档内容。",
            foreground="#888",
            wraplength=340,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(6, 0))

        def submit(_event: object = None) -> None:
            label = var.get().strip()
            dialog.destroy()
            try:
                new_path = core.rename_snapshot(snap, label)
            except (OSError, core.IntegrityError) as exc:
                messagebox.showerror("重命名失败", str(exc), parent=self)
                self.set_status("重命名失败")
                return
            self.refresh_snapshots()
            self.snap_tree.selection_set(str(new_path))
            self.set_status(f"已重命名为：{new_path.name}")

        row = ttk.Frame(dialog)
        row.pack(fill="x", padx=12, pady=(10, 12))
        ttk.Button(row, text="改名", command=submit).pack(side="right")
        ttk.Button(row, text="取消", command=dialog.destroy).pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", submit)
        dialog.bind("<Escape>", lambda _e: dialog.destroy())
        dialog.wait_window()

    def _ask_label(self) -> str | None:
        """问一句备注名。返回 None = 用户取消，返回 "" = 不命名直接备份。"""
        dialog = tk.Toplevel(self)
        dialog.title("导出存档")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        ttk.Label(
            dialog,
            text="给这份存档起个名字（可留空）",
            font=("", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(
            dialog,
            text="比如「打女武神前」「换流派-力量」，以后一眼就能认出是哪个节点。",
            foreground="#555",
            wraplength=340,
            justify="left",
        ).pack(anchor="w", padx=12)

        var = tk.StringVar()
        entry = ttk.Entry(dialog, textvariable=var, width=42)
        entry.pack(fill="x", padx=12, pady=8)
        entry.focus_set()
        ttk.Label(
            dialog,
            text="留空也能备份，之后在右键菜单里随时重命名。",
            foreground="#888",
        ).pack(anchor="w", padx=12)

        result: dict[str, str | None] = {"label": None}

        def ok(_event: object = None) -> None:
            result["label"] = var.get().strip()
            dialog.destroy()

        def skip() -> None:
            result["label"] = ""
            dialog.destroy()

        def cancel() -> None:
            result["label"] = None
            dialog.destroy()

        row = ttk.Frame(dialog)
        row.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(row, text="备份", command=ok).pack(side="right")
        ttk.Button(row, text="不命名", command=skip).pack(side="right", padx=(0, 6))
        ttk.Button(row, text="取消", command=cancel).pack(side="right", padx=(0, 6))
        dialog.bind("<Return>", ok)
        dialog.bind("<Escape>", lambda _e: cancel())
        dialog.wait_window()
        return result["label"]

    def restore(self, confirm: bool = True) -> None:
        game = self.current_game()
        path = self.current_path()
        snap = self.selected_snapshot()
        if game is None:
            return
        if snap is None:
            messagebox.showwarning("SaveKeeper", "请先在下方列表双击（或用右键菜单）选一个备份快照", parent=self)
            return
        if path is None:
            self._no_path_help("导入存档")
            return

        # 游戏还开着的时候换档没有意义：多数游戏退出时会把内存里的存档写回去，
        # 覆盖掉刚换好的文件。先拦住，这比事后解释为什么没生效有价值。
        running = self._running_process(game)
        if running:
            messagebox.showwarning(
                "SaveKeeper",
                f"检测到 {running} 正在运行。\n\n"
                "先完全退出游戏再换档 —— 多数游戏退出时会把内存里的存档写回去，"
                "刚换好的文件会被覆盖。",
                parent=self,
            )
            return

        # 双击是明确的意图，而且导入前会自动备份、可回退，所以不打断；
        # 走按钮时仍然问一次，避免手滑。
        if confirm and not messagebox.askyesno(
            "确认导入",
            f"将用快照「{snap.name}」覆盖当前存档。\n"
            f"导入前会自动备份当前存档，可随时回退。\n\n继续？",
            parent=self,
        ):
            return

        def job() -> str:
            report = core.restore_snapshot(
                snap, path or games_mod.resolve_game_path(game) or self._ask_target(game),
                self.cfg.backup_dir(), game.id, game.name,
            )
            return report.message + (f"（自动备份：{report.safety_backup.name}）" if report.safety_backup else "")

        self._run("导入存档", job)

    def _running_process(self, game: games_mod.Game) -> str:
        """游戏正在跑就返回进程名，否则返回空串。只按进程名匹配，不碰内存。"""
        wanted = {game.name.lower(), (game.id or "").lower()}
        try:
            out = subprocess.run(
                ["tasklist", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        for line in out.splitlines():
            cols = [c.strip('" ') for c in line.split('","')]
            if not cols or not cols[0]:
                continue
            exe = cols[0].lower()
            stem = exe[:-4] if exe.endswith(".exe") else exe
            # 用「名字包含」而不是相等：Exe 名常是 HogwartsLegacy-Win64-Shipping 这种
            for key in wanted:
                key = key.replace(" ", "")
                if len(key) >= 4 and key in stem.replace(" ", ""):
                    return cols[0]
        return ""

    def reveal_snapshot(self) -> None:
        snap = self.selected_snapshot()
        if snap is None:
            return
        paths.open_in_explorer(snap)

    def export_snapshot(self) -> None:
        """把快照导成一个独立文件夹，用于发给别人或存到网盘。"""
        snap = self.selected_snapshot()
        if snap is None:
            messagebox.showwarning("SaveKeeper", "请先选中一个快照", parent=self)
            return
        target = filedialog.askdirectory(title="把快照导出到哪个文件夹")
        if not target:
            return

        def job() -> str:
            report = core.export_snapshot_to(snap, Path(target))
            return report.message

        self._run("导出快照", job)

    def _ask_target(self, game: games_mod.Game) -> Path:
        directory = filedialog.askdirectory(title=f"选择 {game.name} 的存档目录")
        if not directory:
            raise core.IntegrityError("已取消：未指定存档目录")
        self.cfg.set_path_override(game.id, directory)
        cfg_mod.save(self.cfg)
        return Path(directory)

    def import_from_dir(self) -> None:
        game = self.current_game()
        if game is None:
            return
        chosen = filedialog.askopenfilename(
            title="选择要导入的存档（zip 或任意文件夹里的存档文件）",
            filetypes=[("压缩包", "*.zip"), ("全部文件", "*.*")],
        )
        if not chosen:
            return
        src = Path(chosen)
        stage: Path | None = None
        try:
            if src.suffix.lower() == ".zip":
                stage = zip_mod.stage_temp_dir()
                try:
                    zip_mod.extract_zip(src, stage)
                except zip_mod.ArchiveError as exc:
                    messagebox.showerror("导入存档", str(exc), parent=self)
                    return
                source, why = zip_mod.pick_save_subdir(stage)
                if source != stage:
                    messagebox.showinfo("已自动定位存档目录", why, parent=self)
            else:
                # 单文件拖入：把它放进一个临时目录再走统一流程
                stage = zip_mod.stage_temp_dir()
                shutil.copy2(src, stage / src.name)
                source = stage
            target = self.current_path() or self._ask_target(game)
            self._confirm_import(game, source, target)
        finally:
            if stage is not None:
                zip_mod.cleanup_dir(stage)

    def _confirm_import(self, game, source: Path, target: Path) -> None:
        """展示差异预览，确认后导入。"""
        preview = zip_mod.preview_diff(source, target)
        if preview.n_entries == 0:
            messagebox.showwarning(
                "SaveKeeper",
                "这个目录里没有要导入的文件。\n"
                "如果下载的是 zip，直接选 zip 即可，软件会自动解压定位。",
                parent=self,
            )
            return
        lines = [f"来源：{source}"]
        lines.append(preview.summary())
        if preview.replace_n:
            sample = [e.rel for e in preview.entries if e.kind == "replace"][:5]
            lines.append("\n将替换：\n  " + "\n  ".join(sample))
        if preview.new_n:
            sample = [e.rel for e in preview.entries if e.kind == "new"][:5]
            lines.append("\n将新增：\n  " + "\n  ".join(sample))
        lines.append("\n导入前会自动备份当前存档，可随时回退。")
        if not messagebox.askyesno("确认导入", "\n".join(lines), parent=self):
            return

        def job() -> str:
            report = core.import_directory(
                source, target, self.cfg.backup_dir(), game.id, game.name
            )
            return report.message

        self._run("外部导入", job)

    def set_path(self) -> None:
        game = self.current_game()
        if game is None:
            return
        directory = filedialog.askdirectory(title=f"选择 {game.name} 的存档目录")
        if not directory:
            return
        self.cfg.set_path_override(game.id, directory)
        cfg_mod.save(self.cfg)
        self.refresh_games()
        self.game_list.selection_clear(0, "end")
        ids = [g.id for g in self._visible]
        if game.id in ids:
            self.game_list.selection_set(ids.index(game.id))
        self.on_select()
        self.set_status(f"已设置 {game.name} 存档目录：{directory}（手动路径优先于自动探测）")

    def clear_path(self) -> None:
        """撤销手动路径，回到自动探测。

        没有这个入口的话，用户一旦设错就再也回不去了 —— 旧版甚至设对了也没用，
        因为手动路径排在自动探测后面。
        """
        game = self.current_game()
        if game is None:
            return
        if not self.cfg.clear_path_override(game.id):
            messagebox.showinfo("SaveKeeper", f"{game.name} 没有设置过手动路径。", parent=self)
            return
        cfg_mod.save(self.cfg)
        self.refresh_games()
        ids = [g.id for g in self._visible]
        if game.id in ids:
            self.game_list.selection_clear(0, "end")
            self.game_list.selection_set(ids.index(game.id))
        self.on_select()
        self.set_status(f"已恢复 {game.name} 的自动探测")

    def add_from_install_dir(self) -> None:
        """选一个游戏本体目录 → 反推存档位置 → 存成自定义游戏。

        这是给「绿色版 / 破解版 / 刚下的新游戏」准备的路子：这些游戏没有
        Steam 清单，内置库也不会收录，但它一定有本体目录。反推逻辑见
        :mod:`savekeeper.installscan`，四条线索交叉（appid → 本体存档目录 →
        存档根目录反查 → 引擎模板），不靠猜文件夹名。
        """
        directory = filedialog.askdirectory(title="选择游戏本体文件夹（包含 .exe 的那一层）")
        if not directory:
            return

        install = Path(directory)
        # 扫描要读目录树，放到后台线程，避免界面卡死
        self.set_status("正在分析游戏目录…")
        result = installscan.scan_install_dir(install)

        if result.ok and result.save_dir:
            self._confirm_install_scan(install, result)
            return

        # 没定下来：把候选给用户点，选中的那个当存档目录
        if result.candidates:
            self._choose_from_candidates(install, result)
        else:
            messagebox.showwarning(
                "SaveKeeper",
                f"没能从这个目录推断出存档位置。\n\n{result.reason}\n\n"
                "可以先用「＋ 自定义游戏」手动指定存档目录，\n"
                "或先进游戏存一次档再回来试。",
                parent=self,
            )
            self.set_status("未推断出存档位置")

    def _confirm_install_scan(self, install: Path, result: object) -> None:
        name = install.name
        save_dir = result.save_dir  # type: ignore[attr-defined]
        if not messagebox.askyesno(
            "SaveKeeper",
            f"识别到游戏：{name}\n"
            f"存档目录：{save_dir}\n\n"
            f"判定依据：{result.reason}\n"  # type: ignore[attr-defined]
            f"置信度：{result.confidence}\n\n"
            "添加到游戏列表吗？",
            parent=self,
        ):
            self.set_status("已取消")
            return
        self.cfg.add_custom_game(name, str(save_dir))
        cfg_mod.save(self.cfg)
        self.search_var.set("")
        self.refresh_games()
        self.set_status(f"已添加：{name} → {save_dir}")

    def _choose_from_candidates(self, install: Path, result: object) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("选择存档目录")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("680x340")

        ttk.Label(
            dialog,
            text=f"未能唯一确定「{install.name}」的存档位置，请选择：",
            font=("", 10, "bold"),
        ).pack(anchor="w", padx=10, pady=(10, 4))
        ttk.Label(dialog, text=f"（{result.reason}）", foreground="#555").pack(  # type: ignore[attr-defined]
            anchor="w", padx=10
        )

        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=10, pady=8)
        listbox = tk.Listbox(frame)
        listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        sb.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=sb.set)

        cands = result.candidates  # type: ignore[attr-defined]
        for cand, why in cands:
            listbox.insert("end", f"{cand}    ← {why}")
        listbox.selection_set(0)

        def submit() -> None:
            sel = listbox.curselection()
            if not sel:
                return
            chosen = cands[int(sel[0])][0]
            self.cfg.add_custom_game(install.name, str(chosen))
            cfg_mod.save(self.cfg)
            self.search_var.set("")
            self.refresh_games()
            dialog.destroy()
            self.set_status(f"已添加：{install.name} → {chosen}")

        ttk.Button(dialog, text="用这个目录", command=submit).pack(pady=10)
        dialog.wait_window()

    def open_backup_dir(self) -> None:
        game = self.current_game()
        root = self.cfg.backup_dir() / game.id if game else self.cfg.backup_dir()
        root.mkdir(parents=True, exist_ok=True)
        paths.open_in_explorer(root)

    def delete_snapshot(self) -> None:
        snap = self.selected_snapshot()
        if snap is None:
            return
        if not messagebox.askyesno(
            "删除快照", f"确定删除快照「{snap.name}」？此操作不可恢复。", parent=self
        ):
            return
        core.delete_snapshot(snap)
        self.refresh_snapshots()
        self.set_status(f"已删除 {snap.name}")

    def add_custom_game(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("添加自定义游戏")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("520x200")
        ttk.Label(dialog, text="游戏名称：").pack(anchor="w", padx=10, pady=(10, 0))
        name_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=name_var, width=50).pack(fill="x", padx=10)
        ttk.Label(dialog, text="存档目录：").pack(anchor="w", padx=10, pady=(8, 0))
        path_var = tk.StringVar()
        row = ttk.Frame(dialog)
        row.pack(fill="x", padx=10)
        ttk.Entry(row, textvariable=path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(
            row, text="浏览…", width=8,
            command=lambda: path_var.set(filedialog.askdirectory() or path_var.get()),
        ).pack(side="left", padx=(4, 0))

        def submit() -> None:
            name = name_var.get().strip()
            path = path_var.get().strip()
            if not name or not path:
                messagebox.showwarning("SaveKeeper", "名称和目录都要填写", parent=dialog)
                return
            self.cfg.add_custom_game(name, path)
            cfg_mod.save(self.cfg)
            self.search_var.set("")
            self.refresh_games()
            dialog.destroy()
            self.set_status(f"已添加自定义游戏：{name}")

        ttk.Button(dialog, text="添加", command=submit).pack(pady=12)
        dialog.wait_window()


def main() -> None:
    app = SaveKeeperApp()

    # 启动预检：立刻实测备份目录可写，被杀软拦截时第一时间弹自救框引导换位置，
    # 而不是等用户点了「导出」才炸（实测：HIPS 对未信任 exe 只放行 exe 自己的目录）。
    def _preflight() -> None:
        try:
            app.cfg.backup_dir()
        except PermissionError as exc:
            app.after(0, lambda: app._backup_root_blocked("启动预检", lambda: None, exc))
        except OSError:
            pass  # 其它文件系统错误留给具体操作时报错，不在启动时打断用户

    threading.Thread(target=_preflight, daemon=True).start()

    app.mainloop()


if __name__ == "__main__":
    main()
