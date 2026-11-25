"""Cabeçalho, navegação e indicadores compartilhados da interface desktop."""

import tkinter as tk
from tkinter import ttk

from src.chatprobe.browser.version import VERSION


ICONS = {
    "play": ("#......", "###....", "#####..", "#######", "#####..", "###....", "#......"),
    "history": ("..#####..", ".#.....#.", "#...#...#", "#...###.#", "#.......#", ".#.....#.", "..#####.."),
    "queue": ("##.########", "...........", "##.########", "...........", "##.########"),
    "settings": ("..#.....#..", "###########", "..#.....#..", ".....#.....", "###########", ".....#....."),
}


class Navigation(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.header = ttk.Frame(self, padding=(10, 6))
        self.header.pack(fill="x", pady=(0, 12))
        company = ttk.Frame(self.header)
        company.pack(side="right", padx=(12, 4))
        ttk.Label(company, text="ChatProbe", style="Brand.TLabel").pack(anchor="e")
        ttk.Label(company, text=f"v{VERSION}", style="Version.TLabel").pack(anchor="e", pady=(2, 0))
        self.buttons = ttk.Frame(self.header)
        self.buttons.pack(side="left", fill="y")
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(0, weight=1)
        self.pages = {}
        self.icons = []
        self.current = None
        self.on_select = None

    def add(self, name, icon):
        page = ttk.Frame(self.body)
        picture = self.make_icon(icon)
        self.icons.append(picture)
        button = ttk.Button(self.buttons, text=name, image=picture, compound="top", style="Navigation.TButton",
                            command=lambda: self.select(page))
        button.pack(side="left", padx=(0, 4), fill="y")
        self.pages[page] = button
        key = len(self.pages)
        self.winfo_toplevel().bind(f"<Alt-Key-{key}>", lambda event: self.select(page))
        page.grid(row=0, column=0, sticky="nsew")
        if self.current is None:
            self.select(page)
        else:
            page.grid_remove()
        return page

    def make_icon(self, name):
        mask = ICONS[name]
        picture = tk.PhotoImage(master=self, width=len(mask[0]) * 2, height=len(mask) * 2)
        for y, row in enumerate(mask):
            for x, pixel in enumerate(row):
                if pixel == "#":
                    picture.put("#7295dc", to=(x * 2, y * 2, x * 2 + 2, y * 2 + 2))
        return picture

    def select(self, page=None):
        if page is None:
            return self.current
        if self.current is not None:
            self.current.grid_remove()
            self.pages[self.current].state(["!selected"])
        page.grid()
        self.pages[page].state(["selected"])
        self.current = page
        if self.on_select is not None:
            self.on_select(page)


class ScrollPanel(ttk.Frame):
    """Painel de formulários com rolagem quando a janela fica baixa."""

    def __init__(self, parent):
        super().__init__(parent)
        style = ttk.Style(self)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0,
                                background=style.lookup("TFrame", "background"))
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.content = ttk.Frame(self.canvas, padding=(6, 6, 14, 10))
        window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=min(980, event.width)))
        self.content.bind("<Configure>", self.resize)
        self.bind("<<ThemeChanged>>", self.update_theme)

    def resize(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def update_theme(self, event=None):
        self.canvas.configure(background=ttk.Style(self).lookup("TFrame", "background"))


class ExecutionPanel(ttk.Frame):
    def __init__(self, parent, status, run_status, warning, metrics):
        super().__init__(parent, style="Panel.TFrame", padding=(12, 9))
        self.state = tk.StringVar(value="Pronto")
        self.state_label = ttk.Label(self, textvariable=self.state, style="State.TLabel")
        self.state_label.grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100, length=150)
        self.progress.grid(row=0, column=2, sticky="e")
        self.percent = ttk.Label(self, text="0%", style="PanelMuted.TLabel", width=5, anchor="e")
        self.percent.grid(row=0, column=3, sticky="e")
        self.detail = ttk.Label(self, textvariable=status, style="Panel.TLabel", wraplength=620)
        self.detail.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self.labels = [self.detail]
        for variable, style in ((run_status, "PanelMuted.TLabel"), (warning, "PanelWarning.TLabel"),
                                (metrics, "PanelMuted.TLabel")):
            label = ttk.Label(self, textvariable=variable, style=style)
            label.grid(row=len(self.labels) + 1, column=0, columnspan=4, sticky="ew")
            variable.trace_add("write", lambda *args, label=label, variable=variable:
                               self.show_if_text(label, variable))
            self.show_if_text(label, variable)
            self.labels.append(label)
        self.columnconfigure(1, weight=1)
        self.bind("<Configure>", self.wrap)

    @staticmethod
    def show_if_text(label, variable):
        if variable.get():
            label.grid()
        else:
            label.grid_remove()

    def wrap(self, event):
        for label in self.labels:
            label.configure(wraplength=max(100, event.width - 24))

    def set_state(self, value, tone="neutral"):
        self.state.set(value)
        self.state_label.configure(style={"neutral": "State.TLabel", "success": "Success.State.TLabel",
                                          "warning": "Warning.State.TLabel", "error": "Error.State.TLabel",
                                          "active": "Active.State.TLabel"}[tone])

    def set_progress(self, ended, total):
        percent = round(100 * ended / total) if total else 0
        self.progress.configure(value=percent)
        self.percent.configure(text=f"{percent}%")
