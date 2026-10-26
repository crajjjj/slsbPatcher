"""The slsbPatcher window, laid out like Pandora: on the left everything that can be patched
or converted, ticked or unticked; on the right the report; one big Run button at the bottom.

Opening the window reads the load order and previews at once; every tick refreshes the
preview; Run writes the output. Settings live beside the exe (slsbPatcher.settings.json).
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
import webbrowser
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, actionlog, engine, gamedata, install, runlog, scenebuilder

CHECKED, UNCHECKED, MIXED = "☑", "☐", "▣"
REPO = "https://github.com/crajjjj/slsbPatcher"
THEMES = {
    "dark": dict(bg="#1c1b24", panel="#262531", field="#2e2d3b", text="#ebe9f3", muted="#9d9aae", off="#66637a",
                 accent="#8b7cf6", accent_dark="#7566e0", accent_text="#ffffff", select="#3b3656", border="#3a384c",
                 good="#7ad39a", warn="#f0b35c"),
    "light": dict(bg="#f3f2f8", panel="#ffffff", field="#ffffff", text="#1d1c26", muted="#6c6a7c", off="#a9a6b8",
                  accent="#6f5ce6", accent_dark="#5d4bd0", accent_text="#ffffff", select="#e4e0fb", border="#d8d5e6",
                  good="#2f9a5d", warn="#b7791f"),
}
UI = ("Segoe UI", 10)
UI_BOLD = ("Segoe UI Semibold", 10)
BIG = ("Segoe UI Semibold", 14)
MONO = ("Consolas", 10)


def _load_settings() -> dict:
    try:
        return json.loads(install.settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(settings: dict) -> None:
    try:
        path = install.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass


def _count(count: int, one: str, many: str = "") -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def _describe_input(path: Path) -> str:
    try:
        if not path.is_dir():
            return "folder not found"
        instance = gamedata.Mo2Instance.read(path)
        layers = gamedata.mo2_layers(path)
        if layers is not None:
            mods = sum(not layer.name.startswith("(") for layer in layers)
            if instance:
                return f"MO2, profile {instance.selected_profile()}, {mods} enabled mods"
            return f"MO2 profile, {mods} enabled mods"
        if gamedata.looks_like_data(path):
            return "one mod / Data folder"
        return f"folder of {len(gamedata.layers_for_input(path))} pack folders"
    except Exception as error:  # a bad folder must not break the window
        return f"unreadable: {error}"


def _open_folder(path: Path) -> None:
    if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif path.is_dir() and hasattr(os, "startfile"):
        os.startfile(path)


@dataclass
class PackRow:
    key: str                 # json file name (lower), or "reg:" + registry rel path (lower)
    name: str
    mod: str
    count: str
    json_index: int | None = None
    reg_index: int | None = None


@dataclass
class LogRow:
    key: str                 # file name, lower
    name: str
    source: str
    lines: str
    installed: bool
    path: Path


class _QueueHandler(logging.Handler):
    def __init__(self, messages: queue.Queue):
        super().__init__(logging.INFO)
        self.messages = messages

    def emit(self, record):
        self.messages.put(("log", self.format(record)))


class App:
    def __init__(self, root: tk.Tk, inputs: list[Path]):
        self.root = root
        self.settings = _load_settings()
        s = self.settings
        self.theme = s.get("theme", "dark")
        self.colors = THEMES[self.theme]
        self.messages: queue.Queue = queue.Queue()
        self.busy = False
        self.pending: str | None = None
        self.scanned: engine.Scan | None = None
        self.scan_key: tuple | None = None
        self.outcome: engine.Outcome | None = None
        self.rows: list[PackRow] = []
        self.log_rows: list[LogRow] = []
        self._preview_job: str | None = None

        self.inputs = inputs or [Path(p) for p in s.get("inputs", [])] or install.default_inputs()
        self.extra_logs = [Path(p) for p in s.get("logs", [])]
        self.skip_logs = set(s.get("skip_logs", []))
        self.skip_packs = set(s.get("skip_packs", []))
        self.skip_registries = set(s.get("skip_registries", []))
        self.reconvert = set(s.get("reconvert", []))
        self.update_dirs = [Path(p) for p in s.get("update", [])]
        self.out = s.get("out", str(install.default_out()))
        self.zip = bool(s.get("zip", False))
        self.patch = bool(s.get("patch", True))
        self.convert = bool(s.get("convert", True))
        self.official = bool(s.get("official_tags", False))
        self.stricter_futa = bool(s.get("stricter_futa", False))
        self.author = s.get("author", "")
        self.scene_builder = s.get("scene_builder", "")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._fill_lists())
        self.status_var = tk.StringVar()

        root.title(f"slsbPatcher {__version__}")
        root.minsize(1100, 720)
        geometry = s.get("geometry", "")
        placed = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geometry)
        # A remembered place on a screen that is gone would open the window out of sight.
        if placed and 0 <= int(placed.group(3)) < root.winfo_screenwidth() - 200 \
                and 0 <= int(placed.group(4)) < root.winfo_screenheight() - 200:
            root.geometry(geometry)
        else:
            root.geometry("1380x860")
        root.report_callback_exception = self._callback_failed
        self._build()
        self._apply_theme()

        handler = _QueueHandler(self.messages)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("slsbpatcher").addHandler(handler)
        if logging.getLogger("slsbpatcher").level in (logging.NOTSET, logging.WARNING):
            logging.getLogger("slsbpatcher").setLevel(logging.INFO)
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        if self.inputs:
            root.after(150, lambda: self._start("preview"))
        else:
            self._set_report("No load order yet.\n\nMenu > Options: add your MO2 profile folder (for example "
                             "MO2\\profiles\\Default), a Data folder, or a folder of extracted SLAL packs.")
            self.status_var.set("Menu > Options to add your load order.")

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        top = self.topbar = tk.Frame(root)
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))
        top.columnconfigure(2, weight=1)
        self.menu_button = tk.Button(top, text="Menu", command=self._show_menu)
        self.menu_button.grid(row=0, column=0, padx=(0, 4))
        self.about_button = tk.Button(top, text="About", command=self._about)
        self.about_button.grid(row=0, column=1)
        search = ttk.Frame(top)
        search.grid(row=0, column=2)
        ttk.Entry(search, textvariable=self.search_var, width=44, font=UI).pack(side="left", ipady=3)
        ttk.Label(search, text=" ⌕", font=("Segoe UI", 12)).pack(side="left")
        self.theme_button = tk.Button(top, text="☾" if self.theme == "dark" else "☀", command=self._toggle_theme)
        self.theme_button.grid(row=0, column=3)

        self.panes = ttk.Panedwindow(root, orient="horizontal")
        self.panes.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        left = ttk.Frame(self.panes)
        right = ttk.Frame(self.panes)
        self.panes.add(left, weight=3)
        self.panes.add(right, weight=2)

        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        self.caption = ttk.Label(left, style="Muted.TLabel")
        self.caption.grid(row=0, column=0, sticky="w", pady=(0, 4))
        vpanes = ttk.Panedwindow(left, orient="vertical")
        vpanes.grid(row=1, column=0, sticky="nsew")

        packs = ttk.Frame(vpanes)
        packs.columnconfigure(0, weight=1)
        packs.rowconfigure(1, weight=1)
        self.packs_label = ttk.Label(packs, text="SLAL packs and registries", style="Title.TLabel")
        self.packs_label.grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.packs_tree = self._tree(packs, (("tick", "", 34, False, "center"), ("num", "", 36, False, "e"),
                                             ("name", "Name", 210, True, "w"), ("mod", "Mod", 210, True, "w"),
                                             ("count", "Animations", 86, False, "e"), ("status", "What happens", 330, True, "w")))
        self.packs_tree.bind("<Button-1>", lambda e: self._on_click(e, self.packs_tree, self._pack_on, self._set_pack))
        self.packs_tree.bind("<Button-3>", self._pack_menu)
        self.packs_tree.bind("<space>", lambda e: self._toggle_selected(self.packs_tree, self._pack_on, self._set_pack))
        vpanes.add(packs, weight=3)

        logs = ttk.Frame(vpanes)
        logs.columnconfigure(0, weight=1)
        logs.rowconfigure(1, weight=1)
        self.logs_label = ttk.Label(logs, text="SLATE action logs", style="Title.TLabel")
        self.logs_label.grid(row=0, column=0, sticky="w", pady=(6, 2))
        self.logs_tree = self._tree(logs, (("tick", "", 34, False, "center"), ("num", "", 36, False, "e"),
                                           ("name", "Name", 230, True, "w"), ("source", "From", 210, True, "w"),
                                           ("lines", "Lines", 130, False, "e"), ("status", "What happens", 300, True, "w")))
        self.logs_tree.bind("<Button-1>", lambda e: self._on_click(e, self.logs_tree, self._log_on, self._set_log))
        self.logs_tree.bind("<Button-3>", self._log_menu)
        self.logs_tree.bind("<space>", lambda e: self._toggle_selected(self.logs_tree, self._log_on, self._set_log))
        vpanes.add(logs, weight=1)

        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(right)
        self.tabs.grid(row=0, column=0, sticky="nsew")
        report = ttk.Frame(self.tabs, style="Panel.TFrame")
        report.columnconfigure(0, weight=1)
        report.rowconfigure(0, weight=1)
        self.report_text = tk.Text(report, wrap="none", font=MONO, relief="flat", padx=10, pady=8, bd=0,
                                   highlightthickness=0)
        self.report_text.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(report, orient="vertical", command=self.report_text.yview)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar = ttk.Scrollbar(report, orient="horizontal", command=self.report_text.xview)
        hbar.grid(row=1, column=0, sticky="ew")
        self.report_text.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.tabs.add(report, text="Report")
        changes = ttk.Frame(self.tabs, style="Panel.TFrame")
        changes.columnconfigure(0, weight=1)
        changes.rowconfigure(0, weight=1)
        self.changes_tree = ttk.Treeview(changes, columns=("added", "removed", "detail"))
        for column, text, width, anchor in (("#0", "What", 360, "w"), ("added", "Added", 64, "e"),
                                            ("removed", "Removed", 70, "e"), ("detail", "Details", 220, "w")):
            self.changes_tree.heading(column, text=text, anchor=anchor)
            self.changes_tree.column(column, width=width, anchor=anchor, stretch=column in ("#0", "detail"))
        self.changes_tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(changes, orient="vertical", command=self.changes_tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.changes_tree.configure(yscrollcommand=bar.set)
        self.tabs.add(changes, text="Changes")

        self.status = ttk.Label(root, textvariable=self.status_var, style="Muted.TLabel")
        self.status.grid(row=2, column=0, sticky="w", padx=14, pady=(2, 4))
        self.run_button = tk.Button(root, text="Run", command=lambda: self._start("run"), font=BIG, relief="flat",
                                    bd=0, cursor="hand2", pady=14)
        self.run_button.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _tree(self, parent, columns) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for name, text, width, stretch, anchor in columns:
            tree.heading(name, text=text, anchor="center" if name == "tick" else anchor)
            tree.column(name, width=width, minwidth=width if not stretch else 60, stretch=stretch, anchor=anchor)
        tree.grid(row=1, column=0, sticky="nsew")
        bar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        bar.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=bar.set)
        return tree

    def _apply_theme(self) -> None:
        c = self.colors = THEMES[self.theme]
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=c["bg"], foreground=c["text"], font=UI, bordercolor=c["border"],
                        lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["bg"], focuscolor=c["accent"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Panel.TFrame", background=c["panel"])
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Muted.TLabel", foreground=c["muted"])
        style.configure("Title.TLabel", font=UI_BOLD)
        style.configure("TPanedwindow", background=c["bg"])
        style.configure("Sash", sashthickness=6, gripcount=0)
        style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"], foreground=c["text"],
                        rowheight=26, borderwidth=0, relief="flat")
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["text"])])
        style.configure("Treeview.Heading", background=c["bg"], foreground=c["muted"], relief="flat", font=UI_BOLD,
                        padding=(6, 6), borderwidth=0)
        style.map("Treeview.Heading", background=[("active", c["bg"]), ("pressed", c["bg"])])
        style.configure("TScrollbar", background=c["panel"], troughcolor=c["bg"], bordercolor=c["bg"],
                        arrowcolor=c["muted"], relief="flat", gripcount=0)
        style.map("TScrollbar", background=[("active", c["select"]), ("pressed", c["select"])])
        style.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"], padding=(16, 7), font=UI_BOLD,
                        borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["text"])],
                  expand=[("selected", (0, 0, 0, 0))])
        style.configure("TEntry", fieldbackground=c["field"], foreground=c["text"], insertcolor=c["text"],
                        bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=(8, 4))
        style.configure("TButton", background=c["panel"], foreground=c["text"], bordercolor=c["border"],
                        lightcolor=c["panel"], darkcolor=c["panel"], padding=(12, 6), relief="flat")
        style.map("TButton", background=[("active", c["select"]), ("pressed", c["select"])])
        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"], indicatorbackground=c["field"],
                        indicatorforeground=c["accent"], upperbordercolor=c["border"], lowerbordercolor=c["border"])
        style.map("TCheckbutton", background=[("active", c["bg"])],
                  indicatorbackground=[("selected", c["field"]), ("pressed", c["field"])])
        style.configure("TLabelframe", background=c["bg"], bordercolor=c["border"], relief="solid")
        style.configure("TLabelframe.Label", background=c["bg"], foreground=c["muted"], font=UI_BOLD)

        self.root.configure(bg=c["bg"])
        self.topbar.configure(bg=c["bg"])
        for button in (self.menu_button, self.about_button, self.theme_button):
            button.configure(bg=c["bg"], fg=c["text"], activebackground=c["select"], activeforeground=c["text"],
                             relief="flat", bd=0, font=UI_BOLD, padx=12, pady=6, cursor="hand2",
                             highlightthickness=0)
        self.theme_button.configure(text="☾" if self.theme == "dark" else "☀", font=("Segoe UI", 12))
        self.report_text.configure(bg=c["panel"], fg=c["text"], insertbackground=c["text"],
                                   selectbackground=c["select"], selectforeground=c["text"])
        self.run_button.configure(bg=c["accent"], fg=c["accent_text"], activebackground=c["accent_dark"],
                                  activeforeground=c["accent_text"], disabledforeground=c["accent_text"],
                                  highlightthickness=0)
        for tree in (self.packs_tree, self.logs_tree):
            tree.tag_configure("off", foreground=c["off"])
            tree.tag_configure("on", foreground=c["text"])
            tree.tag_configure("convert", foreground=c["good"])
            tree.tag_configure("reconvert", foreground=c["accent"])
            tree.tag_configure("warn", foreground=c["warn"])

    def _toggle_theme(self) -> None:
        self.theme = "light" if self.theme == "dark" else "dark"
        self._apply_theme()
        self._remember()

    def _callback_failed(self, kind, value, tb) -> None:
        """A Tk callback raised. The windowed exe has no console, so log it and say so on screen."""
        logging.getLogger("slsbpatcher").error("".join(traceback.format_exception(kind, value, tb)))
        self.status_var.set(f"Error: {value} (details in slsbPatcher.log)")

    # ------------------------------------------------------------------ menus and dialogs

    def _menu(self) -> tk.Menu:
        c = self.colors
        return tk.Menu(self.root, tearoff=0, bg=c["panel"], fg=c["text"], activebackground=c["select"],
                       activeforeground=c["text"], relief="flat", bd=0, font=UI)

    def _show_menu(self) -> None:
        menu = self._menu()
        menu.add_command(label="Options...", command=self._options)
        menu.add_command(label="Refresh preview", command=lambda: self._start("preview", rescan=True))
        menu.add_separator()
        menu.add_command(label="Tick all packs", command=lambda: self._set_all(self.packs_tree, self._set_pack, True))
        menu.add_command(label="Untick all packs", command=lambda: self._set_all(self.packs_tree, self._set_pack, False))
        menu.add_command(label="Tick all logs", command=lambda: self._set_all(self.logs_tree, self._set_log, True))
        menu.add_command(label="Untick all logs", command=lambda: self._set_all(self.logs_tree, self._set_log, False))
        menu.add_separator()
        out = Path(self.out)
        menu.add_command(label="Open output folder", command=lambda: _open_folder(out), state="normal" if out.exists() else "disabled")
        menu.add_command(label="Open log file", command=lambda: _open_folder(runlog.path()),
                         state="normal" if runlog.path().exists() else "disabled")
        menu.add_separator()
        menu.add_command(label="Exit", command=self._close)
        x = self.menu_button.winfo_rootx()
        y = self.menu_button.winfo_rooty() + self.menu_button.winfo_height()
        menu.tk_popup(x, y)

    def _about(self) -> None:
        c = self.colors
        win = tk.Toplevel(self.root, bg=c["bg"])
        win.title("About slsbPatcher")
        win.transient(self.root)
        win.resizable(False, False)
        text = (f"slsbPatcher {__version__}\n\n"
                "Patches SexLab P+ scene registries with SLATE action logs and converts SLAL packs that\n"
                "have no conversion yet. GPL-3.0.\n\n"
                "Built on slsb-convert.py (SLP-Community, GPL-3.0), the patch rules of SLATE NG (GPL-3.0)\n"
                "and SexLab Scene Builder (Scrab, I unpause, Miss Corruption; Apache-2.0).\n\n"
                f"{install.describe() or 'Not installed in an MO2 instance.'}\n\n"
                f"Settings: {install.settings_path()}\nLog: {runlog.path()}")
        ttk.Label(win, text=text, justify="left", wraplength=640).pack(padx=20, pady=(16, 10))
        buttons = ttk.Frame(win)
        buttons.pack(pady=(0, 14))
        ttk.Button(buttons, text="Open GitHub", command=lambda: webbrowser.open(REPO)).pack(side="left", padx=4)
        ttk.Button(buttons, text="Close", command=win.destroy).pack(side="left", padx=4)
        win.wait_visibility()  # a grab on a window that is not mapped yet fails
        win.grab_set()

    def _options(self) -> None:
        dialog = OptionsDialog(self)
        if dialog.result == "scan":
            self.scanned, self.scan_key, self.outcome = None, None, None
            self._refresh_caption()
            if self.inputs:
                self._start("preview")
            else:
                self.rows, self.log_rows = [], []
                self._fill_lists()
        elif dialog.result == "preview":
            self._refresh_caption()
            self._stale()

    def _pack_menu(self, event) -> None:
        tree = self.packs_tree
        iid = tree.identify_row(event.y)
        if not iid or not self.scanned:
            return
        tree.selection_set(iid)
        row = next(r for r in self.rows if r.key == iid)
        menu = self._menu()
        if row.json_index is not None:
            item = self.scanned.slal[row.json_index]
            var = tk.BooleanVar(value=item.winner.path.name.lower() in self.reconvert)
            menu.add_checkbutton(label="Re-convert (build its registry again, replacing the old one)", variable=var,
                                 state="normal" if item.covered else "disabled",
                                 command=lambda: self._set_reconvert(item.winner.path.name.lower(), var.get()))
            path = item.winner.path
        else:
            path = self.scanned.registries[row.reg_index].winner.path
        menu.add_command(label="Open containing folder", command=lambda: _open_folder(path))
        menu.tk_popup(event.x_root, event.y_root)

    def _log_menu(self, event) -> None:
        tree = self.logs_tree
        iid = tree.identify_row(event.y)
        if not iid:
            return
        tree.selection_set(iid)
        row = self.log_rows[int(iid.split(":")[0])]
        menu = self._menu()
        menu.add_command(label="Open containing folder", command=lambda: _open_folder(row.path))
        if not row.installed:
            menu.add_command(label="Remove this added log", command=lambda: self._remove_log(row))
        menu.tk_popup(event.x_root, event.y_root)

    def _remove_log(self, row: LogRow) -> None:
        self.extra_logs = [p for p in self.extra_logs if p.resolve() != row.path.resolve()]
        self.scanned, self.scan_key = None, None
        self._start("preview")

    # ------------------------------------------------------------------ ticks

    def _pack_on(self, key: str) -> bool:
        return key[4:] not in self.skip_registries if key.startswith("reg:") else key not in self.skip_packs

    def _set_pack(self, key: str, on: bool) -> None:
        target, value = (self.skip_registries, key[4:]) if key.startswith("reg:") else (self.skip_packs, key)
        target.discard(value) if on else target.add(value)

    def _log_on(self, iid: str) -> bool:
        return self.log_rows[int(iid.split(":")[0])].key not in self.skip_logs

    def _set_log(self, iid: str, on: bool) -> None:
        key = self.log_rows[int(iid.split(":")[0])].key
        self.skip_logs.discard(key) if on else self.skip_logs.add(key)

    def _set_reconvert(self, name: str, on: bool) -> None:
        self.reconvert.discard(name) if not on else self.reconvert.add(name)
        self._fill_lists()
        self._stale()

    def _on_click(self, event, tree, is_on, set_on):
        if tree.identify_column(event.x) != "#1":
            return None
        region = tree.identify_region(event.x, event.y)
        if region == "heading":
            keys = tree.get_children()
            all_on = all(is_on(k) for k in keys)
            for key in keys:
                set_on(key, not all_on)
        elif region == "cell":
            iid = tree.identify_row(event.y)
            if not iid:
                return None
            set_on(iid, not is_on(iid))
        else:
            return None
        self._fill_lists()
        self._stale()
        return "break"

    def _toggle_selected(self, tree, is_on, set_on):
        for iid in tree.selection():
            set_on(iid, not is_on(iid))
        self._fill_lists()
        self._stale()
        return "break"

    def _set_all(self, tree, set_on, on: bool) -> None:
        for key in tree.get_children():
            set_on(key, on)
        self._fill_lists()
        self._stale()

    # ------------------------------------------------------------------ lists

    def _rows_from_scan(self) -> None:
        found = self.scanned
        self.rows = []
        if not found:
            self.log_rows = []
            return
        for index, item in enumerate(found.slal):
            self.rows.append(PackRow(item.winner.path.name.lower(), item.winner.path.name, item.winner.layer.name,
                                     str(len(item.animations)), json_index=index))
        sources = found.registry_sources()
        for index, registry in enumerate(found.registries):
            if index not in sources:  # a registry no installed SLAL json is the source of
                self.rows.append(PackRow("reg:" + registry.winner.rel.lower(), registry.winner.path.name,
                                         registry.winner.layer.name, f"{len(registry.parsed.scenes)} scenes", reg_index=index))
        self.rows.sort(key=lambda r: (r.mod.lower(), r.name.lower()))
        actionlog.build_logs(found.installed_logs + found.extra_logs, self.official)  # fills the line counts
        self.log_rows = []
        for f in found.installed_logs:
            self.log_rows.append(LogRow(f.path.name.lower(), f.path.name, f.layer, f"{len(f.lines)} ({f.adds}+ {f.removes}-)", True, f.path))
        for f in found.extra_logs:
            self.log_rows.append(LogRow(f.path.name.lower(), f.path.name, str(f.path.parent), f"{len(f.lines)} ({f.adds}+ {f.removes}-)", False, f.path))

    def _registry_status(self, rel: str) -> tuple[str, str]:
        if not self.patch:
            return "has a registry (patching is off)", "on"
        o = self.outcome
        if o is not None:
            patched = next((p for p in o.patched if p.rel == rel), None)
            if patched:
                return f"patched: +{patched.added} -{patched.removed} in {_count(len(patched.scenes), 'scene')}", "on"
            if rel in o.left_alone_registries:
                return "left alone", "off"
            return "up to date", "on"
        return "has a registry: patched with the logs", "on"

    def _pack_status(self, row: PackRow) -> tuple[str, str]:
        if not self._pack_on(row.key):
            return "left alone", "off"
        found, o = self.scanned, self.outcome
        if row.json_index is None:
            return self._registry_status(found.registries[row.reg_index].winner.rel)
        item = found.slal[row.json_index]
        name = item.winner.path.name.lower()
        produced = next((p for p in o.produced if p.json_name.lower() == name), None) if o else None
        if item.covered and name in self.reconvert and self.convert:
            if produced:
                return f"re-converted: {_count(len(produced.scenes), 'scene')}, replaces its registry", "reconvert"
            return "re-convert: replaces its registry", "reconvert"
        if not item.covered:
            if not item.animations:
                return "no animations", "off"
            if not self.convert:
                return "no registry (converting is off)", "warn"
            if produced:
                return f"converted: {_count(len(produced.scenes), 'scene')}", "convert"
            return "no registry: converted", "convert"
        text, tag = self._registry_status(found.registries[item.main_registry()].winner.rel)
        missing = len(item.animations) - item.covered
        if missing:
            text += f"; {missing} animations in no registry"
            tag = "warn" if tag == "on" else tag
        return text, tag

    def _log_status(self, row: LogRow) -> tuple[str, str]:
        if row.key in self.skip_logs:
            return "left out", "off"
        o = self.outcome
        if o is not None:
            changes = [c for p in o.patched for c in p.changes if c.log == row.name]
            if changes:
                added = sum(c.added for c in changes)
                return f"applied: +{added} -{len(changes) - added}", "on"
            return "no changes (already in the registries, or scenes not installed)", "on"
        return "applied when patching", "on"

    @staticmethod
    def _clear(tree) -> tuple:
        """Empty a tree, returning what it takes to put the view and the selection back."""
        state = tree.yview()[0], tree.selection()
        tree.delete(*tree.get_children())
        return state

    @staticmethod
    def _restore(tree, state) -> None:
        top, selected = state
        tree.yview_moveto(top)
        for iid in selected:
            if tree.exists(iid):
                tree.selection_set(iid)

    def _fill_lists(self) -> None:
        needle = self.search_var.get().strip().lower()
        tree = self.packs_tree
        state = self._clear(tree)
        shown = 0
        for row in self.rows:
            status, tag = self._pack_status(row)
            if needle and needle not in f"{row.name} {row.mod} {status}".lower():
                continue
            shown += 1
            on = self._pack_on(row.key)
            tree.insert("", "end", iid=row.key, tags=(tag if on else "off",),
                        values=(CHECKED if on else UNCHECKED, shown, row.name, row.mod, row.count, status))
        self._restore(tree, state)
        self._heading_tick(tree, self._pack_on)
        on_count = sum(self._pack_on(r.key) for r in self.rows)
        self.packs_label.configure(text=f"SLAL packs and registries  ({on_count} of {len(self.rows)} ticked)")

        tree = self.logs_tree
        state = self._clear(tree)
        shown = 0
        for index, row in enumerate(self.log_rows):
            status, tag = self._log_status(row)
            if needle and needle not in f"{row.name} {row.source} {status}".lower():
                continue
            shown += 1
            on = row.key not in self.skip_logs
            tree.insert("", "end", iid=f"{index}:{row.key}", tags=(tag if on else "off",),
                        values=(CHECKED if on else UNCHECKED, shown, row.name, row.source, row.lines, status))
        self._restore(tree, state)
        self._heading_tick(tree, self._log_on)
        on_count = sum(r.key not in self.skip_logs for r in self.log_rows)
        self.logs_label.configure(text=f"SLATE action logs  ({on_count} of {len(self.log_rows)} ticked)")

    def _heading_tick(self, tree, is_on) -> None:
        keys = tree.get_children()
        states = {is_on(k) for k in keys}
        tree.heading("tick", text=CHECKED if states == {True} else UNCHECKED if states == {False} or not states else MIXED)

    def _refresh_caption(self) -> None:
        if not self.inputs:
            self.caption.configure(text="No load order: Menu > Options.")
            return
        parts = []
        for path in self.inputs:
            instance = gamedata.Mo2Instance.read(path)
            parts.append(f"{path} (MO2, profile {instance.selected_profile()})" if instance else str(path))
        self.caption.configure(text=f"Load order: {'; '.join(parts)}   →   output: {self.out}")

    # ------------------------------------------------------------------ report

    def _set_report(self, text: str) -> None:
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", "end")
        self.report_text.insert("1.0", text)
        self.report_text.configure(state="disabled")

    def _append_report(self, text: str) -> None:
        self.report_text.configure(state="normal")
        self.report_text.insert("end", text + "\n")
        self.report_text.see("end")
        self.report_text.configure(state="disabled")

    def _fill_changes(self, outcome: engine.Outcome) -> None:
        tree = self.changes_tree
        tree.delete(*tree.get_children())
        if outcome.patched:
            changes = [c for p in outcome.patched for c in p.changes]
            added = sum(c.added for c in changes)
            top = tree.insert("", "end", open=True, text=f"Patched registries ({len(outcome.patched)} of {outcome.registries})",
                              values=(added, len(changes) - added, _count(sum(len(p.scenes) for p in outcome.patched), "scene")))
            for mod, items in engine.by_mod(outcome.patched):
                mod_node = tree.insert(top, "end", open=len(outcome.patched) <= 12, text=mod,
                                       values=(sum(p.added for p in items), sum(p.removed for p in items),
                                               _count(len(items), "registry", "registries")))
                for p in items:
                    detail = _count(len(p.scenes), "scene") + (", and its project" if p.project_rel else "")
                    reg = tree.insert(mod_node, "end", text=p.rel.rsplit("/", 1)[-1], values=(p.added, p.removed, detail))
                    by_scene: dict[str, list] = {}
                    for change in p.changes:
                        by_scene.setdefault(change.scene, []).append(change)
                    for scene, items_ in by_scene.items():
                        scene_node = tree.insert(reg, "end", text=scene, values=(
                            sum(c.added for c in items_), sum(not c.added for c in items_), ""))
                        for change in items_:
                            tree.insert(scene_node, "end", text=f"{'+' if change.added else '-'} {change.tag}",
                                        values=("", "", change.log))
            tags = tree.insert("", "end", text="Tags changed", values=("", "", "how many scenes got the change"))
            for tag, count in engine.tag_counts(outcome.patched):
                tree.insert(tags, "end", text=tag, values=(count if tag.startswith("+") else "",
                                                           count if tag.startswith("-") else "", ""))
            logs = tree.insert("", "end", text="By action log")
            for f in outcome.logs:
                items_ = [c for c in changes if c.log == f.path.name]
                tree.insert(logs, "end", text=f.path.name, values=(
                    sum(c.added for c in items_), sum(not c.added for c in items_),
                    "" if items_ else "no changes: already in the registries, or scenes not installed"))
        elif outcome.options.patch:
            reason = ("no action logs ticked" if not outcome.logs else
                      "no registries in the load order" if not outcome.registries else
                      "every ticked registry already matches the logs")
            tree.insert("", "end", text="Nothing to patch", values=("", "", reason))
        if outcome.produced:
            node = tree.insert("", "end", open=True, text=f"New conversions ({len(outcome.produced)})",
                               values=("", "", _count(sum(len(p.scenes) for p in outcome.produced), "scene")))
            for p in outcome.produced:
                tree.insert(node, "end", text=f"{p.json_name}  ->  {p.slr_rel.rsplit('/', 1)[-1]}",
                            values=("", "", f"{_count(len(p.scenes), 'scene')}, {p.module}"))
        if outcome.would_convert:
            node = tree.insert("", "end", open=True, text=f"Will convert ({len(outcome.would_convert)})")
            for name, mod in outcome.would_convert:
                tree.insert(node, "end", text=name, values=("", "", mod))
        if outcome.left_alone_packs or outcome.left_alone_registries:
            node = tree.insert("", "end", text=f"Left alone, unticked ({len(outcome.left_alone_packs) + len(outcome.left_alone_registries)})")
            for name in outcome.left_alone_packs + [r.rsplit("/", 1)[-1] for r in outcome.left_alone_registries]:
                tree.insert(node, "end", text=name)
        if outcome.partial:
            node = tree.insert("", "end", text=f"Partly converted packs ({len(outcome.partial)})",
                               values=("", "", "right-click a pack: Re-convert adds the rest"))
            for p in outcome.partial:
                item = tree.insert(node, "end", text=p.json_name,
                                   values=("", "", f"{len(p.missing)} of {p.total} animations in no registry"))
                for name in p.missing:
                    tree.insert(item, "end", text=name)
        if outcome.unmatched:
            node = tree.insert("", "end", text=f"Scenes in the logs that are not installed ({len(outcome.unmatched)})")
            for name in outcome.unmatched:
                tree.insert(node, "end", text=name)
        if outcome.problems:
            node = tree.insert("", "end", open=True, text=f"Problems ({len(outcome.problems)})")
            for problem in outcome.problems:
                tree.insert(node, "end", text=problem)

    # ------------------------------------------------------------------ work

    def _options_for(self, dry_run: bool) -> engine.Options:
        return engine.Options(
            inputs=list(self.inputs), out=Path(self.out), logs=list(self.extra_logs), skip_logs=sorted(self.skip_logs),
            official_tags=self.official, patch=self.patch, convert=self.convert, reconvert=sorted(self.reconvert),
            update=list(self.update_dirs), author=self.author.strip() or None, stricter_futa=self.stricter_futa,
            scene_builder=self.scene_builder.strip() or None, zip=self.zip, dry_run=dry_run,
            skip_packs=sorted(self.skip_packs), skip_registries=sorted(self.skip_registries))

    def _scan_key(self) -> tuple:
        return tuple(str(p) for p in self.inputs), tuple(str(p) for p in self.extra_logs)

    def _stale(self) -> None:
        """A tick or an option changed: preview again shortly."""
        if self._preview_job:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(500, lambda: self._start("preview"))

    def _start(self, kind: str, rescan: bool = False) -> None:
        self._preview_job = None
        if self.busy:
            self.pending = "run" if "run" in (kind, self.pending) else kind
            return
        if not self.inputs:
            self.status_var.set("Menu > Options to add your load order.")
            return
        if kind == "run":
            out = Path(self.out)
            if not engine.out_is_usable(out):
                messagebox.showerror("slsbPatcher", f"{out} is not empty and is not an slsbPatcher output.\n\n"
                                     "Choose another output folder in Menu > Options.")
                return
        self._remember()
        options = self._options_for(dry_run=(kind == "preview"))
        key = self._scan_key()
        reuse = None if rescan else (self.scanned if self.scan_key == key else None)
        self.busy = True
        self.run_button.configure(text="Working...", state="disabled")
        if kind == "run":
            self._set_report("")
            self.status_var.set("Patching and converting...")
        else:
            self.status_var.set("Reading the load order..." if reuse is None else "Previewing...")
        threading.Thread(target=self._work, args=(kind, options, key, reuse), daemon=True).start()

    def _work(self, kind: str, options: engine.Options, key: tuple, reuse: engine.Scan | None) -> None:
        try:
            found = reuse
            if found is None:
                found = engine.scan(options.inputs, options.logs)
                self.messages.put(("scan", found, key))
            self.messages.put(("outcome", engine.execute(options, found), kind))
        except Exception as error:
            self.messages.put(("error", error, traceback.format_exc()))
        finally:
            self.messages.put(("idle",))

    def _poll(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "log":
                    if self.busy:
                        self._append_report(message[1])
                    self.status_var.set(message[1][:140])
                elif kind == "scan":
                    self.scanned, self.scan_key = message[1], message[2]
                    self._rows_from_scan()
                    self._refresh_caption()
                    self._fill_lists()
                elif kind == "outcome":
                    self.outcome = outcome = message[1]
                    runlog.write(outcome.report)
                    self._set_report("\n".join(outcome.report))
                    self._fill_changes(outcome)
                    self._fill_lists()
                    if message[2] == "run":
                        self._after_run(outcome)
                    else:
                        self._after_preview(outcome)
                elif kind == "error":
                    self._append_report(message[2])
                    self.status_var.set(f"Failed: {message[1]}")
                    messagebox.showerror("slsbPatcher", str(message[1]))
                elif kind == "idle":
                    self.busy = False
                    self.run_button.configure(state="normal")
                    if self.run_button["text"] == "Working...":
                        self.run_button.configure(text="Run")
                    if self.pending:
                        pending, self.pending = self.pending, None
                        self._start(pending)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _after_preview(self, o: engine.Outcome) -> None:
        parts = []
        if o.patched:
            parts.append(f"{_count(len(o.patched), 'registry', 'registries')} to patch")
        if o.would_convert:
            parts.append(f"{_count(len(o.would_convert), 'pack')} to convert")
        left = len(o.left_alone_packs) + len(o.left_alone_registries)
        if left:
            parts.append(f"{left} left alone")
        text = "Preview: " + (", ".join(parts) if parts else "nothing to do") + ". Tick or untick, then press Run."
        if o.problems:
            text += f" {_count(len(o.problems), 'problem')} in the report."
        self.status_var.set(text)
        self.run_button.configure(text="Run" if o.patched or o.would_convert else "Run (nothing to write)")

    def _after_run(self, o: engine.Outcome) -> None:
        instance = install.mo2()
        if o.out and instance and o.out.parent == instance.mods.resolve():
            text = (f"Wrote {_count(o.written, 'file')} to the mod \"{o.out.name}\". Close slsbPatcher so MO2 picks it up"
                    + (", then run Pandora." if o.converted else "."))
        elif o.out:
            text = f"Wrote {_count(o.written, 'file')} to {o.out}. Install that folder as a mod with the highest priority."
            if o.converted:
                text += " Then run Pandora."
        else:
            text = "Nothing to write."
        if o.problems:
            text += f" {_count(len(o.problems), 'problem')} in the report."
        self.status_var.set(text)
        self.run_button.configure(text="Done. Run again")

    # ------------------------------------------------------------------ settings

    def _remember(self) -> None:
        self.settings.update({
            "theme": self.theme, "geometry": self.root.geometry(),
            "inputs": [str(p) for p in self.inputs], "logs": [str(p) for p in self.extra_logs],
            "skip_logs": sorted(self.skip_logs), "skip_packs": sorted(self.skip_packs),
            "skip_registries": sorted(self.skip_registries), "reconvert": sorted(self.reconvert),
            "update": [str(p) for p in self.update_dirs], "out": self.out, "zip": self.zip, "patch": self.patch,
            "convert": self.convert, "official_tags": self.official, "stricter_futa": self.stricter_futa,
            "author": self.author, "scene_builder": self.scene_builder})
        _save_settings(self.settings)

    def _close(self) -> None:
        self._remember()
        self.root.destroy()


class OptionsDialog(tk.Toplevel):
    """Load order, extra logs, what to do, converter settings, output. OK applies them."""

    def __init__(self, app: App):
        super().__init__(app.root, bg=app.colors["bg"])
        self.app = app
        self.result: str | None = None
        self.title("Options")
        self.transient(app.root)
        self.resizable(True, True)
        self.inputs = list(app.inputs)
        self.logs = list(app.extra_logs)
        self.update_dirs = list(app.update_dirs)
        self.patch_var = tk.BooleanVar(value=app.patch)
        self.convert_var = tk.BooleanVar(value=app.convert)
        self.official_var = tk.BooleanVar(value=app.official)
        self.futa_var = tk.BooleanVar(value=app.stricter_futa)
        self.zip_var = tk.BooleanVar(value=app.zip)
        self.author_var = tk.StringVar(value=app.author)
        self.sb_var = tk.StringVar(value=app.scene_builder)
        self.out_var = tk.StringVar(value=app.out)
        self._build()
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        y = app.root.winfo_rooty() + 40
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_visibility()  # a grab on a window that is not mapped yet fails
        self.grab_set()
        self.wait_window()

    def _listbox(self, parent, height):
        c = self.app.colors
        box = tk.Listbox(parent, height=height, bg=c["field"], fg=c["text"], selectbackground=c["select"],
                         selectforeground=c["text"], relief="flat", bd=0, highlightthickness=1,
                         highlightbackground=c["border"], highlightcolor=c["accent"], font=UI, activestyle="none")
        return box

    def _build(self) -> None:
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        frame = ttk.LabelFrame(body, text="Load order", padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        self.inputs_box = self._listbox(frame, 4)
        self.inputs_box.grid(row=0, column=0, sticky="ew")
        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=1, sticky="n", padx=(8, 0))
        for text, command in (("Add folder...", self._add_input), ("Remove", self._remove_input),
                              ("Move up", lambda: self._move_input(-1)), ("Move down", lambda: self._move_input(1))):
            ttk.Button(buttons, text=text, command=command, width=14).pack(fill="x", pady=(0, 3))
        ttk.Label(frame, style="Muted.TLabel", wraplength=700, justify="left", text=(
            "An MO2 instance or profile folder (an instance follows the profile MO2 has selected), a Data folder, "
            "or a folder of extracted SLAL packs. A lower row wins over the rows above it.")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        frame = ttk.LabelFrame(body, text="Extra action logs (besides the ones installed in the load order)", padding=8)
        frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        self.logs_box = self._listbox(frame, 3)
        self.logs_box.grid(row=0, column=0, sticky="ew")
        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=1, sticky="n", padx=(8, 0))
        for text, command in (("Add file...", self._add_log_file), ("Add folder...", self._add_log_folder),
                              ("Remove", self._remove_log)):
            ttk.Button(buttons, text=text, command=command, width=14).pack(fill="x", pady=(0, 3))

        frame = ttk.LabelFrame(body, text="What to do", padding=8)
        frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(frame, text="Patch existing conversions with the ticked action logs (tags only)",
                        variable=self.patch_var).pack(anchor="w")
        ttk.Checkbutton(frame, text="Convert ticked SLAL packs that have no conversion yet",
                        variable=self.convert_var).pack(anchor="w", pady=(3, 0))
        ttk.Checkbutton(frame, text="When patching, also apply lines naming P+ official tags (anal, leadin, oral ...) "
                        "to every stage", variable=self.official_var).pack(anchor="w", pady=(3, 0))
        ttk.Label(frame, style="Muted.TLabel", wraplength=700, justify="left", text=(
            "Official tags are off by default, as in SLATE NG: P+ reads them per stage, so a scene-wide "
            "\"leadin\" makes every stage a lead-in.")).pack(anchor="w", pady=(2, 0))

        frame = ttk.LabelFrame(body, text="Converting", padding=8)
        frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Pack author").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.author_var, width=30).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Checkbutton(frame, text="Stricter futa (slsb-convert.py -sf: no extra futa flag on strap-on positions)",
                        variable=self.futa_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Earlier conversions to keep the pack hash and ids from (folders of *.slsb.json; "
                  "installed ones are found by themselves):").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.update_box = self._listbox(frame, 2)
        self.update_box.grid(row=3, column=0, columnspan=2, sticky="ew")
        buttons = ttk.Frame(frame)
        buttons.grid(row=3, column=2, sticky="n", padx=(8, 0))
        ttk.Button(buttons, text="Add folder...", command=self._add_update, width=14).pack(fill="x")
        ttk.Button(buttons, text="Remove", command=self._remove_update, width=14).pack(fill="x", pady=(3, 0))
        ttk.Label(frame, text="scene_builder_cli.exe").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=self.sb_var).grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Button(frame, text="Browse...", command=self._browse_sb).grid(row=4, column=2, padx=(8, 0), pady=(8, 0))
        try:
            found = f"the bundled one: {scenebuilder.find()}"
        except scenebuilder.SceneBuilderError:
            found = "none found; converting needs it"
        ttk.Label(frame, style="Muted.TLabel", text=f"Leave empty for {found}").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(2, 0))

        frame = ttk.LabelFrame(body, text="Output", padding=8)
        frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text="Folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.out_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Browse...", command=self._browse_out).grid(row=0, column=2)
        ttk.Checkbutton(frame, text="Also pack it into a .zip beside the folder", variable=self.zip_var).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(frame, style="Muted.TLabel", wraplength=700, justify="left", text=(
            "The folder is replaced on every run. Installed in MO2, the default is the mod \"slsbPatcher Output\" "
            "in MO2's mods folder, which MO2 lists once slsbPatcher closes.")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="OK", command=self._ok, width=12).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Cancel", command=self.destroy, width=12).pack(side="left")
        self._fill()

    def _fill(self) -> None:
        self.inputs_box.delete(0, "end")
        for path in self.inputs:
            self.inputs_box.insert("end", f"{path}    [{_describe_input(path)}]")
        self.logs_box.delete(0, "end")
        for path in self.logs:
            self.logs_box.insert("end", str(path))
        self.update_box.delete(0, "end")
        for path in self.update_dirs:
            self.update_box.insert("end", str(path))

    def _selected(self, box) -> int | None:
        selection = box.curselection()
        return int(selection[0]) if selection else None

    def _add_input(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="An MO2 instance or profile folder, a Data folder, or a folder of SLAL packs")
        if folder:
            self.inputs.append(Path(folder))
            self._fill()

    def _remove_input(self) -> None:
        index = self._selected(self.inputs_box)
        if index is not None:
            del self.inputs[index]
            self._fill()

    def _move_input(self, delta: int) -> None:
        index = self._selected(self.inputs_box)
        if index is None or not 0 <= index + delta < len(self.inputs):
            return
        self.inputs[index], self.inputs[index + delta] = self.inputs[index + delta], self.inputs[index]
        self._fill()
        self.inputs_box.selection_set(index + delta)

    def _add_log_file(self) -> None:
        for file in filedialog.askopenfilenames(parent=self, title="SLATE action logs", filetypes=[("SLATE action log", "*.json")]):
            if Path(file) not in self.logs:
                self.logs.append(Path(file))
        self._fill()

    def _add_log_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="A folder of SLATE action logs")
        if not folder:
            return
        found, _ = engine.read_extra_logs([Path(folder)])
        for f in found:
            if f.path not in self.logs:
                self.logs.append(f.path)
        if not found:
            messagebox.showinfo("slsbPatcher", "That folder has no file with a slate.actionlog list.", parent=self)
        self._fill()

    def _remove_log(self) -> None:
        index = self._selected(self.logs_box)
        if index is not None:
            del self.logs[index]
            self._fill()

    def _add_update(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="A folder of *.slsb.json from an earlier conversion")
        if folder:
            self.update_dirs.append(Path(folder))
            self._fill()

    def _remove_update(self) -> None:
        index = self._selected(self.update_box)
        if index is not None:
            del self.update_dirs[index]
            self._fill()

    def _browse_sb(self) -> None:
        file = filedialog.askopenfilename(parent=self, title="scene_builder_cli.exe", filetypes=[("Program", "*.exe"), ("All", "*")])
        if file:
            self.sb_var.set(file)

    def _browse_out(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Output folder (replaced on every run)")
        if folder:
            self.out_var.set(folder)

    def _ok(self) -> None:
        app = self.app
        rescan = (self.inputs != app.inputs or self.logs != app.extra_logs)
        changed = rescan or (
            self.update_dirs != app.update_dirs or self.patch_var.get() != app.patch or self.convert_var.get() != app.convert
            or self.official_var.get() != app.official or self.futa_var.get() != app.stricter_futa
            or self.zip_var.get() != app.zip or self.author_var.get() != app.author
            or self.sb_var.get() != app.scene_builder or self.out_var.get().strip() != app.out)
        app.inputs, app.extra_logs, app.update_dirs = self.inputs, self.logs, self.update_dirs
        app.patch, app.convert, app.official = self.patch_var.get(), self.convert_var.get(), self.official_var.get()
        app.stricter_futa, app.zip, app.author = self.futa_var.get(), self.zip_var.get(), self.author_var.get()
        app.scene_builder, app.out = self.sb_var.get(), self.out_var.get().strip() or str(install.default_out())
        app._remember()
        self.result = "scan" if rescan else ("preview" if changed else None)
        self.destroy()


def launch(inputs: list[Path] | None = None) -> int:
    if sys.stdout is None:  # a windowed exe has no console to print to
        sys.stdout = sys.stderr = open(os.devnull, "w")
    runlog.start("window")
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root, list(inputs or []))
    root.mainloop()
    return 0
