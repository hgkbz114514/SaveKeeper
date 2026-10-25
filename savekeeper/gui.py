"""tkinter 主界面。设计约束：主界面操作路径 ≤ 2 步（选择游戏 → 点击功能按钮）。"""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import config as cfg_mod
from . import core, games as games_mod, installscan, paths


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
        left = ttk.LabelFrame(body, text="① 选择游戏", padding=6)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.game_list = tk.Listbox(left, exportselection=False)
        self.game_list.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.game_list.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.game_list.configure(yscrollcommand=sb.set)
        self.game_list.bind("<<ListboxSelect>>", lambda _e: self.on_select())

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
            row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=(4, 0)
        )
        ttk.Button(btns, text="打开备份目录", command=self.open_backup_dir).grid(
            row=1, column=2, sticky="ew", padx=2, pady=(4, 0)
        )

        # ---------------- 备份列表 ----------------
        bframe = ttk.LabelFrame(right, text="② 备份快照（导入 = 选中后点「导入存档」）", padding=6)
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
            self.lbl_path.configure(text=f"存档目录：{resolved}", foreground="#060")
            self.lbl_stat.configure(text=f"{file_count} 个文件 · {paths.human_size(total_size)}")
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

    # -------------------------------------------------------------- actions
    def _run(self, title: str, fn) -> None:
        if self._busy:
            return
        self._busy = True
        self.set_status(f"{title}…")

        def work() -> None:
            try:
                message = fn()
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: messagebox.showerror(title, str(exc)))
                self.after(0, lambda: self.set_status(f"{title}失败"))
            else:
                self.after(0, lambda: self.set_status(message))
                self.after(0, self.refresh_snapshots)
            finally:
                self._busy = False

        threading.Thread(target=work, daemon=True).start()

    def open_dir(self) -> None:
        path = self.current_path()
        if path is None:
            messagebox.showwarning("SaveKeeper", "未检测到存档目录，请先「设置存档路径」")
            return
        paths.open_in_explorer(path)
        self.set_status(f"已打开 {path}")

    def export(self) -> None:
        game = self.current_game()
        path = self.current_path()
        if game is None or path is None:
            messagebox.showwarning("SaveKeeper", "未检测到存档目录，请先「设置存档路径」")
            return

        def job() -> str:
            snap = core.create_snapshot(path, self.cfg.backup_dir(), game.id, game.name)
            report = core.verify_snapshot(snap)
            if not report.ok:
                raise core.IntegrityError(f"已备份但校验失败：\n- " + "\n- ".join(report.errors[:5]))
            return f"已导出 {snap}（{report.file_count} 个文件，校验通过）"

        self._run("导出存档", job)

    def restore(self) -> None:
        game = self.current_game()
        path = self.current_path()
        snap = self.selected_snapshot()
        if game is None:
            return
        if snap is None:
            messagebox.showwarning("SaveKeeper", "请先在下方列表选中一个备份快照")
            return
        if not messagebox.askyesno(
            "确认导入",
            f"将用快照「{snap.name}」覆盖当前存档。\n"
            f"导入前会自动备份当前存档，可随时回退。\n\n继续？",
        ):
            return

        def job() -> str:
            report = core.restore_snapshot(
                snap, path or games_mod.resolve_game_path(game) or self._ask_target(game),
                self.cfg.backup_dir(), game.id, game.name,
            )
            return report.message + (f"（自动备份：{report.safety_backup.name}）" if report.safety_backup else "")

        self._run("导入存档", job)

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
        directory = filedialog.askdirectory(title="选择要导入的存档文件夹")
        if not directory:
            return
        target = self.current_path() or self._ask_target(game)
        if not messagebox.askyesno(
            "确认导入",
            f"将用「{directory}」覆盖当前存档。\n导入前会自动备份当前存档。\n\n继续？",
        ):
            return

        def job() -> str:
            report = core.import_directory(
                Path(directory), target, self.cfg.backup_dir(), game.id, game.name
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
        self.set_status(f"已设置 {game.name} 存档目录：{directory}")

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
        if not messagebox.askyesno("删除快照", f"确定删除快照「{snap.name}」？此操作不可恢复。"):
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
                messagebox.showwarning("SaveKeeper", "名称和目录都要填写")
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
    SaveKeeperApp().mainloop()


if __name__ == "__main__":
    main()
