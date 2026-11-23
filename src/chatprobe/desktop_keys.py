"""Aba de gerenciamento de chaves do aplicativo Windows."""

import tkinter as tk
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk

from src.chatprobe import groq_api


PROVIDERS = {"Groq": "groq", "OpenAI": "openai", "Anthropic": "anthropic", "Google Gemini": "gemini"}


class KeysTab:
    def __init__(self, tab, credentials, on_change):
        self.tab = tab
        self.credentials = credentials
        self.on_change = on_change

        ttk.Label(tab, text="Provedor").pack(anchor="w")
        self.provider = ttk.Combobox(tab, values=tuple(PROVIDERS), state="readonly")
        self.provider.current(0)
        self.provider.pack(anchor="w", pady=(4, 12))
        self.provider.bind("<<ComboboxSelected>>", self.change_provider)

        saved = ttk.Labelframe(tab, text="Chaves salvas", padding=8)
        saved.pack(fill="x", pady=(0, 10))
        self.selected = ttk.Combobox(saved, state="readonly")
        self.selected.pack(side="left", fill="x", expand=True)
        self.selected.bind("<<ComboboxSelected>>", lambda event: self.status.set(""))
        self.test_button = ttk.Button(saved, text="Testar conexão", command=self.test)
        self.remove_button = ttk.Button(saved, text="Remover", command=self.remove)
        self.remove_button.pack(side="right", padx=(8, 0))

        form = ttk.Labelframe(tab, text="Salvar chave", padding=8)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Nome").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.name = ttk.Entry(form)
        self.name.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(form, text="Chave de API").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.key = ttk.Entry(form, show="*")
        self.key.grid(row=1, column=1, sticky="ew")
        self.save_button = ttk.Button(form, text="Salvar", command=self.save)
        self.save_button.grid(row=1, column=2, padx=(8, 0))

        self.status = tk.StringVar()
        ttk.Label(tab, textvariable=self.status, wraplength=480).pack(anchor="w", pady=(8, 0))
        self.change_provider()

    def change_provider(self, event=None):
        self.name.delete(0, "end")
        self.key.delete(0, "end")
        if self.provider.get() == "Groq":
            self.test_button.pack(side="right", padx=(8, 0))
        else:
            self.test_button.pack_forget()
        self.refresh()

    def refresh(self, preferred="", status=""):
        try:
            names = self.credentials.list_names(PROVIDERS[self.provider.get()])
        except OSError:
            names = []
            status = "Não foi possível consultar as chaves salvas no Windows."
        self.selected.configure(values=names)
        selected = next((name for name in names if name.casefold() == preferred.casefold()),
                        names[0] if names else "")
        self.selected.set(selected)
        state = ["!disabled"] if names else ["disabled"]
        for widget in (self.selected, self.remove_button, self.test_button):
            widget.state(state)
        self.status.set(status or ("" if names else "Nenhuma chave salva para este provedor."))

    def test(self):
        name = self.selected.get()
        try:
            key = self.credentials.get("groq", name)
        except (OSError, UnicodeError):
            self.status.set("Não foi possível ler a chave selecionada.")
            return
        if not key:
            self.refresh(status="A chave selecionada não está mais salva.")
            return

        controls = (self.provider, self.selected, self.save_button, self.remove_button, self.test_button)
        for control in controls:
            control.state(["disabled"])
        self.status.set("Validando a chave na Groq.")
        result = Queue()

        def poll_result():
            try:
                status = result.get_nowait()
            except Empty:
                self.tab.after(100, poll_result)
                return
            self.status.set(status)
            for control in controls:
                control.state(["!disabled"])

        def check_connection():
            result.put(groq_api.check_api_key(key))

        Thread(target=check_connection, daemon=True).start()
        self.tab.after(100, poll_result)

    def save(self):
        provider = PROVIDERS[self.provider.get()]
        name = self.name.get().strip()
        key = self.key.get().strip()
        if not key:
            self.status.set("Informe a chave de API para salvar.")
            return
        try:
            if self.credentials.exists(provider, name) and not messagebox.askyesno(
                "Substituir chave", f'Deseja substituir a chave "{name}"?', parent=self.tab,
            ):
                return
            self.credentials.save(provider, key, name)
        except ValueError as error:
            self.status.set(str(error))
        except OSError:
            self.status.set("O Windows não conseguiu salvar a chave.")
        else:
            self.name.delete(0, "end")
            self.key.delete(0, "end")
            self.refresh(preferred=name, status="Chave salva.")
            self.on_change()

    def remove(self):
        name = self.selected.get()
        if not messagebox.askyesno(
            "Remover chave", f'Deseja remover a chave "{name}" do ChatProbe?', parent=self.tab,
        ):
            return
        try:
            self.credentials.remove(PROVIDERS[self.provider.get()], name)
        except OSError:
            self.status.set("O Windows não conseguiu remover a chave.")
        else:
            self.refresh(status="Chave removida.")
            self.on_change()
