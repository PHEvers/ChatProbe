"""Editor de perfis de IA para a aba Configurações."""

import tkinter as tk
from dataclasses import replace
from queue import Empty, Queue
from threading import Thread
from tkinter import messagebox, ttk
from uuid import uuid4

from src.chatprobe.runner.consumption import ConsumptionLedger, ExecutionOptions, LocalQuotaError, quota_scopes
from src.chatprobe.storage.sqlite_store import RunStore
from src.chatprobe.provider_catalog import LEGACY_GROQ
from src.chatprobe.providers import PRESETS, ProviderError, ProviderProfile


class ProviderManager(tk.Toplevel):
    def __init__(self, parent, catalog, changed, database_path=None):
        super().__init__(parent)
        self.title("Provedores de IA")
        self.transient(parent)
        self.columnconfigure(1, weight=1)
        self.catalog = catalog
        self.changed = changed
        self.database_path = database_path
        self.current = None
        self.kind = "openai"
        self.results = Queue()
        self.busy = False
        self.connection_buttons = []
        self.selector = ttk.Combobox(self, state="readonly", width=47)
        self.selector.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=4)
        self.selector.bind("<<ComboboxSelected>>", self.select)
        self.preset = ttk.Combobox(self, state="readonly", values=tuple(PRESETS))
        self.preset.set("API personalizada")
        self.preset.grid(row=1, column=0, columnspan=2, sticky="ew", padx=8)
        self.preset.bind("<<ComboboxSelected>>", lambda event: self.new())

        self.fields = {}
        for row, (key, label) in enumerate((
            ("name", "Nome"), ("base_url", "URL base"), ("model", "Modelo"),
            ("temperature", "Temperatura opcional"), ("timeout", "Timeout em segundos"),
            ("key", "Nova chave de API"),
            ("quota", "Cota global (RPM RPD TPM TPD)"),
            ("model_quota", "Cota do modelo (RPM RPD TPM TPD)"),
            ("quota_scope", "Grupo de cota compartilhada"),
        ), 2):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=2)
            if key == "model":
                widget = ttk.Combobox(self)
            else:
                widget = ttk.Entry(self, show="*" if key == "key" else "")
            widget.grid(row=row, column=1, sticky="ew", padx=8, pady=2)
            self.fields[key] = widget
        self.buttons(11, (("Novo", self.new), ("Salvar", self.save), ("Duplicar", self.duplicate),
                         ("Excluir", self.remove), ("Definir padrão", self.default)))
        self.buttons(12, (("Salvar chave", self.save_key), ("Remover chave", self.remove_key),
                         ("Reutilizar chave Groq", self.reuse_groq_key)))
        self.buttons(13, (("Atualizar modelos", lambda: self.probe("models")),
                          ("Testar conexão", lambda: self.probe("connection")),
                          ("Testar geração (pode consumir créditos)", lambda: self.probe("generation"))),
                     connection=True)
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=580).grid(
            row=14, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        self.refresh(self.catalog.default)
        self.poll_timer = self.after(100, self.poll)

    def destroy(self):
        self.after_cancel(self.poll_timer)
        super().destroy()

    def buttons(self, row, actions, *, connection=False):
        bar = ttk.Frame(self)
        bar.grid(row=row, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        for text, command in actions:
            button = ttk.Button(bar, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            if connection:
                self.connection_buttons.append(button)

    def refresh(self, provider_id):
        self.ids = list(self.catalog.profiles)
        self.selector.configure(values=[
            f"{p.name} · {p.id[:12]}" + (" (padrão)" if p.id == self.catalog.default else "")
            for p in self.catalog.profiles.values()
        ])
        if provider_id in self.ids:
            self.selector.current(self.ids.index(provider_id))
            self.select()
        else:
            self.new()

    def fill(self, profile):
        self.kind = profile.kind
        self.fields["model"].configure(values=())
        for key, widget in self.fields.items():
            value = getattr(profile, key, None)
            widget.delete(0, "end")
            widget.insert(0, "" if value is None else str(value))

    def new(self):
        preset = self.preset.get()
        self.current = None
        self.selector.set("")
        profile = ProviderProfile(uuid4().hex, preset, *PRESETS[preset])
        self.fill(profile)
        self.describe_validation(profile)

    def select(self, event=None):
        index = self.selector.current()
        if index < 0:
            return
        self.current = self.ids[index]
        profile = self.catalog.profiles[self.current]
        self.fill(profile)
        self.describe_validation(profile)

    def describe_validation(self, profile):
        message = ("Adaptador experimental. O protocolo foi testado com respostas simuladas."
                   if profile.kind in {"gemini", "anthropic"} else "")
        self.status.set(message)

    def profile(self):
        values = {key: field.get().strip() for key, field in self.fields.items() if key != "key"}
        try:
            timeout = float(values.pop("timeout"))
            temperature = values.pop("temperature")
            temperature = float(temperature) if temperature else None
        except ValueError:
            raise ValueError("Informe valores numéricos para tempo limite e temperatura.") from None
        return ProviderProfile(self.current or uuid4().hex, kind=self.kind,
                               timeout=timeout, temperature=temperature, **values).validate()

    def attempt(self, operation):
        try:
            return operation()
        except (ValueError, ProviderError) as error:
            self.status.set(str(error))
        except (OSError, UnicodeError):
            self.status.set("Falha ao acessar as configurações ou o armazenamento de credenciais.")

    def save(self):
        def operation():
            profile = self.profile()
            existing = self.catalog.profiles.get(profile.id)
            if existing and (profile.base_url != existing.base_url or profile.kind != existing.kind):
                if not messagebox.askyesno("Alterar destino", "Remover a chave do perfil antes de alterar o destino?",
                                          parent=self):
                    return
                self.catalog.remove_key(profile.id)
            self.catalog.upsert(profile)
            # Preserve a freshly typed key for the separate masked save action.
            candidate = self.fields["key"].get()
            self.refresh(profile.id)
            self.fields["key"].insert(0, candidate)
            self.changed()
            self.status.set("Provedor salvo.")
        self.attempt(operation)

    def duplicate(self):
        if self.current:
            profile = self.attempt(lambda: self.catalog.duplicate(self.catalog.profiles[self.current]))
            if profile:
                self.refresh(profile.id)
                self.changed()
                self.status.set("Configuração duplicada. Cadastre uma chave para a cópia, se necessário.")

    def remove(self):
        if self.current and messagebox.askyesno("Excluir provedor", "Excluir este perfil e sua chave?",
                                                parent=self):
            def operation():
                self.catalog.remove(self.current)
                self.refresh(self.catalog.default)
                self.changed()
            self.attempt(operation)

    def default(self):
        if self.current:
            def operation():
                self.catalog.set_default(self.current)
                self.refresh(self.current)
                self.changed()
            self.attempt(operation)

    def save_key(self):
        if not self.current:
            self.status.set("Salve o perfil antes de cadastrar a chave.")
            return

        def operation():
            profile = self.catalog.profiles[self.current]
            self.catalog.set_key(profile, self.fields["key"].get().strip())
            self.fields["key"].delete(0, "end")
            self.status.set("Chave salva no Windows." if self.catalog.credentials else "Chave válida nesta sessão.")
        self.attempt(operation)

    def reuse_groq_key(self):
        def operation():
            profile = self.catalog.profiles.get(self.current)
            if not self.catalog.credentials or not profile or profile.id != LEGACY_GROQ or (
                profile.kind, profile.base_url.rstrip("/")
            ) != PRESETS["Groq"]:
                raise ValueError("Selecione o perfil Groq original para reutilizar uma chave do Windows.")
            names = self.catalog.credentials.list_names("groq")
            if not names:
                raise ValueError("Nenhuma chave Groq existente.")
            dialog = tk.Toplevel(self)
            dialog.title("Selecionar chave Groq")
            dialog.transient(self)
            choice = ttk.Combobox(dialog, state="readonly", values=names)
            choice.current(0)
            choice.pack(padx=8, pady=8)

            def use():
                def persist():
                    key = self.catalog.credentials.get("groq", choice.get())
                    if not key:
                        raise ValueError("A chave selecionada não está mais disponível.")
                    self.catalog.set_key(profile, key)
                    self.status.set("Chave associada ao perfil Groq.")
                    dialog.destroy()
                self.attempt(persist)
            ttk.Button(dialog, text="Usar chave", command=use).pack(pady=(0, 8))
            dialog.grab_set()
        self.attempt(operation)

    def remove_key(self):
        if self.current:
            def operation():
                self.catalog.remove_key(self.current)
                self.fields["key"].delete(0, "end")
                self.status.set("Chave do perfil removida.")
            self.attempt(operation)

    def probe(self, action):
        if self.busy:
            return

        def resolve():
            profile = self.profile()
            existing = self.catalog.profiles.get(profile.id)
            if existing is None:
                raise ValueError("Salve o perfil antes de testar a conexão ou listar modelos.")
            if (existing.base_url, existing.kind) != (profile.base_url, profile.kind):
                raise ValueError("Salve a alteração de destino antes de testar o perfil.")
            return self.catalog.resolve(profile)

        provider = self.attempt(resolve)
        if provider is None:
            return
        self.busy = True
        for button in self.connection_buttons:
            button.state(["disabled"])
        self.status.set("Consultando o provedor…")

        profiles = tuple(self.catalog.profiles.values())

        def worker():
            try:
                if action == "generation":
                    profile = provider.profile
                    scopes = quota_scopes(profile, profile.metadata(), "", ExecutionOptions(), profiles)
                    if self.database_path is None:
                        raise LocalQuotaError("O SQLite é necessário para reservar o consumo deste teste.")
                    ledger = ConsumptionLedger(RunStore(self.database_path))
                    value = provider.test_generation(ledger=ledger, scopes=scopes)
                elif action == "models":
                    value = provider.list_models()
                else:
                    value = provider.check_connection()
                self.results.put((provider.profile, action, value, ""))
            except (ProviderError, LocalQuotaError) as error:
                self.results.put((provider.profile, action, None, str(error)))
            except Exception:
                self.results.put((provider.profile, action, None, "Falha inesperada ao consultar o provedor."))

        Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            profile, action, value, error = self.results.get_nowait()
        except Empty:
            pass
        else:
            self.busy = False
            for button in self.connection_buttons:
                button.state(["!disabled"])
            if error:
                self.status.set(error)
            elif action == "generation":
                self.status.set("Geração bem-sucedida.")
            else:
                if profile.id == self.current:
                    self.fields["model"].configure(values=value)
                self.status.set(f"Conexão bem-sucedida. {len(value)} modelo(s) disponível(is).")
        self.poll_timer = self.after(100, self.poll)
