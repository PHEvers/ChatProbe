"""Editor de prompts reutilizável, com cópia fiel e edição expandida."""

import tkinter as tk
from tkinter import ttk


def token_estimate(text):
    return (len(text.encode("utf-8")) + 3) // 4


class PromptEditor(ttk.Frame):
    def __init__(self, parent, *, title=True, expandable=True, rows=5):
        super().__init__(parent)
        self.palette = None
        self.ai_mode = False
        self.title_var = tk.StringVar()
        self.hint = ttk.Label(self, style="Warning.TLabel", wraplength=440)
        self.title_entry = None
        self.expander = None
        self.expanded = None
        if title:
            row = ttk.Frame(self)
            row.pack(fill="x", pady=(0, 5))
            ttk.Label(row, text="Título opcional").pack(side="left", padx=(0, 8))
            self.title_entry = ttk.Entry(row, textvariable=self.title_var, width=14)
            self.title_entry.pack(side="left", fill="x", expand=True)
            if expandable:
                self.expander = ttk.Button(row, text="Ampliar editor", command=self.expand)
                self.expander.pack(side="right", padx=(6, 0))
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        self.text = tk.Text(container, wrap="word", width=18, height=rows, padx=10, pady=8,
                            undo=True, maxundo=-1, relief="flat", font="TkDefaultFont")
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.text.yview)
        scrollbar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.configure(yscrollcommand=scrollbar.set)
        footer = ttk.Frame(self)
        footer.bind("<Configure>", self.wrap_stats)
        footer.pack(side="bottom", fill="x", pady=(5, 0), before=container)
        self.stats = ttk.Label(footer, style="Subtitle.TLabel")
        self.stats.pack(side="left")
        if expandable and not title:
            self.expander = ttk.Button(footer, text="Ampliar editor", command=self.expand)
            self.expander.pack(side="right")
        self.text.bind("<<Modified>>", self.refresh_stats)
        self.text.bind("<Control-a>", self.select_all)
        self.text.bind("<Control-Shift-e>", lambda event: self.expand() if self.expander else None)
        self.refresh_stats()

    def wrap_stats(self, event):
        reserved = self.expander.winfo_reqwidth() + 8 if self.expander and self.expander.master is event.widget else 0
        self.stats.configure(wraplength=max(65, event.width - reserved - 4))

    def select_all(self, event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def value(self):
        return self.text.get("1.0", "end-1c")

    def set_value(self, text, title=""):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.edit_reset()
        self.title_var.set(title)
        self.refresh_stats()

    def set_ai_mode(self, enabled):
        self.ai_mode = enabled
        self.refresh_stats()

    def refresh_stats(self, event=None):
        if event is not None and not self.text.edit_modified():
            return
        text = self.value()
        if self.text.edit_modified():
            self.text.edit_modified(False)
        estimate = token_estimate(text) if self.ai_mode else 0
        label = f"{len(text):,} caracteres".replace(",", ".")
        if self.ai_mode:
            label += f" · ~{estimate:,} tokens estimados".replace(",", ".")
        self.stats.configure(text=label)
        if estimate >= 8192:
            self.hint.configure(text="Prompt longo. Confira a janela de contexto do modelo selecionado.")
            self.hint.pack(side="bottom", anchor="w", pady=(3, 0), before=self.text.master)
        else:
            self.hint.pack_forget()

    def set_enabled(self, enabled):
        self.text.configure(state="normal" if enabled else "disabled")
        for widget in (self.expander, self.title_entry):
            if widget is not None:
                widget.state(["!disabled" if enabled else "disabled"])

    def apply_palette(self, palette):
        self.palette = palette
        self.text.configure(background=palette["field"], foreground=palette["text"],
                            insertbackground=palette["text"], selectbackground=palette["selected"],
                            selectforeground=palette["text"], highlightbackground=palette["border"],
                            highlightcolor=palette["accent"], highlightthickness=1)

    def expand(self):
        if self.text.cget("state") == "disabled":
            return
        parent = self.winfo_toplevel()
        popup = tk.Toplevel(parent)
        popup.title("Editor de prompt")
        self.expanded = popup
        popup.transient(parent)
        width = min(1000, parent.winfo_screenwidth() - 100)
        height = min(750, parent.winfo_screenheight() - 120)
        popup.geometry(f"{width}x{height}+32+32")
        enlarged = PromptEditor(popup, title=self.title_entry is not None, expandable=False)
        enlarged.pack(fill="both", expand=True, padx=14, pady=(14, 0))
        enlarged.set_value(self.value(), self.title_var.get())
        enlarged.set_ai_mode(self.ai_mode)
        if self.palette:
            enlarged.apply_palette(self.palette)
            popup.configure(background=self.palette["canvas"])
        enlarged.text.mark_set("insert", self.text.index("insert"))
        enlarged.text.see("insert")

        def close(event=None):
            value, title = enlarged.value(), enlarged.title_var.get()
            if value != self.value():
                self.text.edit_separator()
                self.text.delete("1.0", "end")
                self.text.insert("1.0", value)
                self.text.edit_separator()
            self.title_var.set(title)
            self.text.mark_set("insert", enlarged.text.index("insert"))
            self.text.see("insert")
            self.expanded = None
            popup.destroy()
            self.text.focus_set()

        ttk.Button(popup, text="Voltar ao editor", style="Primary.TButton", command=close).pack(
            anchor="e", padx=14, pady=10)
        popup.bind("<Escape>", close)
        popup.protocol("WM_DELETE_WINDOW", close)
        popup.grab_set()
        enlarged.text.focus_set()
        return enlarged
