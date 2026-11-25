"""Janela nativa para executar roteiros do ChatProbe."""

import json
import os
import subprocess
import sqlite3
from datetime import datetime
from time import monotonic, time
from uuid import uuid4
import sys
import tkinter as tk
from dataclasses import asdict, replace
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

from src.chatprobe import groq_api, ollama_api
from src.chatprobe.browser import smoke
from src.chatprobe.browser.timing import ExecutionTiming
from src.chatprobe.chat_url import validate_chat_url
from src.chatprobe.desktop_conversation import ConversationView
from src.chatprobe.desktop_keys import KeysTab
from src.chatprobe.desktop_execution import ExecutionDialog
from src.chatprobe.desktop_night_queue import NightQueue
from src.chatprobe.desktop_providers import ProviderManager
from src.chatprobe.desktop_preferences import load_preferences, save_preferences, save_report, write_text_atomically
from src.chatprobe.desktop_theme import DesktopTheme
from src.chatprobe.provider_catalog import ProviderCatalog
from src.chatprobe.providers import Provider, ProviderError, ProviderProfile
from src.chatprobe.runner import batch
from src.chatprobe.runner.reports import TERMINATION_LABELS, execution_summary, legacy_conversation
from src.chatprobe.storage.sqlite_store import DEFAULT_DATABASE, RunStore
from src.chatprobe.storage.queues import QueueStore, scheduled_timestamp
from src.chatprobe.runner.consumption import ConsumptionLedger, ExecutionOptions
from src.chatprobe.windows_credentials import WindowsCredentials


RESOURCE_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
EXAMPLE = RESOURCE_ROOT / "examples/prompts/cupom-nao-impresso.json"
JSON_MODE = "Roteiro JSON"
GROQ_MODE = "Groq"
OLLAMA_MODE = "Ollama"
AI_MODE = "Provedor de IA"
DEFAULT_PROVIDER_LABEL = "Usar provedor padrão"
STATUS_LABELS = {
    "completed": "Concluído", "interrupted": "Teste interrompido",
    "error": "Erro de execução", "rate_limited": "Limite de API", "paused": "Pausado",
    "stopped": "Interrompido pelo usuário",
}


class ChatProbeWindow:
    def __init__(self, root):
        self.root = root
        self.events = Queue()
        self.running = False
        self.report = None
        self.saved_report_path = None
        self.results = {}
        self.total = 1
        self.active_prompts = None
        self.run_control = None
        self.pause_buttons = []
        self.stop_buttons = []
        self.credentials = WindowsCredentials() if sys.platform == "win32" else None
        self.modes = (GROQ_MODE, OLLAMA_MODE, JSON_MODE, AI_MODE) if self.credentials else (
            JSON_MODE, AI_MODE, OLLAMA_MODE,
        )
        self.preferences = load_preferences()
        try:
            self.execution_options = ExecutionOptions(**(self.preferences.execution or {}))
        except (TypeError, ValueError):
            self.execution_options = ExecutionOptions()
        self.queue_owner = uuid4().hex
        self.app_started = time()
        self.queue_id = None
        self.queue_store = None
        self.active_positions = set()
        self.quota_scope = None
        self.last_metrics = 0
        try:
            self.queue_store = QueueStore(RunStore(DEFAULT_DATABASE, timeout=0.1))
        except (OSError, sqlite3.Error):
            pass
        self.provider_fields = []
        self.provider_manager = None
        try:
            self.catalog = ProviderCatalog(self.credentials, legacy_ollama_model=self.preferences.ollama_model,
                                           legacy_mode=self.preferences.mode)
        except (OSError, ValueError, TypeError, KeyError):
            self.catalog = None
        self.mode = self.preferences.mode if self.preferences.mode in self.modes else self.modes[0]
        self.theme = DesktopTheme(root)
        self.drafts = {
            JSON_MODE: EXAMPLE.read_text(encoding="utf-8"),
            GROQ_MODE: (
                "Simule um cliente que finalizou uma venda, mas o cupom "
                "não saiu. Informe que já reiniciou a impressora se o chatbot perguntar. "
                "Encerre quando ele explicar como conferir a configuração da impressora."
            ),
        }
        self.drafts[OLLAMA_MODE] = self.drafts[AI_MODE] = self.drafts[GROQ_MODE]
        root.title("ChatProbe")
        root.geometry("1080x690")
        root.minsize(800, 640)
        root.protocol("WM_DELETE_WINDOW", self.close)

        content = ttk.Frame(root, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="ChatProbe", style="Title.TLabel").pack(anchor="w")
        ttk.Label(content, text="Chat de homologação", style="Subtitle.TLabel").pack(
            anchor="w", pady=(0, 14),
        )

        self.tabs = ttk.Notebook(content)
        self.tabs.pack(fill="both", expand=True)
        self.test_tab = ttk.Frame(self.tabs, padding=12)
        night_tab = ttk.Frame(self.tabs, padding=12)
        self.settings_tab = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(self.test_tab, text="Teste")
        self.tabs.add(night_tab, text="Fila de testes")
        self.tabs.add(self.settings_tab, text="Configurações")
        self.status = tk.StringVar(value="Pronto para executar.")
        self.warning = tk.StringVar()
        self.run_status = tk.StringVar()
        self.profile_summary = tk.StringVar()

        columns = ttk.Panedwindow(self.test_tab, orient="horizontal")
        execution = ttk.Frame(columns, padding=(0, 0, 8, 0))
        results = ttk.Labelframe(columns, text="Conversa", padding=10)
        columns.add(execution, weight=0)
        columns.add(results, weight=1)

        settings = ttk.Labelframe(execution, text="Ajustes do teste", padding=(10, 6))
        settings.pack(fill="x", pady=(0, 8))
        settings.columnconfigure(1, weight=1)
        self.test_provider = self.build_profile_link(settings)
        ttk.Label(settings, text="Quantidade de testes").grid(
            row=1, column=0, padx=(0, 10), pady=(5, 0), sticky="w",
        )
        self.total_field = ttk.Entry(settings, width=5)
        self.total_field.insert(0, "1")
        self.total_field.grid(row=1, column=1, pady=(5, 0), sticky="w")
        ttk.Label(settings, text="Testes simultâneos").grid(
            row=2, column=0, padx=(0, 10), pady=(5, 0), sticky="w",
        )
        self.parallel_field = ttk.Entry(settings, width=5)
        self.parallel_field.insert(0, "1")
        self.parallel_field.grid(row=2, column=1, pady=(5, 0), sticky="w")

        self.message_limit_label = ttk.Label(settings, text="Máx. mensagens do cliente")
        self.message_limit_field = ttk.Entry(settings, width=5)
        self.message_limit_field.insert(0, str(batch.DEFAULT_MAX_CUSTOMER_MESSAGES))

        ttk.Label(settings, text="Produto").grid(row=4, column=0, pady=(5, 0), sticky="w")
        self.product_field = ttk.Combobox(
            settings, values=smoke.PRODUCT_OPTIONS, state="readonly", width=20,
        )
        self.product_field.set(smoke.DEFAULT_PRODUCT)
        self.product_field.grid(row=4, column=1, pady=(5, 0), sticky="ew")

        self.editor = ttk.Labelframe(execution, text=JSON_MODE, padding=8)
        self.editor.pack(fill="both", expand=True)

        self.scenario = ScrolledText(
            self.editor, wrap="word", width=28, height=6, padx=8, pady=6, font=("Segoe UI", 10),
        )
        self.scenario.pack(fill="both", expand=True)
        self.scenario.insert("1.0", self.drafts[self.mode])

        result_header = ttk.Frame(results)
        result_header.pack(fill="x", pady=(0, 6))
        ttk.Label(result_header, text="Resultado").pack(side="left", padx=(0, 8))
        self.result_field = ttk.Combobox(result_header, state="disabled", width=11)
        self.result_field.pack(side="left")
        self.result_field.bind("<<ComboboxSelected>>", self.show_result)
        self.conversation = ConversationView(results)
        self.copy_button = ttk.Button(
            result_header, text="Copiar conversa", command=self.conversation.copy, state="disabled",
        )
        self.copy_button.pack(side="right", padx=(6, 0))
        self.result_status = tk.StringVar()
        ttk.Label(results, textvariable=self.result_status, style="Subtitle.TLabel").pack(
            anchor="w", pady=(0, 5),
        )
        self.conversation.pack(fill="both", expand=True)

        footer = ttk.Frame(self.test_tab)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        self.run_button = ttk.Button(actions, text="Executar teste", command=self.start,
                                     style="Primary.TButton")
        self.run_button.pack(side="left")
        self.build_run_buttons(actions)
        self.save_button = ttk.Button(actions, text="Salvar JSON", command=self.save,
                                      state="disabled")
        self.save_button.pack(side="left", padx=8)
        self.open_folder_button = ttk.Button(
            actions, text="Abrir pasta do relatório", command=self.open_report_folder, state="disabled",
        )
        self.open_folder_button.pack(side="left")
        ttk.Label(footer, textvariable=self.status, wraplength=540).pack(
            anchor="w", pady=(6, 0)
        )
        ttk.Label(footer, textvariable=self.run_status, style="Subtitle.TLabel").pack(anchor="w")
        self.warning_label = ttk.Label(footer, textvariable=self.warning, wraplength=540,
                                       style="Warning.TLabel")
        columns.pack(fill="both", expand=True)
        self.build_night_tab(night_tab)
        self.build_settings(self.settings_tab)
        self.refresh_providers()
        self.show_mode_fields()
        self.apply_theme()
        self.root.after(1000, self.queue_tick)

    def build_profile_link(self, parent):
        profile = ttk.Frame(parent)
        profile.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(profile, textvariable=self.profile_summary, style="Subtitle.TLabel", wraplength=200).pack(side="left")
        ttk.Button(profile, text="Configurações", command=lambda: self.tabs.select(self.settings_tab)).pack(
            side="right", padx=(8, 0),
        )
        field = ttk.Combobox(profile, state="readonly", width=23)
        self.provider_fields.append(field)
        return field

    def refresh_providers(self):
        profiles = self.catalog.profiles.values() if self.catalog else ()
        self.provider_labels = {f"{p.name} · {p.id}": p.id for p in profiles}
        choices = (DEFAULT_PROVIDER_LABEL, *self.provider_labels)
        for field in self.provider_fields:
            field.configure(values=choices)
            if field.get() not in choices:
                field.set(DEFAULT_PROVIDER_LABEL)
        if hasattr(self, "ollama_model"):
            self.update_profile_summary()

    def open_provider_manager(self):
        if self.catalog is None:
            messagebox.showerror("Provedores de IA", "Não foi possível carregar o catálogo local.",
                                 parent=self.root)
            return
        if self.provider_manager is None or not self.provider_manager.winfo_exists():
            self.provider_manager = ProviderManager(self.root, self.catalog, self.refresh_providers, DEFAULT_DATABASE)
        else:
            self.provider_manager.lift()

    def build_night_tab(self, tab):
        controls = ttk.Labelframe(tab, text="Ajustes do lote", padding=8)
        controls.pack(fill="x", pady=(0, 8))
        self.night_provider = self.build_profile_link(controls)
        controls.grid_columnconfigure(1, weight=1)
        parameters = ttk.Frame(controls)
        parameters.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(parameters, text="Produto").pack(side="left", padx=(0, 6))
        self.night_product = ttk.Combobox(parameters, values=smoke.PRODUCT_OPTIONS, state="readonly", width=19)
        self.night_product.set(smoke.DEFAULT_PRODUCT)
        self.night_product.pack(side="left")
        ttk.Label(parameters, text="Testes simultâneos").pack(side="left", padx=(12, 6))
        self.night_parallel = ttk.Entry(parameters, width=4)
        self.night_parallel.insert(0, "1")
        self.night_parallel.pack(side="left")
        self.night_limit_label = ttk.Label(parameters, text="Máx. mensagens do cliente")
        self.night_limit = ttk.Entry(parameters, width=4)
        self.night_limit.insert(0, str(batch.DEFAULT_MAX_CUSTOMER_MESSAGES))

        footer = ttk.Frame(tab)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        self.night_run_button = ttk.Button(actions, text="Executar fila", command=lambda: self.start(nightly=True),
                                          style="Primary.TButton")
        self.night_run_button.pack(side="left")
        ttk.Button(actions, text="Agendar…", command=self.schedule_queue).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Recuperar / cancelar…", command=self.manage_saved_queues).pack(side="left", padx=(6, 0))
        ttk.Button(controls, text="Controle de execução…", command=self.edit_execution).grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self.build_run_buttons(actions)
        self.night_view_button = ttk.Button(actions, text="Ver conversas",
                                           command=lambda: self.tabs.select(self.test_tab), state="disabled")
        self.night_view_button.pack(side="left", padx=8)
        self.metrics = tk.StringVar()
        ttk.Label(footer, textvariable=self.metrics, wraplength=800).pack(anchor="w")
        ttk.Label(footer, textvariable=self.status, wraplength=690).pack(anchor="w", pady=(6, 0))
        ttk.Label(footer, textvariable=self.run_status, style="Subtitle.TLabel").pack(anchor="w")
        ttk.Label(footer, textvariable=self.warning, wraplength=690, style="Warning.TLabel").pack(anchor="w")

        ttk.Label(tab, text="Um teste por prompt. A fila inicia ao clicar em Executar fila.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(0, 6))
        self.night_queue = NightQueue(tab, self.preferences.night_prompts, self.save_night_prompts, self.status.set)
        self.night_queue.pack(fill="both", expand=True)

    def save_night_prompts(self, prompts):
        return self.update_preferences(replace(self.preferences, night_prompts=prompts))

    def build_run_buttons(self, parent):
        pause = ttk.Button(parent, text="Pausar", command=self.toggle_pause, state="disabled")
        pause.pack(side="left", padx=(8, 0))
        self.pause_buttons.append(pause)
        stop = ttk.Button(parent, text="Parar", command=self.stop_batch, state="disabled")
        stop.pack(side="left", padx=(8, 0))
        self.stop_buttons.append(stop)

    def build_settings(self, tab):
        tab.columnconfigure((0, 1), weight=1, uniform="settings")
        options = ttk.Frame(tab)
        options.grid(row=0, column=0, sticky="new", padx=(0, 10))
        execution = ttk.Labelframe(options, text="Preferências de execução", padding=8)
        execution.pack(fill="x", pady=(0, 10))
        execution.columnconfigure(1, weight=1)
        ttk.Label(execution, text="Modo").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.mode_field = ttk.Combobox(execution, values=self.modes, state="readonly", width=18)
        self.mode_field.set(self.mode)
        self.mode_field.grid(row=0, column=1, sticky="ew")
        self.mode_field.bind("<<ComboboxSelected>>", self.change_mode)
        ttk.Label(execution, text="Nome do cliente").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.customer_name_field = ttk.Entry(execution, width=20)
        self.customer_name_field.insert(0, self.preferences.customer_name or smoke.DEFAULT_CUSTOMER_NAME)
        self.customer_name_field.grid(row=1, column=1, sticky="ew", pady=(5, 0))
        self.customer_name_field.bind("<FocusOut>", self.save_customer_name)
        self.customer_name_field.bind("<Return>", self.save_customer_name)

        self.groq_key_label = ttk.Label(execution, text="Chave Groq")
        self.groq_key = ttk.Combobox(execution, state="readonly", width=20, postcommand=self.refresh_test_keys)
        self.groq_notice = ttk.Label(
            execution, text=f"Modelo {groq_api.MODEL}. A Groq recebe o prompt e a conversa.",
            wraplength=300, style="Subtitle.TLabel",
        )
        self.ollama_model_label = ttk.Label(execution, text="Modelo Ollama")
        self.ollama_model = ttk.Combobox(execution, state="readonly", width=20,
                                       postcommand=self.refresh_ollama_models)
        self.ollama_model.set(self.preferences.ollama_model)
        self.ollama_model.bind("<<ComboboxSelected>>", self.save_ollama_model)
        self.ollama_message = tk.StringVar()
        self.ollama_notice = ttk.Label(execution, textvariable=self.ollama_message,
                                      wraplength=300, style="Subtitle.TLabel")

        ttk.Button(options, text="Controle de execução…", command=self.edit_execution).pack(anchor="w", pady=(0, 6))
        destination = ttk.Labelframe(options, text="URL do chat", padding=8)
        destination.pack(fill="x", pady=(0, 10))
        self.chat_url = tk.StringVar(value=self.preferences.chat_url)
        field = ttk.Entry(destination, textvariable=self.chat_url)
        field.pack(side="left", fill="x", expand=True)
        field.bind("<Return>", self.save_chat_url)
        ttk.Button(destination, text="Salvar URL", command=self.save_chat_url).pack(
            side="right", padx=(8, 0),
        )

        results = ttk.Labelframe(options, text="Pasta de resultados", padding=8)
        results.pack(fill="x", pady=(0, 10))
        self.results_folder = tk.StringVar(value=str(self.preferences.results_folder))
        ttk.Entry(results, textvariable=self.results_folder, state="readonly").pack(
            side="left", fill="x", expand=True,
        )
        ttk.Button(results, text="Escolher pasta…", command=self.choose_results_folder).pack(
            side="right", padx=(8, 0),
        )
        self.dark_mode = tk.BooleanVar(value=self.preferences.dark_mode)
        ttk.Checkbutton(options, text="Modo escuro", variable=self.dark_mode,
                        command=self.change_theme).pack(anchor="w", pady=(0, 10))

        management = ttk.Frame(tab)
        management.grid(row=0, column=1, sticky="new")
        providers = ttk.Labelframe(management, text="Provedores de IA", padding=8)
        providers.pack(fill="x", pady=(0, 10))
        ttk.Button(providers, text="Gerenciar provedores…", command=self.open_provider_manager).pack(anchor="w")
        keys_tab = ttk.Labelframe(management, text="Chaves de API existentes", padding=8)
        keys_tab.pack(fill="x")
        if self.credentials:
            self.keys_tab = KeysTab(keys_tab, self.credentials, self.refresh_test_keys)
        else:
            ttk.Label(keys_tab, text="Abra o ChatProbe no Windows para gerenciar suas chaves.").pack(
                anchor="w"
            )

    def save_chat_url(self, event=None):
        try:
            url = validate_chat_url(self.chat_url.get())
        except ValueError as error:
            messagebox.showerror("URL do chat", str(error), parent=self.root)
            return False
        if url != self.preferences.chat_url:
            if not self.update_preferences(replace(self.preferences, chat_url=url)):
                return False
        self.chat_url.set(url)
        return True

    def choose_results_folder(self):
        folder = self.preferences.results_folder
        selected = filedialog.askdirectory(
            parent=self.root, title="Pasta de resultados",
            initialdir=str(folder if folder.is_dir() else Path.home()), mustexist=True,
        )
        if selected and self.update_preferences(replace(self.preferences, results_folder=Path(selected))):
            self.results_folder.set(selected)

    def change_theme(self):
        if self.update_preferences(replace(self.preferences, dark_mode=self.dark_mode.get())):
            self.apply_theme()
        else:
            self.dark_mode.set(self.preferences.dark_mode)

    def apply_theme(self):
        palette = self.theme.apply(self.preferences.dark_mode, self.scenario, self.night_queue.editor)
        self.conversation.apply_palette(palette)

    def update_preferences(self, preferences):
        try:
            save_preferences(preferences)
        except OSError as error:
            messagebox.showerror("Configurações", f"Não foi possível salvar as configurações.\n{error}",
                                 parent=self.root)
            return False
        self.preferences = preferences
        return True

    def change_mode(self, event):
        mode = self.mode_field.get()
        if mode == self.mode:
            return
        if not self.update_preferences(replace(self.preferences, mode=mode)):
            self.mode_field.set(self.mode)
            return
        self.drafts[self.mode] = self.scenario.get("1.0", "end-1c")
        self.mode = mode
        self.scenario.delete("1.0", "end")
        self.scenario.insert("1.0", self.drafts[self.mode])
        self.show_mode_fields()

    def save_customer_name(self, event=None):
        name = self.customer_name_field.get().strip()
        if not name or name == (self.preferences.customer_name or smoke.DEFAULT_CUSTOMER_NAME):
            return True
        return self.update_preferences(replace(self.preferences, customer_name=name))

    def save_ollama_model(self, event=None):
        model = self.ollama_model.get()
        if model == self.preferences.ollama_model or self.update_preferences(replace(self.preferences, ollama_model=model)):
            self.update_profile_summary()
            self.ollama_message.set("Geração local, sem chave de API.")
            return True
        self.ollama_model.set(self.preferences.ollama_model)
        return False

    def update_profile_summary(self):
        model = self.ollama_model.get() if self.mode == OLLAMA_MODE else ""
        default = self.catalog.profiles.get(self.catalog.default) if self.catalog else None
        if self.mode == AI_MODE and default:
            self.profile_summary.set(f"Padrão · {default.name}")
        else:
            self.profile_summary.set(f"Ollama · {model}" if model else self.mode)

    def show_mode_fields(self):
        for widget in (self.groq_key_label, self.groq_key, self.groq_notice,
                       self.ollama_model_label, self.ollama_model, self.ollama_notice,
                       self.message_limit_label, self.message_limit_field):
            widget.grid_remove()
        for field in self.provider_fields:
            if self.mode == AI_MODE:
                field.pack(side="left", fill="x", expand=True, padx=(8, 0))
            else:
                field.pack_forget()
        input_label = JSON_MODE if self.mode == JSON_MODE else "Prompt inicial"
        self.editor.configure(text=input_label)
        self.night_queue.form.configure(text=input_label)
        if self.mode != JSON_MODE:
            self.message_limit_label.grid(row=3, column=0, padx=(0, 10), pady=(5, 0), sticky="w")
            self.message_limit_field.grid(row=3, column=1, pady=(5, 0), sticky="w")
            self.night_limit_label.pack(side="left", padx=(12, 6))
            self.night_limit.pack(side="left")
        else:
            self.night_limit_label.pack_forget()
            self.night_limit.pack_forget()
        if self.mode == GROQ_MODE:
            self.groq_key_label.grid(row=2, column=0, pady=(5, 0), sticky="w")
            self.groq_key.grid(row=2, column=1, pady=(5, 0), sticky="ew")
            self.groq_notice.grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="w")
            self.refresh_test_keys()
        elif self.mode == OLLAMA_MODE:
            self.ollama_model_label.grid(row=2, column=0, pady=(5, 0), sticky="w")
            self.ollama_model.grid(row=2, column=1, pady=(5, 0), sticky="ew")
            self.ollama_notice.grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="w")
            self.refresh_ollama_models()
        self.update_profile_summary()

    def refresh_ollama_models(self):
        try:
            models = ollama_api.list_models()
        except ollama_api.OllamaError as error:
            self.ollama_model.configure(values=())
            self.ollama_message.set(str(error))
            return False
        selected = self.ollama_model.get()
        self.ollama_model.configure(values=models)
        if not models:
            self.ollama_message.set("Nenhum modelo local disponível. Instale um com ollama pull qwen3:4b.")
            return False
        if selected and selected not in models:
            self.ollama_message.set("O modelo salvo não está instalado. Selecione um dos modelos disponíveis.")
            return False
        if not selected:
            self.ollama_model.set(models[0])
        self.ollama_message.set("Geração local, sem chave de API.")
        self.update_profile_summary()
        return True

    def refresh_test_keys(self):
        if self.mode != GROQ_MODE:
            return
        selected = self.groq_key.get()
        try:
            names = self.credentials.list_names("groq")
        except OSError:
            names = []
            self.status.set("Não foi possível consultar as chaves da Groq no Windows.")
        self.groq_key.configure(values=names)
        self.groq_key.set(selected if selected in names else names[0] if names else "")

    def start(self, nightly=False, scheduled=None):
        if self.running:
            return
        if nightly and not self.night_queue.commit_draft():
            return
        prompts = self.night_queue.prompts if nightly else None
        if nightly and not prompts:
            self.status.set("Adicione pelo menos um prompt à fila.")
            return
        try:
            total = len(prompts) if nightly else int(self.total_field.get())
            parallel = int((self.night_parallel if nightly else self.parallel_field).get())
            if not 1 <= parallel <= total:
                raise ValueError
        except ValueError:
            self.status.set("Informe pelo menos 1 teste e um número de simultâneos entre 1 e o total.")
            return

        customer_name = self.customer_name_field.get().strip()
        product = (self.night_product if nightly else self.product_field).get().strip()
        if not customer_name or product not in smoke.PRODUCT_OPTIONS:
            self.status.set("Preencha o Nome do cliente em Configurações e selecione um Produto.")
            return

        test_inputs = []
        texts = prompts if nightly else (self.scenario.get("1.0", "end-1c").strip(),)
        for number, text in enumerate(texts, 1):
            if self.mode == JSON_MODE:
                try:
                    test_inputs.append((smoke.parse_scenario(json.loads(text)), ""))
                except ValueError as error:
                    label = f"Roteiro {number}" if nightly else "Roteiro"
                    self.status.set(f"{label} inválido. {error}")
                    return
            elif not text:
                self.status.set("Descreva o objetivo do teste.")
                return
            else:
                test_inputs.append((None, text))

        try:
            options = self.provider_options(
                self.night_limit if nightly else self.message_limit_field,
                self.night_provider if nightly else self.test_provider,
            )
        except (ValueError, groq_api.GroqError, ProviderError) as error:
            self.status.set(str(error))
            return
        if not self.save_chat_url() or not self.save_customer_name():
            return

        snapshot = {"url": self.preferences.chat_url, "mode": self.mode, "parallel": parallel,
                    "product": product, "customer_name": customer_name,
                    "execution": asdict(self.execution_options),
                    "max_customer_messages": options["max_customer_messages"],
                    "ollama_model": options.get("ollama_model"),
                    "provider": asdict(options["ai_provider"].profile) if "ai_provider" in options else None}
        if nightly:
            if self.queue_store is None:
                self.status.set("O SQLite está indisponível para a fila persistente.")
                return
            try:
                identifier = self.queue_store.create(snapshot, prompts, scheduled)
            except (OSError, sqlite3.Error):
                self.status.set("Falha ao registrar a fila no SQLite.")
                return
            if scheduled is not None:
                self.status.set("Fila agendada. Mantenha o aplicativo aberto e o computador ligado.")
            else:
                self.launch_saved_queue(identifier, options)
            return
        self.launch_execution(snapshot, total, test_inputs, options)

    def edit_execution(self):
        dialog = ExecutionDialog(self.root, self.execution_options)
        if dialog.result is not None and self.update_preferences(
            replace(self.preferences, execution=asdict(dialog.result))
        ):
            self.execution_options = dialog.result

    def schedule_queue(self):
        if self.running:
            return
        value = simpledialog.askstring(
            "Agendar fila", "Data e hora no fuso do sistema (AAAA-MM-DD HH:MM).\n"
            "Opcionalmente inclua um deslocamento, por exemplo -03:00.\n"
            "Mantenha o aplicativo aberto e o computador ligado.", parent=self.root,
        )
        if value:
            try:
                scheduled = scheduled_timestamp(value.strip())
            except (ValueError, OverflowError, OSError):
                self.status.set("Informe uma data e hora futuras válidas.")
            else:
                self.start(nightly=True, scheduled=scheduled)

    def manage_saved_queues(self):
        if not self.queue_store:
            self.status.set("O SQLite está indisponível.")
            return
        try:
            self.queue_store.recover_stale()
            rows = self.queue_store.available()
        except sqlite3.Error:
            self.status.set("Falha ao consultar as filas salvas.")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Filas salvas")
        identifiers = [row[0] for row in rows]
        labels = [f"{row[0][:8]} · " + (
            datetime.fromtimestamp(row[2]).strftime("%d/%m/%Y %H:%M") if row[1] == "scheduled" else "Itens pendentes")
            for row in rows]
        selection = ttk.Combobox(dialog, state="readonly", values=labels, width=38)
        selection.pack(padx=8, pady=8)
        if labels:
            selection.current(0)
        else:
            ttk.Label(dialog, text="Nenhuma fila pendente ou agendada.").pack(padx=8)

        def apply(cancel):
            index = selection.current()
            if index < 0:
                return
            identifier = identifiers[index]
            try:
                if cancel:
                    if self.queue_store.cancel(identifier):
                        self.status.set("Agendamento cancelado.")
                else:
                    self.launch_saved_queue(identifier)
            except sqlite3.Error:
                self.status.set("Falha ao atualizar a fila salva.")
            dialog.destroy()

        ttk.Button(dialog, text="Iniciar itens pendentes", command=lambda: apply(False)).pack(padx=8, pady=3)
        ttk.Button(dialog, text="Cancelar agendamento", command=lambda: apply(True)).pack(padx=8, pady=3)

    def queue_tick(self):
        try:
            if self.queue_store and self.running and self.queue_id:
                self.queue_store.heartbeat(self.queue_id, self.queue_owner)
            elif self.queue_store and not self.running:
                self.queue_store.recover_stale()
                for identifier, status, scheduled in self.queue_store.available():
                    if status == "scheduled" and self.app_started <= scheduled <= time():
                        if not self.launch_saved_queue(identifier):
                            # A stale or invalid scheduled profile requires explicit recovery.
                            self.queue_store.unschedule(identifier)
                        break
        except sqlite3.Error:
            self.status.set("Falha ao consultar o agendamento no SQLite.")
        self.root.after(2000, self.queue_tick)

    def launch_saved_queue(self, identifier, options=None):
        if self.running:
            return False
        try:
            snapshot, items = self.queue_store.read(identifier)
            pending = [(number, prompt) for number, prompt, status, _ in items if status == "pending"]
            if not pending:
                return False
            if options is None:
                options = {"max_customer_messages": snapshot["max_customer_messages"]}
                if snapshot["mode"] == AI_MODE:
                    profile = ProviderProfile(**snapshot["provider"])
                    current = self.catalog.profiles.get(profile.id) if self.catalog else None
                    # Use current secret only if it still belongs to the saved destination.
                    if current is None or (current.base_url, current.kind) != (profile.base_url, profile.kind):
                        raise ValueError("Restaure o perfil e o destino originais para recuperar a fila.")
                    options["ai_provider"] = Provider(profile, self.catalog.resolve(current).key)
                elif snapshot["mode"] == OLLAMA_MODE:
                    options["ollama_model"] = snapshot["ollama_model"]
                elif snapshot["mode"] == GROQ_MODE:
                    name = self.groq_key.get()
                    if not name and self.credentials:
                        names = self.credentials.list_names("groq")
                        name = names[0] if names else ""
                    secret = self.credentials.get("groq", name) if self.credentials and name else None
                    if not secret:
                        raise ValueError("Configure uma chave Groq para recuperar a fila.")
                    options["key"] = secret
            inputs = [(smoke.parse_scenario(json.loads(prompt)), "") if snapshot["mode"] == JSON_MODE
                      else (None, prompt) for _, prompt in pending]
            if not self.queue_store.claim(identifier, self.queue_owner):
                return False
        except (OSError, ValueError, KeyError, ProviderError, sqlite3.Error) as error:
            self.status.set(str(error) if isinstance(error, (ValueError, ProviderError)) else "Falha ao recuperar a fila.")
            return False
        self.launch_execution(snapshot, len(pending), inputs, options, queue_id=identifier,
                              positions=[number for number, _ in pending],
                              items=items)
        return True

    def launch_execution(self, snapshot, total, test_inputs, options, *, queue_id=None, positions=None, items=()):
        self.queue_id = queue_id
        self.active_positions.clear()
        self.quota_scope = None
        self.active_prompts = [prompt for _, prompt, _, _ in items] if queue_id else None
        self.execution_mode = snapshot["mode"]
        self.report = None
        self.saved_report_path = None
        self.results = {number: result for number, _, _, result in items if result is not None}
        self.completed_before_launch = len(self.results)
        self.previously_ended = sum(status not in {"pending", "running"} and result is None
                                    for _, _, status, result in items)
        self.run_control = batch.BatchControl()
        self.batch_timing = ExecutionTiming()
        self.total = len(items) if queue_id else total
        self.parallel = min(snapshot["parallel"], total)
        self.result_field.configure(state="disabled", values=())
        self.result_field.set("")
        self.set_running(True)
        for widget in (self.save_button, self.open_folder_button, self.night_view_button, self.copy_button):
            widget.state(["disabled"])
        self.result_status.set("")
        self.conversation.show()
        self.warning.set("")
        self.warning_label.pack_forget()
        if self.results:
            choices = [f"Teste {number}" for number in sorted(self.results)]
            self.result_field.configure(state="readonly", values=choices)
            self.result_field.set(choices[0])
            self.show_result()
        self.status.set(f"Iniciando {total} teste(s).")
        scenario, objective = test_inputs[0]
        Thread(
            target=batch.run_batch, args=(snapshot["url"], total, self.parallel, self.events),
            kwargs={"scenario": scenario, "objective": objective, "product": snapshot["product"],
                    "customer_name": snapshot["customer_name"], "headless": bool(queue_id),
                    "test_inputs": tuple(test_inputs) if queue_id else None, "positions": positions,
                    "queue_id": queue_id, "queue_owner": self.queue_owner,
                    "execution": ExecutionOptions(**snapshot["execution"]),
                    "related_profiles": tuple(self.catalog.profiles.values()) if self.catalog else (),
                    "control": self.run_control, "database_path": DEFAULT_DATABASE, **options},
        ).start()
        self.root.after(100, self.poll)

    def provider_options(self, message_limit, provider_field):
        max_customer_messages = batch.DEFAULT_MAX_CUSTOMER_MESSAGES
        if self.mode != JSON_MODE:
            try:
                max_customer_messages = int(message_limit.get())
                if max_customer_messages < 1:
                    raise ValueError
            except ValueError:
                raise ValueError("Informe um limite inteiro de pelo menos 1 mensagem do cliente.") from None
        options = {"max_customer_messages": max_customer_messages}
        if self.mode == AI_MODE:
            if self.catalog is None:
                raise ValueError("Não foi possível abrir o catálogo de provedores.")
            provider_id = self.provider_labels.get(provider_field.get(), self.catalog.default)
            profile = self.catalog.profiles.get(provider_id)
            if profile is None or not profile.model:
                raise ValueError("Configure um provedor e selecione um modelo em Configurações.")
            try:
                options["ai_provider"] = self.catalog.resolve(profile)
            except (OSError, UnicodeError):
                raise ValueError("Não foi possível ler a credencial do provedor.") from None
        elif self.mode == GROQ_MODE:
            key_name = self.groq_key.get()
            if not key_name:
                raise ValueError("Salve uma chave em Configurações → Chaves de API e selecione-a para o teste.")
            try:
                key = self.credentials.get("groq", key_name)
            except (OSError, UnicodeError):
                raise ValueError("Não foi possível ler a chave selecionada.") from None
            if not key:
                self.refresh_test_keys()
                raise ValueError("A chave selecionada não está mais salva. Selecione outra.")
            groq_api.validate_key_format(key)
            options["key"] = key
        elif self.mode == OLLAMA_MODE:
            if not self.refresh_ollama_models():
                raise ValueError(self.ollama_message.get())
            ollama_model = self.ollama_model.get()
            if not ollama_model:
                raise ValueError("Selecione um modelo instalado no Ollama em Configurações.")
            if not self.save_ollama_model():
                raise ValueError("Não foi possível salvar o modelo selecionado.")
            options["ollama_model"] = ollama_model
        return options

    def set_running(self, running):
        self.running = running
        for widget in (self.run_button, self.night_run_button, self.mode_field, self.groq_key,
                       self.ollama_model, self.total_field, self.parallel_field, self.customer_name_field,
                       self.product_field, self.message_limit_field, self.night_product,
                       self.night_parallel, self.night_limit, *self.provider_fields):
            widget.state(["disabled" if running else "!disabled"])
        self.night_queue.set_running(running)
        self.run_status.set("")
        self.update_run_buttons()

    def update_run_buttons(self):
        active = self.running and not self.run_control.stopped.is_set()
        paused = active and not self.run_control.ready.is_set()
        for button in self.pause_buttons:
            button.configure(text="Retomar" if paused else "Pausar")
            button.state(["!disabled" if active else "disabled"])
        for button in self.stop_buttons:
            button.state(["!disabled" if active else "disabled"])

    def toggle_pause(self):
        if not self.running or self.run_control.stopped.is_set():
            return
        if self.run_control.ready.is_set():
            self.run_control.pause()
            self.run_status.set("Fila pausada. As operações em andamento terminam antes da próxima etapa.")
        else:
            self.run_control.resume()
            self.run_status.set("")
        self.update_run_buttons()

    def stop_batch(self):
        if self.running and not self.run_control.stopped.is_set():
            self.run_control.stop()
            self.run_status.set("Encerrando o lote após as operações em andamento.")
            self.update_run_buttons()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    number, message = value
                    self.status.set(f"Teste {number}/{self.total} — {message}")
                elif kind == "active":
                    self.active_positions.add(value)
                elif kind == "queue_wait":
                    self.status.set(f"Intervalo entre testes · {value} s")
                elif kind == "quota_scope":
                    self.quota_scope, self.quota_metadata = value
                elif kind == "limit":
                    if value not in self.warning.get():
                        previous = self.warning.get()
                        self.warning.set(f"{previous} {value} Nenhum novo teste será iniciado neste lote.".strip())
                        self.warning_label.pack(anchor="w")
                elif kind == "storage_error":
                    previous = self.warning.get()
                    if value not in previous:
                        self.warning.set(f"{previous} {value}".strip())
                        self.warning_label.pack(anchor="w")
                elif kind == "batch_error":
                    self.warning.set(f"Não foi possível continuar o lote. {value}")
                    self.warning_label.pack(anchor="w")
                elif kind == "result":
                    self.add_result(value)
                elif kind == "finished":
                    self.finish()
                    return
        except Empty:
            self.refresh_metrics()
            self.root.after(100, self.poll)

    def refresh_metrics(self):
        if monotonic() - self.last_metrics < 1:
            return
        self.last_metrics = monotonic()
        ended = len(self.results) + self.previously_ended
        active = len(self.active_positions)
        pending = max(0, self.total - ended - active)
        errors = sum(result["status"] in {"error", "rate_limited"} for result in self.results.values())
        elapsed = self.batch_timing.fields(finished=False)["duration_seconds"]
        text = (f"{self.total} testes · {pending} pendentes · {active} ativos · {ended} encerrados · "
                f"{errors} erros · {ended / self.total:.0%} · {elapsed:.0f} s")
        observed = len(self.results) - self.completed_before_launch
        if observed > 0 and self.running:
            text += f" · restante ~{elapsed * (self.total - ended) / observed:.0f} s"
        if self.quota_scope and self.queue_store:
            try:
                minute, day = ConsumptionLedger(self.queue_store.store).snapshot(self.quota_scope)
                text += (f"\n{self.quota_metadata['name']} · {self.quota_metadata['model']} · "
                         f"requisições/min {minute[0]}, /dia {day[0]} · "
                         f"tokens medidos/min {minute[1]}, /dia {day[1]} · "
                         f"chamadas sem métrica/min {minute[2]}, /dia {day[2]}")
            except sqlite3.Error:
                pass
        self.metrics.set(text)

    def add_result(self, result):
        self.active_positions.discard(result["test"])
        self.results[result["test"]] = result
        options = [f"Teste {number}" for number in sorted(self.results)]
        self.result_field.configure(state="readonly", values=options)
        if not self.result_field.get():
            self.result_field.set(options[0])
            self.show_result()
        self.status.set(f"Resultados disponíveis para {len(self.results)} de {self.total} teste(s).")
        if self.active_prompts is not None:
            self.night_view_button.state(["!disabled"])
            try:
                self.write_report(self.report_content(finished=False))
            except (OSError, ValueError) as error:
                self.status.set(f"Falha ao salvar o resultado parcial. {error}")

    def report_content(self, finished):
        results = [self.results[number] for number in sorted(self.results)]
        pending = self.total - len(results) - self.previously_ended
        warning = self.warning.get()
        stopped = self.run_control.stopped.is_set()
        if (
            self.active_prompts is None and self.execution_mode == JSON_MODE and self.total == 1 and results
            and not stopped and results[0]["turns"] and results[0]["status"] != "error"
        ):
            report = legacy_conversation(results[0])
            if warning:
                report[0]["test_metadata"]["warning"] = warning
        else:
            report = {"total": self.total, "parallel": self.parallel,
                      "not_started" if finished else "pending": pending,
                      "tests": results, **self.batch_timing.fields(finished=finished)}
            if self.active_prompts is not None:
                report.update(prompts=self.active_prompts, finished=finished, queue_id=self.queue_id)
                usage = {}
                for test in results:
                    for metric, amount in test.get("token_usage", {}).items():
                        usage[metric] = usage.get(metric, 0) + amount
                if usage:
                    report["token_usage"] = usage
            if stopped and finished:
                report["stopped_by_user"] = True
            if warning:
                report["warning"] = warning
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"

    def write_report(self, content):
        if self.active_prompts is not None and self.saved_report_path is not None:
            write_text_atomically(self.saved_report_path, content)
        else:
            self.saved_report_path = save_report(self.preferences.results_folder, content)
        return self.saved_report_path

    def finish(self):
        self.set_running(False)
        self.last_metrics = 0
        self.refresh_metrics()
        completed = sum(result["termination_reason"] == "completed" for result in self.results.values())
        pending = self.total - len(self.results) - self.previously_ended
        self.report = self.report_content(finished=True)
        self.save_button.state(["!disabled"])

        title = "Testes concluídos" if completed == self.total else "Lote encerrado"
        if completed != self.total and self.run_control.stopped.is_set():
            title = "Lote parado"
        summary = f"{completed}/{self.total} teste(s) concluído(s)."
        if completed != self.total:
            summary += f" {len(self.results) - completed} com outros motivos de encerramento. {pending} não iniciado(s)."
        try:
            path = self.write_report(self.report)
        except (OSError, ValueError) as error:
            self.status.set(f"{title}. {summary} Falha ao salvar automaticamente.")
            if self.saved_report_path is not None:
                self.open_folder_button.state(["!disabled"])
            if self.active_prompts is None:
                messagebox.showerror(
                    title, f"{summary}\n\nFalha ao salvar o relatório em {self.preferences.results_folder}.\n"
                    f"{error}\n\nUse Salvar JSON para escolher outro local.", parent=self.root,
                )
        else:
            self.open_folder_button.state(["!disabled"])
            self.status.set(f"{title}. {summary}\nArquivo salvo em {path}")
            if self.active_prompts is None:
                notify = messagebox.showinfo if completed == self.total else messagebox.showwarning
                notify(title, f"{summary}\n\nArquivo salvo em {path}", parent=self.root)

    def show_result(self, event=None):
        number = int(self.result_field.get().split()[-1])
        result = self.results[number]
        status = STATUS_LABELS[result["status"]]
        if result.get("termination_reason") == "message_limit":
            status = TERMINATION_LABELS["message_limit"]
        self.result_status.set(status)
        self.conversation.show(result["turns"], execution_summary(result))
        self.copy_button.configure(state="normal" if result["turns"] or result["detail"] else "disabled")

    def save(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, initialfile="chatprobe.json", defaultextension=".json",
            filetypes=[("Arquivo JSON", "*.json")],
        )
        if path:
            try:
                destination = Path(path)
                destination.write_text(self.report, encoding="utf-8")
            except OSError as error:
                messagebox.showerror("Não foi possível salvar o JSON", str(error), parent=self.root)
            else:
                self.saved_report_path = destination
                self.open_folder_button.state(["!disabled"])

    def open_report_folder(self):
        if self.saved_report_path is None:
            return
        folder = self.saved_report_path.parent
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:
            messagebox.showerror("Pasta do relatório", str(error), parent=self.root)

    def close(self):
        if self.running:
            messagebox.showinfo("Teste em andamento", "Use Parar para encerrar o lote e salvar os resultados.",
                                parent=self.root)
        else:
            self.root.destroy()


def main():
    if getattr(sys, "frozen", False):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    root = tk.Tk()
    ChatProbeWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
