"""The slsbPatcher window: the same work as the command line, with the options on screen.

Preview reads the load order and shows what a run would do; Run writes the output folder.
Settings are remembered in %APPDATA%/slsbPatcher/settings.json.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, actionlog, console, engine, gamedata, scenebuilder

CHECKED, UNCHECKED = "☑", "☐"
MONO = ("Consolas", 9)
GREY = "#5f6368"


def _settings_path() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "slsbPatcher" / "settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(settings: dict) -> None:
    try:
        path = _settings_path()
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
        layers = gamedata.mo2_layers(path)
        if layers is not None:
            return f"MO2 profile, {sum(not layer.name.startswith('(') for layer in layers)} enabled mods"
        if gamedata.looks_like_data(path):
            return "one mod / Data folder"
        return f"folder of {len(gamedata.layers_for_input(path))} pack folders"
    except Exception as error:  # a bad folder must not break the window
        return f"unreadable: {error}"


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
        self.messages: queue.Queue = queue.Queue()
        self.busy = False
        self.scanned: engine.Scan | None = None
        self.scan_key: tuple | None = None
        self.outcome: engine.Outcome | None = None

        s = self.settings
        self.inputs = inputs or [Path(p) for p in s.get("inputs", [])]
        self.extra_logs = [Path(p) for p in s.get("logs", [])]
        self.skip_logs = set(s.get("skip_logs", []))     # log file names, lower case
        self.reconvert = set(s.get("reconvert", []))     # SLAL json file names, lower case
        self.update_dirs = [Path(p) for p in s.get("update", [])]
        default_out = (console.exe_folder() if getattr(sys, "frozen", False) else Path.cwd()) / "slsbPatcher Output"
        self.out_var = tk.StringVar(value=s.get("out", str(default_out)))
        self.zip_var = tk.BooleanVar(value=s.get("zip", False))
        self.patch_var = tk.BooleanVar(value=s.get("patch", True))
        self.convert_var = tk.BooleanVar(value=s.get("convert", True))
        self.official_var = tk.BooleanVar(value=s.get("official_tags", False))
        self.futa_var = tk.BooleanVar(value=s.get("stricter_futa", False))
        self.author_var = tk.StringVar(value=s.get("author", ""))
        self.sb_var = tk.StringVar(value=s.get("scene_builder", ""))
        self.status_var = tk.StringVar(value="Add your load order, then press Preview.")
        for var in (self.patch_var, self.convert_var, self.official_var, self.futa_var, self.author_var):
            var.trace_add("write", lambda *_: self._stale())

        root.title(f"slsbPatcher {__version__}")
        root.minsize(980, 700)
        self._build()
        self._fill_inputs()
        self._fill_logs()
        self._fill_update()

        handler = _QueueHandler(self.messages)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger("slsbpatcher").addHandler(handler)
        logging.getLogger("slsbpatcher").setLevel(logging.INFO)
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)
        if inputs:
            root.after(200, self.preview)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        main = ttk.Frame(root, padding=10)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        # Load order
        frame = ttk.LabelFrame(main, text="Load order", padding=8)
        frame.grid(row=0, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        self.inputs_tree = ttk.Treeview(frame, columns=("kind",), height=3, selectmode="browse")
        self.inputs_tree.heading("#0", text="Folder")
        self.inputs_tree.heading("kind", text="Read as")
        self.inputs_tree.column("#0", width=640, stretch=True)
        self.inputs_tree.column("kind", width=240, stretch=False)
        self.inputs_tree.grid(row=0, column=0, sticky="nsew")
        buttons = ttk.Frame(frame)
        buttons.grid(row=0, column=1, sticky="n", padx=(8, 0))
        for text, command in (("Add folder...", self.add_input), ("Remove", self.remove_input),
                              ("Move up", lambda: self.move_input(-1)), ("Move down", lambda: self.move_input(1))):
            ttk.Button(buttons, text=text, command=command).pack(fill="x", pady=(0, 4))
        ttk.Label(frame, foreground=GREY, text="An MO2 profile folder (e.g. MO2\\profiles\\Default), a Data folder, or a "
                  "folder of extracted SLAL packs. A lower row wins over the rows above it.").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # Tabs
        self.tabs = ttk.Notebook(main)
        self.tabs.grid(row=1, column=0, sticky="nsew", pady=10)
        self.tabs.add(self._build_summary(), text="Summary")
        self.tabs.add(self._build_packs(), text="SLAL packs")
        self.tabs.add(self._build_logs(), text="Action logs")
        self.tabs.add(self._build_options(), text="Options")
        self.tabs.add(self._build_report(), text="Full report")

        # Output
        out = ttk.Frame(main)
        out.grid(row=2, column=0, sticky="ew")
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Output folder").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(out, textvariable=self.out_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(out, text="Browse...", command=self.browse_out).grid(row=0, column=2, padx=(8, 0))
        ttk.Checkbutton(out, text="Also make a .zip", variable=self.zip_var).grid(row=0, column=3, padx=(12, 0))

        # Actions
        actions = ttk.Frame(main)
        actions.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(4, weight=1)
        self.preview_button = ttk.Button(actions, text="Preview", command=self.preview)
        self.preview_button.grid(row=0, column=0)
        self.run_button = ttk.Button(actions, text="Run", command=self.run)
        self.run_button.grid(row=0, column=1, padx=(8, 0))
        self.open_button = ttk.Button(actions, text="Open output folder", command=self.open_output, state="disabled")
        self.open_button.grid(row=0, column=2, padx=(8, 0))
        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=120)
        self.progress.grid(row=0, column=3, padx=(16, 8))
        self.progress.grid_remove()
        ttk.Label(actions, textvariable=self.status_var, foreground=GREY).grid(row=0, column=4, sticky="w")

    def _scrolled(self, parent, widget):
        bar = ttk.Scrollbar(parent, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=bar.set)
        return bar

    def _build_summary(self):
        tab = ttk.Frame(self.tabs, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        panes = ttk.Panedwindow(tab, orient="vertical")
        panes.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(panes)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)
        self.summary_text = tk.Text(top, height=13, wrap="word", font=MONO, relief="flat", padx=6, pady=4)
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        self._scrolled(top, self.summary_text).grid(row=0, column=1, sticky="ns")
        self._set_text(self.summary_text, "Press Preview to see what slsbPatcher would patch and convert.")
        panes.add(top, weight=1)

        bottom = ttk.Frame(panes)
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(0, weight=1)
        self.summary_tree = ttk.Treeview(bottom, columns=("added", "removed", "detail"))
        for column, text, width, anchor in (("#0", "What", 460, "w"), ("added", "Added", 70, "e"),
                                            ("removed", "Removed", 70, "e"), ("detail", "Details", 300, "w")):
            self.summary_tree.heading(column, text=text, anchor=anchor)
            self.summary_tree.column(column, width=width, anchor=anchor, stretch=column in ("#0", "detail"))
        self.summary_tree.grid(row=0, column=0, sticky="nsew")
        self._scrolled(bottom, self.summary_tree).grid(row=0, column=1, sticky="ns")
        panes.add(bottom, weight=2)
        return tab

    def _build_packs(self):
        tab = ttk.Frame(self.tabs, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        self.packs_label = ttk.Label(tab, text="Press Preview to list the SLAL packs of the load order.")
        self.packs_label.grid(row=0, column=0, sticky="w", pady=(0, 6))
        columns = ("reconvert", "json", "mod", "animations", "covered", "status")
        self.packs_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, text, width, stretch in (("reconvert", "Re-convert", 80, False), ("json", "SLAL json", 230, True),
                                             ("mod", "Mod", 300, True), ("animations", "Animations", 80, False),
                                             ("covered", "In a registry", 90, False), ("status", "What happens", 260, True)):
            self.packs_tree.heading(column, text=text, anchor="w")
            self.packs_tree.column(column, width=width, stretch=stretch, anchor="center" if column == "reconvert" else "w")
        self.packs_tree.grid(row=1, column=0, sticky="nsew")
        self._scrolled(tab, self.packs_tree).grid(row=1, column=1, sticky="ns")
        self.packs_tree.bind("<Button-1>", self._toggle_reconvert)
        ttk.Label(tab, foreground=GREY, wraplength=900, justify="left", text=(
            "A pack that already has a registry is patched with the action logs, nothing else. Tick Re-convert to build "
            "its registry again from the SLAL json instead: it keeps the old registry's pack hash, scene ids and stage ids "
            "and replaces it, but its tags then come only from the action logs used in this run.")).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return tab

    def _build_logs(self):
        tab = ttk.Frame(self.tabs, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        columns = ("use", "log", "source", "lines", "adds", "removes", "official")
        self.logs_tree = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse")
        for column, text, width, stretch in (("use", "Use", 50, False), ("log", "Action log", 300, True),
                                             ("source", "From", 300, True), ("lines", "Lines", 70, False),
                                             ("adds", "AddTag", 70, False), ("removes", "RemoveTag", 80, False),
                                             ("official", "Official tags", 90, False)):
            self.logs_tree.heading(column, text=text, anchor="w")
            self.logs_tree.column(column, width=width, stretch=stretch, anchor="center" if column == "use" else "w")
        self.logs_tree.grid(row=0, column=0, sticky="nsew")
        self._scrolled(tab, self.logs_tree).grid(row=0, column=1, sticky="ns")
        self.logs_tree.bind("<Button-1>", self._toggle_log)
        buttons = ttk.Frame(tab)
        buttons.grid(row=0, column=2, sticky="n", padx=(8, 0))
        for text, command in (("Add log file...", self.add_log_file), ("Add log folder...", self.add_log_folder),
                              ("Remove added log", self.remove_log)):
            ttk.Button(buttons, text=text, command=command).pack(fill="x", pady=(0, 4))
        ttk.Label(tab, foreground=GREY, wraplength=900, justify="left", text=(
            "Logs installed in the load order (SKSE\\Plugins\\SLATE) show up after Preview. Add others, such as Hentairim "
            "tag logs, with the buttons. Untick a log to leave it out. \"Official tags\" counts lines naming one of "
            "P+'s 55 official tags, which are not patched unless the option for them is on.")).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        return tab

    def _build_options(self):
        tab = ttk.Frame(self.tabs, padding=10)
        tab.columnconfigure(0, weight=1)

        todo = ttk.LabelFrame(tab, text="What to do", padding=8)
        todo.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(todo, text="Patch existing conversions with the action logs (tags only)",
                        variable=self.patch_var).pack(anchor="w")
        ttk.Checkbutton(todo, text="Convert SLAL packs that have no conversion yet",
                        variable=self.convert_var).pack(anchor="w", pady=(4, 0))

        patching = ttk.LabelFrame(tab, text="Patching", padding=8)
        patching.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(patching, text="Also apply lines naming P+ official tags (anal, leadin, oral ...) to every stage",
                        variable=self.official_var).pack(anchor="w")
        ttk.Label(patching, foreground=GREY, wraplength=880, justify="left", text=(
            "Off by default, as in SLATE NG: P+ reads its official tags per stage, so a scene-wide \"leadin\" makes every "
            "stage a lead-in.")).pack(anchor="w", pady=(2, 0))

        converting = ttk.LabelFrame(tab, text="Converting", padding=8)
        converting.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        converting.columnconfigure(1, weight=1)
        ttk.Label(converting, text="Pack author").grid(row=0, column=0, sticky="w")
        ttk.Entry(converting, textvariable=self.author_var, width=30).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Checkbutton(converting, text="Stricter futa (slsb-convert.py -sf: no extra futa flag on strap-on positions)",
                        variable=self.futa_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(converting, text="Earlier conversions to keep the pack hash and ids from (folders of *.slsb.json; "
                  "installed ones are found by themselves):").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self.update_list = tk.Listbox(converting, height=3)
        self.update_list.grid(row=3, column=0, columnspan=2, sticky="ew")
        ub = ttk.Frame(converting)
        ub.grid(row=3, column=2, sticky="n", padx=(8, 0))
        ttk.Button(ub, text="Add folder...", command=self.add_update).pack(fill="x")
        ttk.Button(ub, text="Remove", command=self.remove_update).pack(fill="x", pady=(4, 0))

        sb = ttk.LabelFrame(tab, text="Scene Builder", padding=8)
        sb.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        sb.columnconfigure(1, weight=1)
        ttk.Label(sb, text="scene_builder_cli.exe").grid(row=0, column=0, sticky="w")
        ttk.Entry(sb, textvariable=self.sb_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(sb, text="Browse...", command=self.browse_sb).grid(row=0, column=2)
        try:
            found = str(scenebuilder.find())
        except scenebuilder.SceneBuilderError:
            found = "not found: converting needs it"
        ttk.Label(sb, foreground=GREY, text=f"Leave empty to use the bundled one ({found}).").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        return tab

    def _build_report(self):
        tab = ttk.Frame(self.tabs, padding=8)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.report_text = tk.Text(tab, wrap="none", font=MONO, relief="flat", padx=6, pady=4)
        self.report_text.grid(row=0, column=0, sticky="nsew")
        self._scrolled(tab, self.report_text).grid(row=0, column=1, sticky="ns")
        bar = ttk.Scrollbar(tab, orient="horizontal", command=self.report_text.xview)
        self.report_text.configure(xscrollcommand=bar.set)
        bar.grid(row=1, column=0, sticky="ew")
        return tab

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    @staticmethod
    def _append_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.insert("end", text + "\n")
        widget.see("end")
        widget.configure(state="disabled")

    def _stale(self) -> None:
        if self.outcome and not self.busy:
            self.status_var.set("Settings changed: press Preview to update the summary.")

    def _options(self, dry_run: bool) -> engine.Options:
        return engine.Options(
            inputs=list(self.inputs), out=Path(self.out_var.get().strip()), logs=list(self.extra_logs),
            skip_logs=sorted(self.skip_logs), official_tags=self.official_var.get(), patch=self.patch_var.get(),
            convert=self.convert_var.get(), reconvert=sorted(self.reconvert), update=list(self.update_dirs),
            author=self.author_var.get().strip() or None, stricter_futa=self.futa_var.get(),
            scene_builder=self.sb_var.get().strip() or None, zip=self.zip_var.get(), dry_run=dry_run)

    def _remember(self) -> None:
        self.settings.update({
            "inputs": [str(p) for p in self.inputs], "logs": [str(p) for p in self.extra_logs],
            "skip_logs": sorted(self.skip_logs), "reconvert": sorted(self.reconvert),
            "update": [str(p) for p in self.update_dirs], "out": self.out_var.get(), "zip": self.zip_var.get(),
            "patch": self.patch_var.get(), "convert": self.convert_var.get(), "official_tags": self.official_var.get(),
            "stricter_futa": self.futa_var.get(), "author": self.author_var.get(), "scene_builder": self.sb_var.get()})
        _save_settings(self.settings)

    # ------------------------------------------------------------------ load order

    def _fill_inputs(self) -> None:
        self.inputs_tree.delete(*self.inputs_tree.get_children())
        for index, path in enumerate(self.inputs):
            self.inputs_tree.insert("", "end", iid=str(index), text=str(path), values=(_describe_input(path),))

    def add_input(self) -> None:
        folder = filedialog.askdirectory(title="An MO2 profile folder, a Data folder, or a folder of SLAL packs",
                                         mustexist=True)
        if folder:
            self.inputs.append(Path(folder))
            self._fill_inputs()
            self._stale()

    def _selected_input(self) -> int | None:
        selection = self.inputs_tree.selection()
        return int(selection[0]) if selection else None

    def remove_input(self) -> None:
        index = self._selected_input()
        if index is not None:
            del self.inputs[index]
            self._fill_inputs()
            self._stale()

    def move_input(self, delta: int) -> None:
        index = self._selected_input()
        if index is None or not 0 <= index + delta < len(self.inputs):
            return
        self.inputs[index], self.inputs[index + delta] = self.inputs[index + delta], self.inputs[index]
        self._fill_inputs()
        self.inputs_tree.selection_set(str(index + delta))
        self._stale()

    # ------------------------------------------------------------------ logs

    def _fill_logs(self) -> None:
        tree = self.logs_tree
        tree.delete(*tree.get_children())
        installed = self.scanned.installed_logs if self.scanned else []
        if self.scanned and self.scan_key == self._scan_key():
            extra = self.scanned.extra_logs
        else:
            extra, _ = engine.read_extra_logs(self.extra_logs)
        actionlog.build_logs(installed + extra, self.official_var.get())  # fills the line counts
        for kind, files in (("installed", installed), ("added", extra)):
            for index, f in enumerate(files):
                use = UNCHECKED if engine.is_skipped(f, sorted(self.skip_logs)) else CHECKED
                source = f.layer if kind == "installed" else str(f.path.parent)
                tree.insert("", "end", iid=f"{kind}:{index}:{f.path.name.lower()}",
                            values=(use, f.path.name, source, len(f.lines), f.adds, f.removes, f.official_tags))

    def _toggle_log(self, event) -> None:
        if self.logs_tree.identify_column(event.x) != "#1" or self.logs_tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.logs_tree.identify_row(event.y)
        if not row:
            return
        name = row.split(":", 2)[2]
        self.skip_logs.symmetric_difference_update({name})
        values = list(self.logs_tree.item(row, "values"))
        values[0] = UNCHECKED if name in self.skip_logs else CHECKED
        self.logs_tree.item(row, values=values)
        self._stale()

    def add_log_file(self) -> None:
        files = filedialog.askopenfilenames(title="SLATE action logs", filetypes=[("SLATE action log", "*.json")])
        for file in files:
            if Path(file) not in self.extra_logs:
                self.extra_logs.append(Path(file))
        if files:
            self._fill_logs()
            self._stale()

    def add_log_folder(self) -> None:
        folder = filedialog.askdirectory(title="A folder of SLATE action logs", mustexist=True)
        if not folder:
            return
        found, _ = engine.read_extra_logs([Path(folder)])
        for f in found:
            if f.path not in self.extra_logs:
                self.extra_logs.append(f.path)
        if not found:
            messagebox.showinfo("slsbPatcher", "That folder has no file with a slate.actionlog list.")
        self._fill_logs()
        self._stale()

    def remove_log(self) -> None:
        selection = self.logs_tree.selection()
        if not selection or not selection[0].startswith("added:"):
            messagebox.showinfo("slsbPatcher", "Select an added log. Logs installed in the load order can only be "
                                "unticked.")
            return
        name = selection[0].split(":", 2)[2]
        self.extra_logs = [p for p in self.extra_logs if p.name.lower() != name]
        self._fill_logs()
        self._stale()

    # ------------------------------------------------------------------ options tab

    def _fill_update(self) -> None:
        self.update_list.delete(0, "end")
        for path in self.update_dirs:
            self.update_list.insert("end", str(path))

    def add_update(self) -> None:
        folder = filedialog.askdirectory(title="A folder of *.slsb.json from an earlier conversion", mustexist=True)
        if folder:
            self.update_dirs.append(Path(folder))
            self._fill_update()
            self._stale()

    def remove_update(self) -> None:
        for index in reversed(self.update_list.curselection()):
            del self.update_dirs[index]
        self._fill_update()
        self._stale()

    def browse_sb(self) -> None:
        file = filedialog.askopenfilename(title="scene_builder_cli.exe", filetypes=[("Program", "*.exe"), ("All", "*")])
        if file:
            self.sb_var.set(file)

    def browse_out(self) -> None:
        folder = filedialog.askdirectory(title="Output folder (it is replaced on every run)")
        if folder:
            self.out_var.set(folder)

    # ------------------------------------------------------------------ packs

    def _fill_packs(self) -> None:
        tree = self.packs_tree
        tree.delete(*tree.get_children())
        if not self.scanned:
            return
        new = partial = 0
        for index, item in enumerate(self.scanned.slal):
            total, covered = len(item.animations), item.covered
            name = item.winner.path.name
            ticked = name.lower() in self.reconvert
            if covered and ticked:
                status = "re-convert: replaces its registry"
            elif covered == total and covered:
                status = "has a registry: patched"
            elif covered:
                partial += 1
                status = f"partly converted: {total - covered} animations in no registry"
            elif not total:
                status = "no animations"
            else:
                new += 1
                status = "no registry: converted" if self.convert_var.get() else "no registry (converting is off)"
            box = (CHECKED if ticked else UNCHECKED) if covered else ""
            tree.insert("", "end", iid=str(index), values=(box, name, item.winner.layer.name, total, covered, status))
        self.packs_label.configure(text=f"{len(self.scanned.slal)} SLAL jsons: {len(self.scanned.slal) - new} with a "
                                        f"registry ({partial} only partly), {new} without one.")

    def _toggle_reconvert(self, event) -> None:
        if self.packs_tree.identify_column(event.x) != "#1" or self.packs_tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.packs_tree.identify_row(event.y)
        if not row or not self.scanned:
            return
        item = self.scanned.slal[int(row)]
        if not item.covered:
            return
        self.reconvert.symmetric_difference_update({item.winner.path.name.lower()})
        self._fill_packs()
        self._stale()

    # ------------------------------------------------------------------ summary

    def _fill_summary(self, outcome: engine.Outcome) -> None:
        self._set_text(self.summary_text, "\n".join(outcome.summary))
        self._set_text(self.report_text, "\n".join(outcome.report))
        tree = self.summary_tree
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
                    "" if items_ else "no changes: already in the registries, or for scenes not installed"))
        elif outcome.options.patch:
            reason = ("no action logs were read" if not outcome.logs else
                      "no registries in the load order" if not outcome.registries else
                      "every registry already matches the logs")
            tree.insert("", "end", text="Nothing to patch", values=("", "", reason))
        if outcome.produced:
            node = tree.insert("", "end", open=True, text=f"New conversions ({len(outcome.produced)})",
                               values=("", "", _count(sum(len(p.scenes) for p in outcome.produced), "scene")))
            for p in outcome.produced:
                tree.insert(node, "end", text=f"{p.json_name}  ->  {p.slr_rel.rsplit('/', 1)[-1]}",
                            values=("", "", f"{_count(len(p.scenes), 'scene')}, {p.module}"))
        if outcome.would_convert:
            node = tree.insert("", "end", open=True, text=f"Would convert ({len(outcome.would_convert)})")
            for name, mod in outcome.would_convert:
                tree.insert(node, "end", text=name, values=("", "", mod))
        if outcome.partial:
            node = tree.insert("", "end", text=f"Partly converted packs ({len(outcome.partial)})",
                               values=("", "", "tick Re-convert in SLAL packs to add the rest"))
            for p in outcome.partial:
                item = tree.insert(node, "end", text=p.json_name, values=("", "", f"{len(p.missing)} of {p.total} "
                                                                                   f"animations in no registry"))
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

    def _scan_key(self) -> tuple:
        return tuple(str(p) for p in self.inputs), tuple(str(p) for p in self.extra_logs)

    def preview(self) -> None:
        self._start(dry_run=True)

    def run(self) -> None:
        out = Path(self.out_var.get().strip())
        if not self.out_var.get().strip():
            messagebox.showerror("slsbPatcher", "Choose an output folder.")
            return
        if not engine.out_is_usable(out):
            messagebox.showerror("slsbPatcher", f"{out} is not empty and is not an slsbPatcher output.\n\n"
                                 "Choose another output folder.")
            return
        self._start(dry_run=False)

    def _start(self, dry_run: bool) -> None:
        if self.busy:
            return
        if not self.inputs:
            messagebox.showinfo("slsbPatcher", "Add your MO2 profile folder (or a folder of SLAL packs) to the load order first.")
            return
        self._remember()
        options = self._options(dry_run)
        key = self._scan_key()
        reuse = self.scanned if self.scan_key == key else None
        self.busy = True
        for button in (self.preview_button, self.run_button):
            button.configure(state="disabled")
        self.progress.grid()
        self.progress.start(12)
        self._set_text(self.report_text, "")
        self.status_var.set("Reading the load order..." if reuse is None else ("Working..." if not dry_run else "Previewing..."))
        threading.Thread(target=self._work, args=(options, key, reuse), daemon=True).start()

    def _work(self, options: engine.Options, key: tuple, reuse: engine.Scan | None) -> None:
        try:
            found = reuse
            if found is None:
                found = engine.scan(options.inputs, options.logs)
                self.messages.put(("scan", found, key))
            if not options.dry_run:
                self.messages.put(("status", "Patching and converting..."))
            self.messages.put(("outcome", engine.execute(options, found)))
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
                    self._append_text(self.report_text, message[1])
                    self.status_var.set(message[1][:120])
                elif kind == "status":
                    self.status_var.set(message[1])
                elif kind == "scan":
                    self.scanned, self.scan_key = message[1], message[2]
                    self._fill_packs()
                    self._fill_logs()
                elif kind == "outcome":
                    self.outcome = outcome = message[1]
                    self._fill_summary(outcome)
                    self._fill_packs()
                    self._fill_logs()
                    self.tabs.select(0)
                    self.open_button.configure(state="normal" if outcome.out else "disabled")
                    if outcome.options.dry_run:
                        self.status_var.set("Preview done: nothing was written. Press Run to write the output folder.")
                    elif outcome.out:
                        self.status_var.set(f"Wrote {outcome.written} files to {outcome.out}")
                    else:
                        self.status_var.set("Nothing to write.")
                elif kind == "error":
                    self._append_text(self.report_text, message[2])
                    self.status_var.set(f"Failed: {message[1]}")
                    messagebox.showerror("slsbPatcher", str(message[1]))
                elif kind == "idle":
                    self.busy = False
                    self.progress.stop()
                    self.progress.grid_remove()
                    for button in (self.preview_button, self.run_button):
                        button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def open_output(self) -> None:
        target = self.outcome.out if self.outcome and self.outcome.out else Path(self.out_var.get())
        if target.exists() and hasattr(os, "startfile"):
            os.startfile(target)

    def _close(self) -> None:
        self._remember()
        self.root.destroy()


def launch(inputs: list[Path] | None = None) -> int:
    if sys.stdout is None:  # a windowed exe has no console to print to
        sys.stdout = sys.stderr = open(os.devnull, "w")
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
