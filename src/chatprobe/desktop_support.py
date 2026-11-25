"""Central de ajuda, diagnóstico e operações de backup."""

import sqlite3
import tkinter as tk
import webbrowser
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4
from zipfile import BadZipFile

from src.chatprobe.browser.version import RESOURCE_ROOT
from src.chatprobe.desktop_prompt import PromptEditor
from src.chatprobe.distribution import create_backup, diagnostic, restore_backup


class SupportView(ttk.Frame):
    def __init__(self, parent, app, database):
        super().__init__(parent)
        self.app, self.database = app, database
        self.status = tk.StringVar()
        self.busy = False
        for title, command in (("Primeiros passos", self.guide), ("Diagnóstico", self.check),
                               ("Criar backup...", self.backup), ("Restaurar backup...", self.restore),
                               ("Versões no GitHub", self.releases)):
            ttk.Button(self, text=title, command=command).pack(anchor="w", pady=3)
        ttk.Label(self, textvariable=self.status, wraplength=550).pack(anchor="w")

    def run(self, operation, done):
        if self.busy:
            return
        self.busy = True
        out = Queue()
        self.status.set("Processando...")
        def work():
            try:
                out.put((True, operation()))
            except (OSError, ValueError, TypeError, sqlite3.Error, BadZipFile):
                out.put((False, None))
        def finish():
            try:
                success, value = out.get_nowait()
            except Empty:
                self.after(80, finish)
                return
            self.busy = False
            if success:
                done(value)
            else:
                self.app.restoring = False
                self.status.set("Operação não concluída. Confira o arquivo, o espaço livre e as permissões.")
        Thread(target=work, daemon=True).start()
        self.after(80, finish)

    def text(self, title, content):
        popup = tk.Toplevel(self)
        popup.title(title)
        popup.geometry("700x480")
        editor = PromptEditor(popup, title=False, readonly=True)
        editor.pack(fill="both", expand=True, padx=10, pady=10)
        editor.set_value(content)
        from src.chatprobe.desktop_theme import PALETTES
        editor.apply_palette(PALETTES[self.app.theme.style.theme_use()])
        def copy():
            popup.clipboard_clear()
            popup.clipboard_append(content)
        ttk.Button(popup, text="Copiar", command=copy).pack(anchor="e", padx=10, pady=8)

    def guide(self):
        self.text("Ajuda do ChatProbe", (RESOURCE_ROOT / "docs/fase-7-distribuicao.md").read_text(encoding="utf-8"))

    def check(self):
        self.run(lambda: diagnostic(self.database), lambda value: self.text("Diagnóstico sanitizado", value))

    def backup(self):
        if self.busy:
            return
        path = filedialog.asksaveasfilename(parent=self, initialfile=f"chatprobe-backup-{uuid4().hex[:8]}.zip",
                                            defaultextension=".zip")
        if path:
            self.run(lambda: create_backup(self.database, Path(path)),
                     lambda _: self.status.set("Backup salvo."))

    def restore(self):
        if self.busy or self.app.running:
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[("Backup ZIP", "*.zip")])
        if not path or not messagebox.askyesno("Restaurar", "Substituir os dados locais e fechar o ChatProbe?",
                                              parent=self):
            return
        safety = self.database.with_name(f"backup-seguranca-{uuid4().hex}.zip")
        self.app.restoring = True
        def work():
            restore_backup(self.database, Path(path), safety)
        def done(_):
            messagebox.showinfo("Backup restaurado", "Reabra o ChatProbe e recadastre as chaves se necessário.",
                                parent=self)
            self.app.root.destroy()
        self.run(work, done)

    @staticmethod
    def releases():
        webbrowser.open("https://github.com/PHEvers/ChatProbe/releases")
