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
        for title, command in (("Assistente de configuração", lambda: setup_assistant(self.app)),
                               ("Primeiros passos", self.guide), ("Diagnóstico", self.check),
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


def setup_assistant(app):
    """Reaproveita os perfis vigentes e salva as preferências ao concluir."""
    from dataclasses import replace
    from src.chatprobe.chat_url import validate_chat_url

    dialog = tk.Toplevel(app.root)
    dialog.title("Configuração inicial")
    dialog.transient(app.root)
    dialog.geometry("600x340")
    pages = ttk.Notebook(dialog)
    pages.pack(fill="both", expand=True, padx=10, pady=10)
    connection, provider, finish = (ttk.Frame(pages, padding=12) for _ in range(3))
    for frame, title in ((connection, "Atendimento"), (provider, "Provedor"), (finish, "Primeiro teste")):
        pages.add(frame, text=title)
    name = tk.StringVar(value=app.customer_name_field.get())
    url = tk.StringVar(value=app.chat_url.get())
    mode = tk.StringVar(value=app.mode)
    for label, variable in (("URL do chatbot autorizado", url), ("Nome do cliente de teste", name)):
        ttk.Label(connection, text=label).pack(anchor="w", pady=(5, 2))
        ttk.Entry(connection, textvariable=variable).pack(fill="x")
    ttk.Label(provider, text="Modo de execução").pack(anchor="w")
    ttk.Combobox(provider, values=app.modes, textvariable=mode, state="readonly").pack(anchor="w")
    ttk.Button(provider, text="Configurar modelos e credenciais",
               command=app.open_provider_manager).pack(anchor="w", pady=6)
    ttk.Label(finish, wraplength=480,
              text="Na aba Teste, confira produto, perfil e prompt antes de iniciar. "
                   "Um Roteiro JSON pode rodar sem provedor de IA.").pack(anchor="w")
    bar = ttk.Frame(dialog)
    bar.pack(fill="x", padx=10, pady=(0, 10))
    def move(change):
        index = pages.index("current")
        pages.select(max(0, min(2, index + change)))
    def save():
        try:
            value = validate_chat_url(url.get())
            if not name.get().strip() or mode.get() not in app.modes:
                raise ValueError("Informe o nome e um modo válido.")
        except ValueError as error:
            messagebox.showerror("Configuração inicial", str(error), parent=dialog)
            return
        if app.update_preferences(replace(app.preferences, chat_url=value, customer_name=name.get().strip(),
                                          mode=mode.get())):
            app.chat_url.set(value)
            app.customer_name_field.delete(0, "end")
            app.customer_name_field.insert(0, name.get().strip())
            if app.mode != mode.get():
                app.switch_mode(mode.get())
            dialog.destroy()
    for label, command in (("Voltar", lambda: move(-1)), ("Avançar", lambda: move(1)),
                           ("Concluir", save), ("Mais tarde", dialog.destroy)):
        ttk.Button(bar, text=label, command=command).pack(side="left", padx=3)


def check_startup(app, database):
    result = Queue()
    def verify():
        problems = []
        if not (RESOURCE_ROOT / "docs/fase-7-distribuicao.md").is_file():
            problems.append("ajuda local ausente")
        summary = diagnostic(database)
        if "indisponível ou incompatível" in summary:
            problems.append("SQLite indisponível")
        if "Chromium · não encontrado" in summary:
            problems.append("Chromium ausente")
        result.put(problems)
    def poll():
        try:
            problems = result.get_nowait()
        except Empty:
            app.root.after(120, poll)
            return
        if problems:
            app.warning.set("Verificação inicial: " + ", ".join(problems) + ". Confira Configurações → Ajuda.")
    Thread(target=verify, daemon=True).start()
    app.root.after(120, poll)
