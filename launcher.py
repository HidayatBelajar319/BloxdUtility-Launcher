"""Players Launcher — the desktop hub for Bloxd Utility.

    python launcher.py

One tkinter window, six tabs, mirroring the Main website and the Docs site:

* **Home**     — launchers, links, live stats.
* **Code Lab** — the vendored Bloxd Editor engine (editor-core.js,
                 bloxd-editor.js, bloxd-editor.css, copied byte-for-byte out of
                 BloxdUtilityCLI/editor, contract v1.0.0) in a pywebview window,
                 editing snippet files in the data directory.
* **AI**       — the AI panel: key-required provider chain, chat, "save as
                 snippet", and the templates/ player-control folder.
* **Mods**     — the local mods folder: list, install .zip, delete.
* **Docs**     — open the docs site or read local markdown offline.
* **Changelog**— what shipped in this launcher.
* **Settings** — data directory, AI keys, Bloxd.io URL.

Nothing is written inside the git repository: config.json, snippets, mods and
the vocabulary cache all live in the per-user data directory.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import appdata  # noqa: E402
import game as game_module  # noqa: E402
from ai_panel import AiPanel  # noqa: E402

import tkinter as tk  # noqa: E402
from tkinter import filedialog, messagebox, ttk  # noqa: E402

BG = "#12151c"
PANEL = "#161a23"
FG = "#dfe6f2"
MUTED = "#8a94a8"
ACCENT = "#4c8dff"

TIPS = [
    "World Code runs ONCE when the lobby starts (F8 on PC) — put every callback and "
    "every globalThis variable there.",
    "// comments do not work in Bloxd. Use /* block comments */. The Code Lab flags them.",
    "Code Blocks are limited to 500 lines and 16000 characters — the editor counts for you.",
    "The tick callback fires 20x per second. Keep it cheap or everybody lags.",
    "api.setBlockRect() fills a whole area far faster than looping api.setBlock().",
    "Store per-player data keyed by player ID in a global object — never one shared variable.",
    "myId / playerId hold whoever triggered the current Code Block; thisPos is the block's [x, y, z].",
]


# --------------------------------------------------------------------------- #
# Markdown -> styled Text blocks (works with or without the `markdown` package)
# --------------------------------------------------------------------------- #

def _html_to_blocks(markup: str) -> list:
    class Collector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks: list = []
            self.current: list = []
            self.kind = "para"
            self.in_pre = False
            self.in_list = False

        def _flush(self):
            text = "".join(self.current).strip()
            self.current = []
            if text:
                self.blocks.append((self.kind, text))
            self.kind = "list" if self.in_list else "para"

        def handle_starttag(self, tag, attrs):
            if tag == "pre":
                self._flush()
                self.in_pre = True
                self.kind = "code"
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                self._flush()
                self.kind = "h" + tag[1]
            elif tag == "li":
                self._flush()
                self.in_list = True
                self.kind = "list"
            elif tag in ("p", "br", "tr"):
                self._flush()

        def handle_endtag(self, tag):
            if tag == "pre":
                self.in_pre = False
                self._flush()
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "ul", "ol", "table", "tr"):
                self._flush()

        def handle_data(self, data):
            self.current.append(data)

        def close(self):
            super().close()
            self._flush()

    collector = Collector()
    collector.feed(markup)
    return collector.blocks


def _fallback_blocks(text: str) -> list:
    blocks: list = []
    fence = False
    buffer: list = []
    for raw in str(text).splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if fence:
                blocks.append(("code", "\n".join(buffer)))
                buffer = []
            fence = not fence
            continue
        if fence:
            buffer.append(line)
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            blocks.append(("h" + str(len(stripped) - len(stripped.lstrip("#"))), stripped.lstrip("#").strip()))
        elif stripped.startswith(("- ", "* ")):
            blocks.append(("list", "• " + stripped[2:]))
        elif re.match(r"^\d+\.\s", stripped):
            blocks.append(("list", stripped))
        elif set(stripped) <= set("|-: "):
            continue
        else:
            blocks.append(("para", stripped))
    if buffer:
        blocks.append(("code", "\n".join(buffer)))
    return blocks


def markdown_blocks(text: str) -> list:
    try:
        import markdown  # optional dependency
    except Exception:
        return _fallback_blocks(text)
    try:
        return _html_to_blocks(markdown.markdown(text, extensions=["fenced_code", "tables"]))
    except Exception:
        return _fallback_blocks(text)


# --------------------------------------------------------------------------- #
# pywebview bridge for the Code Lab
# --------------------------------------------------------------------------- #

class CodeLabApi:
    """Everything the vendored-editor page may call. Plain JSON types only."""

    def __init__(self, hub: "Launcher"):
        self.hub = hub
        self.current = ""
        self.last_report: dict = {}

    # -- snippets
    def list_snippets(self) -> list:
        return appdata.list_snippets()

    def read_snippet(self, name: str) -> str:
        try:
            return appdata.read_snippet(name)
        except OSError as err:
            return f"/* could not read {name}: {err} */"

    def write_snippet(self, name: str, source: str) -> dict:
        try:
            path = appdata.write_snippet(name, source or "")
        except OSError as err:
            return {"ok": False, "message": str(err)}
        self.current = Path(path).stem
        self.hub.after(0, self.hub.on_snippets_changed)
        return {"ok": True, "name": self.current, "path": path}

    def delete_snippet(self, name: str) -> bool:
        ok = appdata.delete_snippet(name)
        if ok and self.current == name:
            self.current = ""
        self.hub.after(0, self.hub.on_snippets_changed)
        return ok

    def rename_snippet(self, name: str, new_name: str) -> dict:
        try:
            source = appdata.read_snippet(name)
            path = appdata.write_snippet(new_name, source)
            if name != Path(path).stem:
                appdata.delete_snippet(name)
        except OSError as err:
            return {"ok": False, "message": str(err)}
        self.current = Path(path).stem
        self.hub.after(0, self.hub.on_snippets_changed)
        return {"ok": True, "name": self.current}

    def get_current(self) -> str:
        return self.current or str(appdata.config().get("lastSnippet", ""))

    def set_current(self, name: str) -> bool:
        self.current = str(name or "")
        appdata.write_config({"lastSnippet": self.current})
        return True

    def ready(self) -> dict:
        return {"current": self.current, "engine": appdata.engine_version()}

    # -- vocabulary
    def get_cached_vocab(self) -> dict:
        return appdata.read_vocab_cache()

    def save_vocab(self, vocab: dict) -> bool:
        return appdata.write_vocab_cache(vocab)

    def http_get(self, url: str) -> dict:
        return appdata.http_get(url)

    # -- misc
    def report(self, report: dict) -> bool:
        self.last_report = report or {}
        return True

    def open_data_dir(self) -> bool:
        return self.hub.reveal(appdata.data_dir())

    def ask_ai(self, source: str) -> bool:
        self.hub.ask_ai_about(source or "")
        return True


class CodeLabWindow:
    """Owns the pywebview window that hosts assets/code_lab.html."""

    def __init__(self, hub: "Launcher"):
        self.hub = hub
        self.window = None
        self.thread: threading.Thread | None = None
        self.error = ""
        self.pending: tuple = (None, None)
        self.page = appdata.ASSETS / "code_lab.html"

    @property
    def running(self) -> bool:
        return self.window is not None

    def is_open(self) -> bool:
        return self.window is not None

    def open(self, source: str = "", name: str = "") -> None:
        if self.window is not None:
            if source:
                self.send_code(source, name)
            self.focus()
            return
        if not self.page.is_file():
            messagebox.showerror("Code Lab", f"Missing page: {self.page}")
            return

        try:
            import webview
        except Exception as err:
            self.error = str(err)
            messagebox.showwarning(
                "Code Lab",
                f"pywebview is not available ({err}).\n\n"
                "Install it with:  pip install -r requirements.txt\n"
                "The page itself can still be opened in your browser:\n" + str(self.page))
            webbrowser.open(self.page.as_uri())
            return

        api = CodeLabApi(self.hub)
        self.api = api
        debounce = int(appdata.config().get("editorDebounceMs") or 200)
        try:
            self.window = webview.create_window(
                "Code Lab — Players Launcher",
                str(self.page),
                js_api=api,
                width=1280, height=820, min_size=(900, 600),
            )
        except Exception as err:
            self.error = str(err)
            messagebox.showerror("Code Lab", f"Could not create the webview window:\n{err}")
            self.window = None
            return

        def on_loaded():
            time.sleep(0.4)
            if source:
                self.send_code(source, name)

        try:
            self.window.events.loaded += on_loaded
        except Exception:
            pass

        def run():
            started = time.time()
            try:
                webview.start(debug=False)
            except Exception as err:  # pragma: no cover - platform dependent
                self.error = str(err)
                self.hub.after(0, lambda: self.hub.note(f"Code Lab webview stopped: {err}"))
            finally:
                alive_for = time.time() - started
                self.window = None
                if alive_for < 1.5:
                    # No GUI backend (headless session, missing Edge runtime, ...):
                    # keep the launcher useful by opening the page in a browser.
                    self.error = "pywebview had no GUI backend"
                    self.hub.after(0, self._fallback_to_browser)
                self.hub.after(0, self.hub.on_lab_closed)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        time.sleep(0.8)
        self.hub.on_lab_opened()

    def _fallback_to_browser(self) -> None:
        self.hub.note("No pywebview GUI backend — opened the Code Lab page in your browser "
                      "(the file manager needs the packaged window).")
        webbrowser.open(self.page.as_uri())

    def send_code(self, source: str, name: str = "") -> None:
        if self.window is None:
            self.pending = (source, name)
            return
        script = (
            "try {"
            " if (window.labEditor) { window.labEditor.setValue(%s); }"
            " var f = document.getElementById('filename'); if (f) f.value = %s;"
            " if (window.labEditor) window.labEditor.focus();"
            "} catch (e) {}" % (json.dumps(source or ""), json.dumps(name or ""))
        )
        try:
            self.window.evaluate_js(script)
        except Exception as err:
            self.hub.after(0, lambda: self.hub.note(f"Could not push code to the Code Lab: {err}"))

    def get_source(self) -> str:
        if self.window is None:
            return ""
        try:
            value = self.window.evaluate_js(
                "(window.labEditor && window.labEditor.getValue) ? window.labEditor.getValue() : ''")
            return value if isinstance(value, str) else ""
        except Exception:
            return ""

    def focus(self) -> None:
        try:
            if self.window is not None:
                self.window.restore()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# The hub
# --------------------------------------------------------------------------- #

class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        appdata.ensure_dirs()
        self.title(f"{appdata.APP_TITLE} v{appdata.VERSION}")
        self.geometry("1080x720")
        self.minsize(880, 600)
        self.configure(background=BG)

        self.lab = CodeLabWindow(self)
        self.ai: AiPanel | None = None
        self.status = tk.StringVar(value="ready")

        self._build_style()
        self._build_menu()
        self._build_tabs()
        self._refresh_home()
        self._load_docs_index()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # -- chrome ------------------------------------------------------------ #
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=FG)
        style.configure("TButton", padding=(8, 4))
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Sub.TLabel", foreground=MUTED)
        style.configure("TNotebook", background=BG)
        style.configure("TNotebook.Tab", padding=(14, 7))
        style.configure("TLabelframe", background=PANEL, foreground=FG)
        style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open Code Lab", command=self.open_code_lab)
        file_menu.add_command(label="Play Bloxd.io", command=self.play_game)
        file_menu.add_separator()
        file_menu.add_command(label="Reveal data folder", command=lambda: self.reveal(appdata.data_dir()))
        file_menu.add_command(label="Reveal launcher folder", command=lambda: self.reveal(appdata.HOME))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="Docs website", command=lambda: webbrowser.open(appdata.LINKS["docs"]))
        help_menu.add_command(label="Bloxd API vocabulary", command=lambda: webbrowser.open(appdata.LINKS["codeApi"]))
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.configure(menu=menubar)

        footer = ttk.Frame(self, padding=(10, 4))
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, textvariable=self.status, style="Sub.TLabel").pack(side="left")
        ttk.Button(footer, text="Play Bloxd.io", command=self.play_game).pack(side="right", padx=2)
        ttk.Button(footer, text="Open Code Lab", command=self.open_code_lab).pack(side="right", padx=2)

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.home_tab = self._tab_home()
        self.lab_tab = self._tab_code_lab()
        self.ai_tab = ttk.Frame(self.notebook, padding=8)
        self.mods_tab = self._tab_mods()
        self.docs_tab = self._tab_docs()
        self.log_tab = self._tab_changelog()
        self.settings_tab = self._tab_settings()

        self.notebook.add(self.home_tab, text="Home")
        self.notebook.add(self.lab_tab, text="Code Lab")
        self.notebook.add(self.ai_tab, text="AI")
        self.notebook.add(self.mods_tab, text="Mods")
        self.notebook.add(self.docs_tab, text="Docs")
        self.notebook.add(self.log_tab, text="Changelog")
        self.notebook.add(self.settings_tab, text="Settings")

        self.ai = AiPanel(self.ai_tab, self)
        self.ai.pack(fill="both", expand=True)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, _event=None) -> None:
        if self.notebook.select() == str(self.settings_tab):
            self._refresh_stats_labels()

    def _tab_home(self) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=14)
        ttk.Label(tab, text="Players", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text="Bloxd Utility desktop hub — play, code, ask the AI, ship mods.",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 12))

        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Button(row, text="▶  Play Bloxd.io", command=self.play_game).pack(side="left")
        ttk.Button(row, text="Open Code Lab", command=self.open_code_lab).pack(side="left", padx=8)
        ttk.Button(row, text="Ask the AI", command=self.ask_ai_about).pack(side="left")
        ttk.Button(row, text="Install a mod", command=self.install_mod).pack(side="left", padx=8)

        links = ttk.LabelFrame(tab, text="Links", padding=8)
        links.pack(fill="x", pady=(14, 0))
        grid = [("Utility site", "site"), ("Documentation", "docs"),
                ("Bloxd API vocabulary", "codeApi"), ("Texture packs", "texturePacks"),
                ("Code Block wiki", "wiki"), ("Bloxd.io", "bloxd"),
                ("Launcher repo", "launcher"), ("Owner", "official")]
        for index, (label, key) in enumerate(grid):
            ttk.Button(links, text=label, width=18,
                       command=lambda k=key: webbrowser.open(appdata.LINKS[k])
                       ).grid(row=index // 4, column=(index % 4) * 2, sticky="w", padx=4, pady=3)

        self.stats_labels: dict = {}
        stats = ttk.LabelFrame(tab, text="Stats", padding=8)
        stats.pack(fill="x", pady=(12, 0))
        rows = [("Launcher", "version"), ("Bloxd Editor engine", "engine"),
                ("Data folder", "dataDir"), ("Snippets", "snippets"),
                ("Mods", "mods"), ("Templates", "templates"),
                ("API vocabulary cache", "vocabCache"), ("Python", "python"),
                ("tkinter", "tk"), ("pywebview", "pywebview"), ("requests", "requests")]
        for index, (label, key) in enumerate(rows):
            ttk.Label(stats, text=label, style="Sub.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 10))
            value = ttk.Label(stats, text="—", wraplength=620, justify="left")
            value.grid(row=index, column=1, sticky="w")
            self.stats_labels[key] = value
        ttk.Button(stats, text="Refresh", command=self._refresh_home).grid(
            row=len(rows), column=1, sticky="w", pady=(6, 0))

        tips = ttk.LabelFrame(tab, text="Tips", padding=8)
        tips.pack(fill="both", expand=True, pady=(12, 0))
        for tip in TIPS:
            ttk.Label(tips, text="• " + tip, style="Sub.TLabel", wraplength=900,
                      justify="left").pack(anchor="w", pady=2)
        return tab

    def _tab_code_lab(self) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=14)
        ttk.Label(tab, text="Code Lab", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text=(
            "The Bloxd Editor engine is vendored into assets/editor/ "
            f"(engine v{appdata.engine_version()}, copied byte-for-byte from "
            "BloxdUtilityCLI/editor). It opens in its own window with a live "
            "problems panel, Bloxd-API autocomplete and real-time trainReport."),
            style="Sub.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Button(row, text="Open Code Lab window", command=self.open_code_lab).pack(side="left")
        ttk.Button(row, text="Focus window", command=lambda: self.lab.focus()).pack(side="left", padx=8)
        ttk.Button(row, text="Ask AI about this code", command=self.ask_ai_about).pack(side="left")
        ttk.Button(row, text="Reveal data folder", command=lambda: self.reveal(appdata.data_dir())
                   ).pack(side="left", padx=8)

        self.lab_status = ttk.Label(tab, text="Code Lab window: closed", style="Sub.TLabel")
        self.lab_status.pack(anchor="w", pady=(10, 6))

        self.lab_stats = ttk.Label(tab, text="trainReport: —", style="Sub.TLabel",
                                    wraplength=900, justify="left")
        self.lab_stats.pack(anchor="w", pady=(0, 10))

        box = ttk.LabelFrame(tab, text="Snippets (data folder)", padding=8)
        box.pack(fill="both", expand=True)
        self.snippet_list = tk.Listbox(box, font=("Consolas", 10), background=BG, foreground=FG,
                                       highlightthickness=1, highlightbackground="#2a3141")
        self.snippet_list.pack(side="left", fill="both", expand=True)
        side = ttk.Frame(box)
        side.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(side, text="New", command=self.new_snippet).pack(fill="x")
        ttk.Button(side, text="Save", command=self.save_snippet_via_tab).pack(fill="x", pady=4)
        ttk.Button(side, text="Delete", command=self.delete_snippet_via_tab).pack(fill="x")
        ttk.Button(side, text="Refresh", command=self.on_snippets_changed).pack(fill="x", pady=4)
        ttk.Button(side, text="Copy", command=self.copy_snippet_via_tab).pack(fill="x")

        self.snippet_name = tk.StringVar(value="")
        ttk.Entry(tab, textvariable=self.snippet_name).pack(fill="x", pady=(8, 0))
        return tab

    def _tab_mods(self) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=14)
        ttk.Label(tab, text="Mods", style="Title.TLabel").pack(anchor="w")
        ttk.Label(tab, text="Mods are plain files in your data folder — no accounts, no uploads.",
                  style="Sub.TLabel").pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(tab)
        row.pack(fill="x")
        ttk.Button(row, text="Install .zip…", command=self.install_mod).pack(side="left")
        ttk.Button(row, text="Refresh", command=self.refresh_mods).pack(side="left", padx=6)
        ttk.Button(row, text="Delete", command=self.delete_mod).pack(side="left")
        ttk.Button(row, text="Reveal folder", command=lambda: self.reveal(appdata.mods_dir())
                   ).pack(side="left", padx=6)
        ttk.Button(row, text="Open docs site", command=lambda: webbrowser.open(appdata.LINKS["docs"])
                   ).pack(side="right")

        columns = ("name", "size", "modified", "kind")
        self.mod_tree = ttk.Treeview(tab, columns=columns, show="headings", height=14)
        for column, title, width in zip(columns, ("Name", "Size", "Modified", "Kind"),
                                       (360, 90, 160, 120)):
            self.mod_tree.heading(column, text=title)
            self.mod_tree.column(column, width=width, anchor="w")
        self.mod_tree.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(tab, textvariable=self.status, style="Sub.TLabel").pack(anchor="w")
        return tab

    def _tab_docs(self) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=14)
        top = ttk.Frame(tab)
        top.pack(fill="x")
        ttk.Label(top, text="Docs", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Open documentation website", command=self.open_docs_site
                   ).pack(side="right")
        ttk.Button(top, text="Add markdown file…", command=self.add_doc, width=22
                   ).pack(side="right", padx=8)
        ttk.Button(top, text="Refresh", command=self._load_docs_index, width=10
                   ).pack(side="right", padx=8)

        body = ttk.PanedWindow(tab, orient="horizontal")
        body.pack(fill="both", expand=True, pady=(10, 0))
        left = ttk.Frame(body)
        self.doc_list = tk.Listbox(left, width=34, font=("Consolas", 9), background=BG,
                                   foreground=FG, highlightthickness=1, highlightbackground="#2a3141")
        self.doc_list.pack(fill="both", expand=True)
        self.doc_list.bind("<<ListboxSelect>>", self._on_doc_select)
        right = ttk.Frame(body)
        self.doc_text = tk.Text(right, wrap="word", background=BG, foreground=FG, relief="flat",
                                highlightthickness=1, highlightbackground="#2a3141",
                                font=("Segoe UI", 10), padx=12, pady=8)
        scroll = ttk.Scrollbar(right, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.doc_text.pack(side="left", fill="both", expand=True)
        self.doc_text.configure(yscrollcommand=scroll.set)
        scroll.configure(command=self.doc_text.yview)
        self.doc_text.tag_configure("h1", font=("Segoe UI", 17, "bold"), foreground="#ffffff",
                                    spacing1=10, spacing3=6)
        self.doc_text.tag_configure("h2", font=("Segoe UI", 14, "bold"), foreground=ACCENT,
                                    spacing1=8, spacing3=4)
        self.doc_text.tag_configure("h3", font=("Segoe UI", 12, "bold"), foreground="#9fd0ff",
                                    spacing1=6, spacing3=3)
        self.doc_text.tag_configure("code", font=("Consolas", 10), background="#0d1017",
                                    foreground="#9fd0ff", lmargin1=16, lmargin2=16)
        self.doc_text.tag_configure("list", lmargin1=18, spacing1=2)
        self.doc_text.tag_configure("para", spacing3=6)
        self.doc_text.configure(state="disabled")
        body.add(left, weight=1)
        body.add(right, weight=4)
        return tab

    def _tab_changelog(self) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=14)
        ttk.Label(tab, text="Changelog", style="Title.TLabel").pack(anchor="w")
        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(0, 8))
        ttk.Button(row, text="Utility site changelog", width=24,
                   command=lambda: webbrowser.open(appdata.LINKS["site"] + "changelog")
                   ).pack(side="left")
        ttk.Button(row, text="Docs changelog", width=20,
                   command=lambda: webbrowser.open(appdata.LINKS["docs"] + "changelog")
                   ).pack(side="left", padx=6)
        self.log_text = tk.Text(tab, wrap="word", background=BG, foreground=FG, relief="flat",
                                highlightthickness=1, highlightbackground="#2a3141",
                                font=("Segoe UI", 10), padx=12, pady=8)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_configure("h1", font=("Segoe UI", 16, "bold"), foreground="#ffffff", spacing1=8)
        self.log_text.tag_configure("h2", font=("Segoe UI", 13, "bold"), foreground=ACCENT, spacing1=6)
        self.log_text.tag_configure("code", font=("Consolas", 10), background="#0d1017",
                                    foreground="#9fd0ff", lmargin1=16, lmargin2=16)
        self.log_text.tag_configure("list", lmargin1=18, spacing1=2)
        self.log_text.configure(state="disabled")
        return tab

    def _tab_settings(self) -> ttk.Frame:
        tab = ttk.Frame(self.notebook, padding=14)
        ttk.Label(tab, text="Settings", style="Title.TLabel").pack(anchor="w")

        data_box = ttk.LabelFrame(tab, text="Data folder", padding=8)
        data_box.pack(fill="x", pady=(12, 0))
        self.data_dir_var = tk.StringVar(value=str(appdata.data_dir()))
        ttk.Entry(data_box, textvariable=self.data_dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(data_box, text="Browse…", command=self.choose_data_dir, width=10
                   ).pack(side="left", padx=6)
        ttk.Button(data_box, text="Apply", command=self.apply_data_dir, width=8
                   ).pack(side="left")
        ttk.Label(data_box, text="Snippets, mods, docs and config.json live here — never in the repo.",
                  style="Sub.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Button(data_box, text="Open config.json", command=self.open_config_file,
                   width=18).pack(anchor="w", pady=(6, 0))

        keys_box = ttk.LabelFrame(tab, text="AI keys (required — there is no anonymous provider)",
                                  padding=8)
        keys_box.pack(fill="x", pady=(12, 0))
        self.provider_var = tk.StringVar(value=appdata.config().get("aiProvider", "auto"))
        ttk.Label(keys_box, text="Provider chain").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(keys_box, textvariable=self.provider_var, state="readonly", width=16,
                     values=("auto",) + appdata.PROVIDER_ORDER).grid(row=0, column=1, sticky="w")
        self.key_vars: dict = {}
        self.model_vars: dict = {}
        settings = appdata.config()
        for index, provider_id in enumerate(appdata.PROVIDER_ORDER):
            provider = appdata.PROVIDERS[provider_id]
            ttk.Label(keys_box, text=provider["label"]).grid(row=1 + index * 2, column=0, sticky="w")
            key_var = tk.StringVar(value=str(settings.get(provider["keyField"], "")))
            self.key_vars[provider_id] = key_var
            ttk.Entry(keys_box, textvariable=key_var, show="*", width=44
                      ).grid(row=1 + index * 2, column=1, columnspan=2, sticky="w")
            ttk.Label(keys_box, text=provider["keyHelp"], style="Sub.TLabel"
                      ).grid(row=2 + index * 2, column=1, columnspan=2, sticky="w")
        ttk.Label(keys_box, text="Model overrides (optional)", style="Sub.TLabel").grid(
            row=0, column=3, sticky="w", padx=(18, 8))
        for index, provider_id in enumerate(appdata.PROVIDER_ORDER):
            provider = appdata.PROVIDERS[provider_id]
            model_var = tk.StringVar(value=str(settings.get(provider["modelField"], "")))
            self.model_vars[provider_id] = model_var
            ttk.Label(keys_box, text=provider["label"], style="Sub.TLabel").grid(
                row=1 + index, column=3, sticky="w", padx=(18, 8))
            ttk.Entry(keys_box, textvariable=model_var, width=26).grid(
                row=1 + index, column=4, sticky="w")

        links_box = ttk.LabelFrame(tab, text="Links", padding=8)
        links_box.pack(fill="x", pady=(12, 0))
        self.bloxd_url_var = tk.StringVar(value=str(settings.get("bloxdUrl", appdata.LINKS["bloxd"])))
        self.docs_url_var = tk.StringVar(value=str(settings.get("docsUrl", appdata.LINKS["docs"])))
        self.debounce_var = tk.StringVar(value=str(settings.get("editorDebounceMs", 200)))
        ttk.Label(links_box, text="Bloxd.io URL").grid(row=0, column=0, sticky="w")
        ttk.Entry(links_box, textvariable=self.bloxd_url_var, width=54).grid(row=0, column=1, sticky="w")
        ttk.Label(links_box, text="Docs URL").grid(row=1, column=0, sticky="w")
        ttk.Entry(links_box, textvariable=self.docs_url_var, width=54).grid(row=1, column=1, sticky="w")
        ttk.Label(links_box, text="Editor debounce (ms)").grid(row=2, column=0, sticky="w")
        ttk.Entry(links_box, textvariable=self.debounce_var, width=12).grid(row=2, column=1, sticky="w")

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="Save settings", command=self.save_settings).pack(side="left")
        ttk.Button(row, text="Reveal data folder", command=lambda: self.reveal(appdata.data_dir())
                   ).pack(side="left", padx=8)
        ttk.Label(tab, text="config.json is git-ignored and lives in the data folder — keys never "
                            "touch the repository.", style="Sub.TLabel", wraplength=900,
                  justify="left").pack(anchor="w", pady=(10, 0))
        return tab

    # -- helpers ------------------------------------------------------------ #
    def note(self, message: str) -> None:
        self.status.set(message)

    def reveal(self, path) -> None:
        target = Path(path)
        if not target.exists():
            messagebox.showinfo("Not found", str(target))
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as err:
            self.note(f"Could not open {target}: {err}")

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text or "")
        self.update_idletasks()
        self.note("Copied to the clipboard.")

    def get_editor_source(self) -> str:
        return self.lab.get_source()

    def on_snippets_changed(self) -> None:
        entries = appdata.list_snippets()
        self.snippet_list.delete(0, "end")
        for entry in entries:
            self.snippet_list.insert("end", f"{entry['name']}  ({appdata.human_bytes(entry['bytes'])})")
        if entries and not self.snippet_name.get().strip():
            self.snippet_name.set(entries[-1]["name"])
        self._refresh_home()

    def _selected_snippet(self):
        selection = self.snippet_list.curselection()
        entries = appdata.list_snippets()
        if not selection:
            return None
        index = selection[0]
        return entries[index] if 0 <= index < len(entries) else None

    def new_snippet(self) -> None:
        self.snippet_name.set("")
        self.lab.send_code("", "")
        self.note("New snippet — type in the Code Lab, then Save.")

    def save_snippet_via_tab(self) -> None:
        source = self.lab.get_source()
        if not source:
            messagebox.showinfo("Save", "Open the Code Lab window first — the code lives there.")
            return
        name = self.snippet_name.get().strip()
        if not name:
            messagebox.showinfo("Save", "Type a snippet name in the box below the list first.")
            return
        path = appdata.write_snippet(name, source)
        self.on_snippets_changed()
        self.note(f"Saved {appdata.relative_to_data(path)}")

    def delete_snippet_via_tab(self) -> None:
        entry = self._selected_snippet()
        if not entry:
            return
        if not messagebox.askyesno("Delete", f"Delete snippet '{entry['name']}'?"):
            return
        appdata.delete_snippet(entry["name"])
        self.on_snippets_changed()
        self.note(f"Deleted {entry['name']}")

    def copy_snippet_via_tab(self) -> None:
        entry = self._selected_snippet()
        if not entry:
            return
        try:
            self.copy_to_clipboard(appdata.read_snippet(entry["name"]))
        except OSError as err:
            messagebox.showerror("Copy", str(err))

    def insert_into_lab(self, source: str, name: str) -> str:
        path = appdata.write_snippet(name, source)
        self.on_snippets_changed()
        self.snippet_name.set(Path(path).stem)
        self.lab.open(source=source, name=Path(path).stem)
        return path

    def ask_ai_about(self, source: str = "") -> None:
        self.notebook.select(self.ai_tab)
        if self.ai:
            self.ai.ask_about_code(source or self.lab.get_source())

    def open_code_lab(self) -> None:
        self.notebook.select(self.lab_tab)
        self.lab.open()

    def on_lab_opened(self) -> None:
        self.lab_status.configure(text="Code Lab window: open")
        self.note("Code Lab window opened — edit and save with the buttons inside it.")
        self._poll_report()

    def on_lab_closed(self) -> None:
        self.lab_status.configure(text="Code Lab window: closed")
        self.note("Code Lab window closed.")

    def _poll_report(self) -> None:
        api = getattr(self.lab, "api", None)
        report = getattr(api, "last_report", None) if api else None
        if report and report.get("stats"):
            stats = report["stats"]
            calls = ", ".join(stats.get("apiCalls") or []) or "none"
            self.lab_stats.configure(
                text=f"trainReport — ok={report.get('ok')} · {stats.get('chars', 0)} chars · "
                     f"{stats.get('lines', 0)} lines · api calls: {calls}")
        if self.lab.is_open():
            self.after(1500, self._poll_report)

    def play_game(self) -> None:
        url = str(self.bloxd_url_var.get() or appdata.LINKS["bloxd"]).strip() or appdata.LINKS["bloxd"]
        if not game_module.available():
            if messagebox.askyesno("pywebview missing",
                                   "pywebview is not installed. Open Bloxd.io in your browser instead?"):
                webbrowser.open(url)
            return
        # The game window gets its own process so the hub keeps repainting and the
        # Code Lab webview session stays untouched.
        try:
            subprocess.Popen([sys.executable, str(appdata.HOME / "game.py"), url])
            self.note(f"Launching Bloxd.io in a player window ({url})")
        except Exception as err:
            self.note(f"Could not launch the game window: {err}")
            webbrowser.open(url)

    def install_mod(self) -> None:
        archive = filedialog.askopenfilename(
            parent=self, title="Install a mod (.zip)",
            filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")])
        if not archive:
            return
        result = appdata.install_mod(archive)
        self.refresh_mods()
        (messagebox.showinfo if result["ok"] else messagebox.showerror)(
            "Install mod", result["message"])
        self.note(result["message"])

    def refresh_mods(self) -> None:
        for item in self.mod_tree.get_children():
            self.mod_tree.delete(item)
        for entry in appdata.list_mods():
            self.mod_tree.insert(
                "", "end", iid=entry["path"],
                values=(entry["name"], appdata.human_bytes(entry["bytes"]),
                        appdata.iso_time(entry["modified"]),
                        "installed folder" if entry.get("isFolder")
                        else "zip archive" if entry["isZip"] else "mod file"))
        self._refresh_home()

    def delete_mod(self) -> None:
        selection = self.mod_tree.selection()
        if not selection:
            return
        path = selection[0]
        if not messagebox.askyesno("Delete", f"Delete {Path(path).name}?"):
            return
        target = Path(path)
        try:
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as err:
            messagebox.showerror("Delete", str(err))
        self.refresh_mods()

    def open_docs_site(self) -> None:
        webbrowser.open(str(self.docs_url_var.get() or appdata.LINKS["docs"]).strip()
                        or appdata.LINKS["docs"])

    def add_doc(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Add a markdown file to your local docs",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")])
        if not path:
            return
        target = appdata.docs_dir() / Path(path).name
        try:
            target.write_text(Path(path).read_text(encoding="utf-8", errors="replace"),
                              encoding="utf-8")
        except OSError as err:
            messagebox.showerror("Docs", str(err))
            return
        self._load_docs_index()

    def _load_docs_index(self) -> None:
        self.doc_files = []
        docs_root = appdata.docs_dir()
        docs_root.mkdir(parents=True, exist_ok=True)
        for folder in (docs_root, appdata.HOME / "templates"):
            for item in sorted(folder.glob("*.md")):
                self.doc_files.append(item)
        for item in (appdata.HOME / "README.md", appdata.HOME / "CHANGELOG.md"):
            if item.is_file():
                self.doc_files.append(item)
        self.doc_list.delete(0, "end")
        for item in self.doc_files:
            self.doc_list.insert("end", str(appdata.relative_to_data(item)))
        if self.doc_files:
            self.doc_list.selection_set(0)
            self._render_doc(self.doc_files[0])
        self._load_changelog()

    def _on_doc_select(self, _event=None) -> None:
        selection = self.doc_list.curselection()
        if selection:
            self._render_doc(self.doc_files[selection[0]])

    def _render_doc(self, path: Path) -> None:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as err:
            raw = f"# Cannot read\n\n{err}"
        self._fill_blocks(self.doc_text, markdown_blocks(raw))

    def _load_changelog(self) -> None:
        path = appdata.HOME / "CHANGELOG.md"
        raw = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else \
            "# Changelog\n\nNo CHANGELOG.md in the launcher folder yet."
        self._fill_blocks(self.log_text, markdown_blocks(raw))

    @staticmethod
    def _fill_blocks(widget: tk.Text, blocks: list) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for kind, text in blocks:
            clean = html_lib.unescape(str(text)).strip()
            if not clean:
                continue
            if kind.startswith("h"):
                level = min(int(kind[1:] or 1), 4)
                widget.insert("end", clean + "\n", f"h{level}")
            elif kind == "code":
                widget.insert("end", clean + "\n", "code")
            elif kind == "list":
                widget.insert("end", "• " + clean.lstrip("• ") + "\n", "list")
            else:
                widget.insert("end", clean + "\n", "para")
        widget.configure(state="disabled")

    # -- settings ---------------------------------------------------------- #
    def _load_settings(self) -> None:
        self.refresh_mods()
        self.on_snippets_changed()
        if self.lab_status is not None:
            self.lab_status.configure(text="Code Lab window: closed")

    def choose_data_dir(self) -> None:
        chosen = filedialog.askdirectory(parent=self, title="Choose the launcher data folder",
                                         initialdir=self.data_dir_var.get() or str(appdata.HOME))
        if chosen:
            self.data_dir_var.set(chosen)

    def apply_data_dir(self) -> None:
        target = self.data_dir_var.get().strip()
        if not target:
            return
        if not messagebox.askyesno("Data folder",
                                   f"Move the launcher data to\n{target}\n\nContinue?"):
            return
        try:
            appdata.set_data_dir(target)
        except OSError as err:
            messagebox.showerror("Data folder", str(err))
            return
        messagebox.showinfo("Data folder",
                            "Saved. Restart the launcher to use the new folder everywhere.")
        self.note(f"Data folder is now {appdata.data_dir()}")

    def save_settings(self) -> None:
        patch = {
            "aiProvider": self.provider_var.get(),
            "bloxdUrl": self.bloxd_url_var.get().strip() or appdata.LINKS["bloxd"],
            "docsUrl": self.docs_url_var.get().strip() or appdata.LINKS["docs"],
        }
        try:
            patch["editorDebounceMs"] = max(50, int(str(self.debounce_var.get()).strip() or 200))
        except ValueError:
            patch["editorDebounceMs"] = 200
        for provider_id, provider in appdata.PROVIDERS.items():
            patch[provider["keyField"]] = self.key_vars[provider_id].get().strip()
            patch[provider["modelField"]] = self.model_vars[provider_id].get().strip()
        appdata.write_config(patch)
        if self.ai:
            self.ai._load_settings()
        self.note(f"Saved to {appdata.config_path()}")
        self._refresh_home()

    def open_config_file(self) -> None:
        path = appdata.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            appdata.write_config({})
        self.reveal(path)

    def _refresh_home(self) -> None:
        snapshot = appdata.stats()
        pretty = {
            "version": f"v{appdata.VERSION} (Players Launcher)",
            "engine": f"v{snapshot['engine']} — vendored, {snapshot['vendorEngine']} "
                      f"({appdata.VENDOR_ENGINE})",
            "dataDir": snapshot["dataDir"],
            "snippets": f"{snapshot['snippets']} file(s), {appdata.human_bytes(snapshot['snippetBytes'])}",
            "mods": f"{snapshot['mods']} item(s) ({snapshot['modZips']} zip archive(s))",
            "templates": f"{snapshot['templates']} in templates/",
            "vocabCache": snapshot["vocabCache"],
            "python": snapshot["python"],
            "tk": snapshot["tk"],
            "pywebview": snapshot["pywebview"],
            "requests": snapshot["requests"],
        }
        for key, value in pretty.items():
            label = self.stats_labels.get(key)
            if label is not None:
                label.configure(text=value)

    def _refresh_stats_labels(self) -> None:
        self._refresh_home()

    def show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"{appdata.APP_TITLE}\nversion {appdata.VERSION}\n\n"
            f"Bloxd Editor engine v{appdata.engine_version()} (vendored, frozen contract 1.0.0)\n"
            f"Data folder: {appdata.data_dir()}\n\n"
            "Owner: FallenNightA\nOfficial: github.com/HidayatBelajar319\n"
            "Bloxd Editor engine: Bloxdy/code-api vocabulary, MIT-style vendored copy.")

    def on_close(self) -> None:
        try:
            if self.ai and self.ai.busy:
                pass
        finally:
            self.destroy()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Players — Bloxd Utility Launcher")
    parser.add_argument("--game", action="store_true", help="open the Bloxd.io player window and exit")
    parser.add_argument("--data-dir", default="", help="override the data directory for this run")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    args = parser.parse_args(argv)

    if args.data_dir:
        import os
        os.environ["BLOXD_LAUNCHER_HOME"] = str(Path(args.data_dir).expanduser())

    if args.version:
        print(f"{appdata.APP_TITLE} {appdata.VERSION} (editor engine v{appdata.engine_version()})")
        return 0
    if args.game:
        return game_module.main()

    app = Launcher()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
