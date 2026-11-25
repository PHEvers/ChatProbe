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

from src.chatprobe import groq_api, ollama_api
from src.chatprobe.browser import smoke
from src.chatprobe.browser.timing import ExecutionTiming
from src.chatprobe.browser.version import VERSION
from src.chatprobe.chat_url import validate_chat_url
from src.chatprobe.desktop_conversation import ConversationView
from src.chatprobe.desktop_keys import KeysTab
from src.chatprobe.desktop_execution import ExecutionDialog
from src.chatprobe.desktop_history import HistoryView
from src.chatprobe.desktop_support import SupportView, setup_assistant
from src.chatprobe.desktop_library import LibraryView
from src.chatprobe.storage.library import Library
from src.chatprobe.history_rerun import RerunDraft
from src.chatprobe.desktop_night_queue import NightQueue
from src.chatprobe.desktop_layout import ExecutionPanel, Navigation, ScrollPanel
from src.chatprobe.desktop_prompt import PromptEditor
from src.chatprobe.desktop_providers import ProviderManager
from src.chatprobe.desktop_preferences import SETTINGS_FILE, load_preferences, save_preferences, save_report, write_text_atomically
from src.chatprobe.desktop_theme import DesktopTheme
from src.chatprobe.provider_catalog import ProviderCatalog
from src.chatprobe.providers import PRESETS, Provider, ProviderError, ProviderProfile
from src.chatprobe.runner import batch
from src.chatprobe.runner.reports import TERMINATION_LABELS, execution_summary, legacy_conversation
from src.chatprobe.storage.sqlite_store import DEFAULT_DATABASE, RunStore
from src.chatprobe.storage.history import HistoryStore
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
MISSING_PROVIDER_LABEL = "Selecione um provedor"
HISTORY_MODES = {"json": JSON_MODE, "groq": GROQ_MODE, "ollama": OLLAMA_MODE, "ai": AI_MODE}
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
        self.restoring = False
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
        self.rerun_draft = None
        self.library_origin = None
        self.library_content = None
        self.rerun_check = None
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
        self.theme.apply(self.preferences.dark_mode)
        self.prompt_titles = {}
        self.ollama_refresh = None
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
        root.geometry("1160x760")
        density = float(root.tk.call("tk", "scaling")) / (4 / 3)
        root.minsize(800, 620 + min(120, max(0, round((density - 1) * 160))))
        root.protocol("WM_DELETE_WINDOW", self.close)

        content = ttk.Frame(root, padding=(14, 12))
        content.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Selecione um produto e prepare o prompt para começar.")
        self.warning = tk.StringVar()
        self.run_status = tk.StringVar()
        self.metrics = tk.StringVar()
        self.profile_summary = tk.StringVar()
        self.indicators = ExecutionPanel(content, self.status, self.run_status, self.warning, self.metrics)
        self.indicators.pack(side="bottom", fill="x", pady=(12, 0))
        self.tabs = Navigation(content)
        self.tabs.pack(fill="both", expand=True)
        self.test_tab = self.tabs.add("Teste", "play")
        self.queue_tab = self.tabs.add("Fila de testes", "queue")
        self.library_tab = self.tabs.add("Biblioteca", "library")
        self.history_tab = self.tabs.add("Histórico", "history")
        self.settings_tab = self.tabs.add("Configurações", "settings")

        ttk.Label(self.test_tab, text="Teste individual", style="Section.TLabel").pack(anchor="w", pady=(0, 7))
        columns = ttk.Panedwindow(self.test_tab, orient="horizontal")
        execution = ttk.Frame(columns, style="Panel.TFrame", padding=12)
        workspace = ttk.Panedwindow(columns, orient="vertical")
        results = ttk.Labelframe(workspace, text="Conversa", padding=9, style="Card.TLabelframe")
        columns.add(execution, weight=1)
        columns.add(workspace, weight=2)

        settings = ttk.Frame(execution, style="Panel.TFrame")
        self.test_settings = settings
        settings.pack(fill="x")
        settings.columnconfigure((0, 1), weight=1)
        ttk.Label(settings, text="Configurações", style="PanelHeading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.product_field = ttk.Combobox(settings, values=smoke.PRODUCT_OPTIONS, state="readonly", width=18)
        self.product_field.set(smoke.DEFAULT_PRODUCT)
        ttk.Label(settings, text="Produto", style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w")
        self.product_field.grid(row=2, column=0, columnspan=2, pady=(2, 5), sticky="ew")
        self.test_provider = self.build_profile_link(settings, row=3, card=True)
        for column, (label, attribute) in enumerate((("Quantidade de testes", "total_field"),
                                                    ("Simultâneos", "parallel_field"))):
            ttk.Label(settings, text=label, style="PanelMuted.TLabel").grid(
                row=6, column=column, padx=(0, 5), sticky="w")
            field = ttk.Entry(settings, width=4)
            field.insert(0, "1")
            field.grid(row=7, column=column, padx=(0, 5), pady=(2, 0), sticky="ew")
            setattr(self, attribute, field)
        self.message_limit_label = ttk.Label(settings, text="Máx. interações", style="PanelMuted.TLabel")
        self.message_limit_field = ttk.Entry(settings, width=4)
        self.message_limit_field.insert(0, str(batch.DEFAULT_MAX_CUSTOMER_MESSAGES))
        self.rerun_tools = ttk.Frame(execution, style="Panel.TFrame")
        self.rerun_label = ttk.Label(self.rerun_tools, style="PanelMuted.TLabel")
        self.rerun_label.pack(anchor="w")
        ttk.Button(self.rerun_tools, text="Revisar...", command=self.review_rerun).pack(side="left")
        ttk.Button(self.rerun_tools, text="Limpar origem", command=self.clear_rerun).pack(side="left", padx=4)

        self.editor = ttk.Labelframe(workspace, text=JSON_MODE, padding=9, style="Card.TLabelframe")
        workspace.add(self.editor, weight=1)
        workspace.add(results, weight=2)
        self.prompt_editor = PromptEditor(self.editor, rows=4)
        self.prompt_editor.pack(fill="both", expand=True)
        self.scenario = self.prompt_editor.text
        self.prompt_editor.set_value(self.drafts[self.mode])

        result_header = ttk.Frame(results, style="Panel.TFrame")
        result_header.pack(fill="x", pady=(0, 6))
        ttk.Label(result_header, text="Resultado", style="PanelMuted.TLabel").pack(side="left", padx=(0, 8))
        self.result_field = ttk.Combobox(result_header, state="disabled", width=11)
        self.result_field.pack(side="left")
        self.result_field.bind("<<ComboboxSelected>>", self.show_result)
        self.conversation = ConversationView(results)
        self.copy_button = ttk.Button(
            result_header, text="Copiar conversa", command=self.conversation.copy, state="disabled",
        )
        self.expand_conversation = ttk.Button(result_header, text="Ampliar", state="disabled",
                                               command=self.conversation.expand)
        self.expand_conversation.pack(side="right")
        self.copy_button.configure(text="Copiar")
        self.copy_button.pack(side="right", padx=(4, 6))
        self.result_status = tk.StringVar()
        ttk.Label(results, textvariable=self.result_status, style="PanelMuted.TLabel").pack(
            anchor="w", pady=(0, 5),
        )
        self.conversation.pack(fill="both", expand=True)

        actions = ttk.Frame(execution, style="Panel.TFrame")
        actions.pack(side="bottom", fill="x", pady=(8, 0), before=settings)
        self.run_button = ttk.Button(actions, text="Iniciar teste", command=self.start, style="Primary.TButton")
        self.run_button.pack(fill="x", pady=(0, 4))
        controls = ttk.Frame(actions, style="Panel.TFrame")
        controls.pack(fill="x")
        self.build_run_buttons(controls)
        self.stop_buttons[-1].pack_configure(side="right")
        exports = ttk.Frame(results, style="Panel.TFrame")
        exports.pack(side="bottom", fill="x", pady=(5, 0), before=self.conversation)
        self.save_button = ttk.Button(exports, text="Exportar JSON", command=self.save, state="disabled")
        self.save_button.pack(side="right", padx=(6, 0))
        self.open_folder_button = ttk.Button(exports, text="Abrir pasta", command=self.open_report_folder, state="disabled")
        self.open_folder_button.pack(side="right")
        columns.pack(fill="both", expand=True)
        self.build_night_tab(self.queue_tab)
        self.history = HistoryView(self.history_tab, HistoryStore(self.queue_store.store) if self.queue_store else None,
                                   self.stage_history, self.enqueue_history, running=lambda: self.running)
        self.history.pack(fill="both", expand=True)
        self.library = LibraryView(self.library_tab, Library(self.queue_store.store) if self.queue_store else None,
                                   self.load_library_prompt, self.queue_library_prompts, self.library_history)
        self.library.pack(fill="both", expand=True)
        self.tabs.on_select = lambda page: self.history.refresh() if page is self.history_tab else None
        self.build_settings(self.settings_tab)
        self.refresh_providers()
        self.show_mode_fields()
        self.apply_theme()
        self.queue_timer = self.root.after(1000, self.queue_tick)
        self.root.bind("<Destroy>", self.cancel_timers, add="+")

    def cancel_timers(self, event):
        if event.widget is self.root:
            for name in ("queue_timer", "ollama_timer"):
                timer = getattr(self, name, None)
                if timer:
                    self.root.after_cancel(timer)

    def build_profile_link(self, parent, *, compact=False, row=0, card=False):
        profile = ttk.Frame(parent, style="Panel.TFrame" if card else "TFrame")
        profile.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        label = ttk.Label(profile, textvariable=self.profile_summary, style="PanelMuted.TLabel" if card else "Subtitle.TLabel", wraplength=365 if compact else 230)
        if compact:
            label.pack(side="left", padx=(0, 10))
        else:
            label.pack(anchor="w")
        field = ttk.Combobox(profile, state="readonly", width=18)
        self.provider_fields.append(field)
        return field

    def refresh_providers(self):
        profiles = self.catalog.profiles.values() if self.catalog else ()
        self.provider_labels = {
            f"{p.name} · {p.model or 'sem modelo'} [{p.id[:8]}]": p.id for p in profiles
        }
        choices = (DEFAULT_PROVIDER_LABEL, *self.provider_labels)
        for field in self.provider_fields:
            field.configure(values=choices)
            if field.get() not in choices and field.get() != MISSING_PROVIDER_LABEL:
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
        self.night_provider = self.build_profile_link(controls, compact=True)
        controls.grid_columnconfigure(1, weight=1)
        parameters = ttk.Frame(controls)
        parameters.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Label(parameters, text="Produto").pack(side="left", padx=(0, 6))
        self.night_product = ttk.Combobox(parameters, values=smoke.PRODUCT_OPTIONS, state="readonly", width=15)
        self.night_product.set(smoke.DEFAULT_PRODUCT)
        self.night_product.pack(side="left")
        ttk.Label(parameters, text="Simultâneos").pack(side="left", padx=(12, 6))
        self.night_parallel = ttk.Entry(parameters, width=4)
        self.night_parallel.insert(0, "1")
        self.night_parallel.pack(side="left")
        self.night_limit_label = ttk.Label(parameters, text="Máx. interações")
        self.night_limit = ttk.Entry(parameters, width=4)
        self.night_limit.insert(0, str(batch.DEFAULT_MAX_CUSTOMER_MESSAGES))

        self.execution_summary_text = tk.StringVar()
        ttk.Label(controls, textvariable=self.execution_summary_text, style="Subtitle.TLabel",
                  wraplength=640).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.update_execution_summary()

        footer = ttk.Frame(tab)
        footer.pack(side="bottom", fill="x", pady=(10, 0))
        actions = ttk.Frame(footer)
        actions.pack(fill="x")
        self.night_run_button = ttk.Button(actions, text="Executar fila", command=lambda: self.start(nightly=True),
                                          style="Primary.TButton")
        self.night_run_button.pack(side="left")
        self.build_run_buttons(actions)
        self.night_view_button = ttk.Button(actions, text="Ver conversas",
                                           command=lambda: self.tabs.select(self.test_tab), state="disabled")
        self.night_view_button.pack(side="right")

        tools = ttk.Frame(controls)
        tools.grid(row=3, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Button(tools, text="Controle de execução...", command=self.edit_execution).pack(side="left")
        self.schedule_button = ttk.Button(tools, text="Agendar...", command=self.schedule_queue)
        self.schedule_button.pack(side="left", padx=(6, 0))
        ttk.Button(tools, text="Recuperar / cancelar...", command=self.manage_saved_queues).pack(side="left", padx=6)

        self.night_queue = NightQueue(tab, self.preferences.night_prompts, self.save_night_prompts, self.status.set,
                                      titles=self.preferences.night_prompt_titles, origins=self.preferences.night_prompt_origins)
        self.night_queue.pack(fill="both", expand=True)

    def save_night_prompts(self, prompts, titles, origins):
        return self.update_preferences(replace(self.preferences, night_prompts=prompts, night_prompt_titles=titles,
                                               night_prompt_origins=origins))

    def update_execution_summary(self):
        options = self.execution_options
        quota = options.queue_quota or "desativada"
        self.execution_summary_text.set(
            f"Interações {options.interaction_seconds:g} s · entre testes {options.test_seconds:g} s · "
            f"cota da fila (RPM RPD TPM TPD) {quota}"
        )

    def build_run_buttons(self, parent):
        pause = ttk.Button(parent, text="Pausar", command=self.toggle_pause, state="disabled")
        pause.pack(side="left", padx=(8, 0))
        self.pause_buttons.append(pause)
        stop = ttk.Button(parent, text="Parar", command=self.stop_batch, state="disabled")
        stop.pack(side="left", padx=(8, 0))
        self.stop_buttons.append(stop)

    def build_settings(self, tab):
        categories = ttk.Notebook(tab)
        categories.pack(fill="both", expand=True)

        def page(title):
            panel = ScrollPanel(categories)
            categories.add(panel, text=title)
            return panel.content

        application = page("Aplicação")
        providers = page("Provedores de IA")
        consumption = page("Consumo")
        storage = page("Armazenamento")
        appearance = page("Aparência")
        help_page = page("Ajuda")
        SupportView(help_page, self, DEFAULT_DATABASE).pack(fill="x")

        destination = ttk.Labelframe(application, text="Aplicação de atendimento", padding=12)
        destination.pack(fill="x", pady=(0, 10))
        ttk.Label(destination, text="URL do chat").pack(anchor="w", pady=(0, 4))
        address = ttk.Frame(destination)
        address.pack(fill="x")
        self.chat_url = tk.StringVar(value=self.preferences.chat_url)
        field = ttk.Entry(address, textvariable=self.chat_url)
        field.pack(side="left", fill="x", expand=True)
        field.bind("<Return>", self.save_chat_url)
        ttk.Button(address, text="Salvar URL", command=self.save_chat_url).pack(side="left", padx=(8, 0))

        identity = ttk.Labelframe(application, text="Identificação do cliente", padding=12)
        identity.pack(fill="x")
        identity.columnconfigure(1, weight=1)
        ttk.Label(identity, text="Nome padrão").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.customer_name_field = ttk.Entry(identity, width=20)
        self.customer_name_field.insert(0, self.preferences.customer_name or smoke.DEFAULT_CUSTOMER_NAME)
        self.customer_name_field.grid(row=0, column=1, sticky="ew")
        self.customer_name_field.bind("<FocusOut>", self.save_customer_name)
        self.customer_name_field.bind("<Return>", self.save_customer_name)
        ttk.Label(identity, text=f"CNPJ de triagem padrão · {smoke.DEFAULT_CNPJ}",
                  style="Subtitle.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        execution = ttk.Labelframe(providers, text="Modo de execução", padding=12)
        execution.pack(fill="x", pady=(0, 10))
        execution.columnconfigure(1, weight=1)
        ttk.Label(execution, text="Modo").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.mode_field = ttk.Combobox(execution, values=self.modes, state="readonly", width=18)
        self.mode_field.set(self.mode)
        self.mode_field.grid(row=0, column=1, sticky="ew")
        self.mode_field.bind("<<ComboboxSelected>>", self.change_mode)
        self.groq_key_label = ttk.Label(execution, text="Chave Groq")
        self.groq_key = ttk.Combobox(execution, state="readonly", width=20, postcommand=self.refresh_test_keys)
        self.groq_notice = ttk.Label(
            execution, text=f"Modelo {groq_api.MODEL}. A Groq recebe o prompt e a conversa.",
            wraplength=400, style="Subtitle.TLabel",
        )
        self.ollama_model_label = ttk.Label(execution, text="Modelo Ollama")
        self.ollama_model = ttk.Combobox(execution, state="readonly", width=20, postcommand=self.refresh_ollama_models)
        self.ollama_model.set(self.preferences.ollama_model)
        self.ollama_model.bind("<<ComboboxSelected>>", self.save_ollama_model)
        self.ollama_message = tk.StringVar()
        self.ollama_notice = ttk.Label(execution, textvariable=self.ollama_message,
                                      wraplength=400, style="Subtitle.TLabel")
        ttk.Button(providers, text="Gerenciar provedores...", command=self.open_provider_manager).pack(
            anchor="w", pady=(0, 10))
        if self.credentials:
            keys_tab = ttk.Labelframe(providers, text="Chaves de API existentes", padding=12)
            keys_tab.pack(fill="x")
            self.keys_tab = KeysTab(keys_tab, self.credentials, self.refresh_test_keys)

        rules = ttk.Labelframe(consumption, text="Valores padrão das execuções", padding=12)
        rules.pack(fill="x", pady=(0, 10))
        ttk.Button(rules, text="Controle de execução...", command=self.edit_execution).pack(anchor="w")
        ttk.Label(
            rules, text="Intervalos, cota da fila e recuperação de falhas. As cotas por serviço e modelo ficam no gerenciador de provedores.",
            style="Subtitle.TLabel", wraplength=530,
        ).pack(anchor="w", pady=(8, 0))
        conversation = ttk.Labelframe(consumption, text="Comportamento das conversas", padding=12)
        conversation.pack(fill="x")
        ttk.Label(
            conversation, text="Confirmações podem se repetir em etapas diferentes. O limite de mensagens é definido em cada teste ou fila.",
            style="Subtitle.TLabel", wraplength=530,
        ).pack(anchor="w")

        results = ttk.Labelframe(storage, text="Pasta de relatórios JSON", padding=12)
        results.pack(fill="x", pady=(0, 10))
        self.results_folder = tk.StringVar(value=str(self.preferences.results_folder))
        ttk.Entry(results, textvariable=self.results_folder, state="readonly").pack(side="left", fill="x", expand=True)
        ttk.Button(results, text="Escolher pasta...", command=self.choose_results_folder).pack(side="left", padx=(8, 0))
        ttk.Label(storage, text="As execuções também são armazenadas no SQLite local. "
                               "A interface usa o fuso do sistema e os relatórios registram horários em UTC.",
                  style="Subtitle.TLabel", wraplength=540).pack(anchor="w")
        self.dark_mode = tk.BooleanVar(value=self.preferences.dark_mode)
        ttk.Checkbutton(appearance, text="Usar tema escuro", variable=self.dark_mode,
                        command=self.change_theme).pack(anchor="w")

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
        palette = self.theme.apply(self.preferences.dark_mode)
        self.prompt_editor.apply_palette(palette)
        self.night_queue.prompt_editor.apply_palette(palette)
        self.conversation.apply_palette(palette)
        self.history.apply_palette(palette)
        self.library.editor.apply_palette(palette)

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
        self.switch_mode(mode)

    def switch_mode(self, mode):
        self.drafts[self.mode] = self.prompt_editor.value()
        self.prompt_titles[self.mode] = self.prompt_editor.title_var.get()
        self.mode = mode
        self.mode_field.set(mode)
        self.prompt_editor.set_value(self.drafts[self.mode], self.prompt_titles.get(self.mode, ""))
        self.show_mode_fields()

    @staticmethod
    def library_reference(row, run=None):
        return {"library_prompt_id": str(row[0]), "library_collection_id": row[1],
                "library_prompt_version": row[3], "library_collection_run_id": run, "library_expected": row[2].expected}

    def load_library_prompt(self, row):
        if self.running:
            return
        self.clear_rerun()
        prompt = row[2]
        mode = JSON_MODE if prompt.mode == "json" else AI_MODE
        if mode != self.mode:
            self.switch_mode(mode)
        self.prompt_editor.set_value(prompt.content, prompt.title)
        self.library_origin = self.library_reference(row)
        self.library_content = prompt.content
        if prompt.product in smoke.PRODUCT_OPTIONS:
            self.product_field.set(prompt.product)
        self.tabs.select(self.test_tab)

    def queue_library_prompts(self, rows):
        if not rows or self.running:
            return
        if any((row[2].mode == "json") != (self.mode == JSON_MODE) for row in rows):
            self.library.status.set("Selecione o modo correspondente à fila antes de enviar.")
            return
        if not self.night_queue.commit_draft():
            return
        run = uuid4().hex
        queue = self.night_queue
        if queue.persist(queue.prompts + tuple(row[2].content for row in rows),
                         queue.titles + tuple(row[2].title for row in rows),
                         queue.origins + tuple(self.library_reference(row, run) for row in rows)):
            self.tabs.select(self.queue_tab)

    def library_history(self, identifier, *, collection=False):
        key = "library_collection" if collection else "library_prompt"
        self.history.filters = {key: str(identifier)}
        self.history.page = 0
        self.tabs.select(self.history_tab)

    def stage_history(self, record):
        if self.running or not record.get("prompt_text"):
            self.history.status.set("O prompt ou roteiro original deste teste não está disponível.")
            return
        mode = HISTORY_MODES.get(record["mode"])
        if mode not in self.modes:
            if mode == GROQ_MODE and AI_MODE in self.modes:
                mode = AI_MODE
            else:
                self.history.status.set("O modo original é desconhecido ou indisponível neste computador.")
                return
        if self.prompt_editor.expanded:
            self.history.status.set("Feche o editor ampliado antes de carregar outro prompt.")
            return
        if mode != self.mode:
            self.switch_mode(mode)
        self.library_origin = None
        self.rerun_draft = RerunDraft.from_record(record, self.preferences.chat_url,
                                                 self.customer_name_field.get(), self.execution_options)
        self.rerun_check = None
        self.rerun_label.configure(text=f"Origem · {record['id'][:12]}")
        self.rerun_tools.pack(side="bottom", fill="x", pady=(5, 0), before=self.test_settings)
        self.prompt_editor.set_value(record["prompt_text"], record.get("title") or "")
        product = record.get("product")
        if product in smoke.PRODUCT_OPTIONS:
            self.product_field.set(product)
        limit = (record.get("settings") or {}).get("max_customer_messages")
        for field, value in ((self.total_field, 1), (self.parallel_field, 1), (self.message_limit_field, limit)):
            if type(value) is int and value > 0:
                field.delete(0, "end")
                field.insert(0, str(value))
        provider = record.get("provider") or {}
        if mode == AI_MODE:
            matching = next((label for label, identifier in self.provider_labels.items()
                             if identifier == provider.get("id")
                             and self.catalog.profiles[identifier].model == provider.get("model")), None)
            self.test_provider.set(matching or MISSING_PROVIDER_LABEL)
        elif mode == OLLAMA_MODE and provider.get("model"):
            self.ollama_model.set(provider["model"])
        self.tabs.select(self.test_tab)
        self.status.set("Confira as condições e revise a URL antes de iniciar a reexecução." if mode == JSON_MODE else
                        "Confira o provedor e o modelo. A disponibilidade será verificada ao iniciar.")

    def clear_rerun(self):
        self.rerun_draft = None
        self.rerun_check = None
        self.rerun_tools.pack_forget()
        if self.test_provider.get() == MISSING_PROVIDER_LABEL:
            self.test_provider.set(DEFAULT_PROVIDER_LABEL)

    def review_rerun(self):
        draft = self.rerun_draft
        if not draft or self.running:
            return
        url = simpledialog.askstring("URL da reexecução", "Revise o destino do chat:", initialvalue=draft.url,
                                     parent=self.root)
        if url is None:
            return
        try:
            url = validate_chat_url(url)
        except ValueError as error:
            self.status.set(str(error))
            return
        name = simpledialog.askstring("Identificação", "Revise o nome do cliente:", initialvalue=draft.customer_name,
                                      parent=self.root)
        if not name or not name.strip():
            return
        options = ExecutionDialog(self.root, draft.execution).result
        use_provider_settings = draft.use_provider_settings
        if self.mode == AI_MODE and draft.provider_id and draft.model:
            use_provider_settings = messagebox.askyesno(
                "Parâmetros do provedor", "Usar a temperatura e o timeout registrados se o perfil e o modelo coincidirem?",
                parent=self.root,
            )
        self.rerun_draft = replace(draft, url=url, customer_name=name.strip(),
                                   execution=options or draft.execution, use_provider_settings=use_provider_settings)

    def enqueue_history(self, records):
        if self.running or self.night_queue.prompt_editor.expanded:
            return
        if any(not item.get("prompt_text") or HISTORY_MODES.get(item["mode"]) != self.mode for item in records):
            self.history.status.set("Selecione testes com prompt original e com o mesmo modo de execução da fila.")
            return
        if messagebox.askyesno("Enviar à fila", f"Adicionar {len(records)} teste(s) com as condições atuais da fila?",
                               parent=self.root) and self.night_queue.append_history(records):
            self.tabs.select(self.queue_tab)
            self.status.set(f"{len(records)} teste(s) adicionado(s) à fila.")

    def verify_history_provider(self, options, start):
        draft = self.rerun_draft
        provider = options.get("ai_provider")
        ollama_model = options.get("ollama_model")
        groq_key = options.get("key")
        signature = (draft, provider, ollama_model, groq_key)
        if self.rerun_check == signature or not (provider or ollama_model or groq_key):
            return True
        if getattr(self, "checking_history_provider", False):
            return False
        self.checking_history_provider = True
        self.status.set("Verificando disponibilidade do provedor e do modelo...")

        def verify():
            try:
                if provider:
                    provider.check_connection()
                elif ollama_model and ollama_model not in ollama_api.list_models():
                    return "Modelo não listado. Escolha outro modelo de Ollama."
                elif groq_key:
                    profile = ProviderProfile("groq", "Groq", *PRESETS["Groq"], model=groq_api.MODEL)
                    Provider(profile, groq_key).check_connection()
            except (ProviderError, ollama_api.OllamaError, ValueError) as error:
                return str(error)
            except OSError:
                return "Servidor indisponível."
            return ""

        def checked(error):
            self.checking_history_provider = False
            if self.rerun_draft is not draft or self.running or not self.root.winfo_exists():
                return
            if error:
                self.status.set(f"{error} Revise o provedor ou escolha outro.")
            else:
                self.rerun_check = signature
                start()

        self.history.submit(verify, checked)
        return False

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
        model = self.ollama_model.get() if self.mode == OLLAMA_MODE else groq_api.MODEL if self.mode == GROQ_MODE else ""
        default = self.catalog.profiles.get(self.catalog.default) if self.catalog else None
        if self.mode == AI_MODE and default:
            self.profile_summary.set(f"Padrão · {default.name} / {default.model or 'sem modelo'}")
        else:
            self.profile_summary.set(f"{self.mode} · {model}" if model else self.mode)

    def show_mode_fields(self):
        for widget in (self.groq_key_label, self.groq_key, self.groq_notice,
                       self.ollama_model_label, self.ollama_model, self.ollama_notice,
                       self.message_limit_label, self.message_limit_field):
            widget.grid_remove()
        for field in self.provider_fields:
            if self.mode == AI_MODE:
                if field is self.night_provider:
                    field.pack(side="left", fill="x", expand=True)
                else:
                    field.pack(fill="x", pady=(4, 0))
            else:
                field.pack_forget()
        input_label = JSON_MODE if self.mode == JSON_MODE else "Prompt inicial"
        self.editor.configure(text=input_label)
        self.night_queue.form.configure(text=input_label)
        self.prompt_editor.set_ai_mode(self.mode != JSON_MODE)
        self.night_queue.prompt_editor.set_ai_mode(self.mode != JSON_MODE)
        if self.mode != JSON_MODE:
            self.message_limit_label.grid(row=4, column=0, sticky="w")
            self.message_limit_field.grid(row=5, column=0, columnspan=2, pady=(2, 5), sticky="ew")
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
        if self.ollama_refresh is not None:
            return
        result = Queue()
        self.ollama_refresh = result
        self.ollama_message.set("Consultando modelos instalados...")

        def worker():
            try:
                result.put((ollama_api.list_models(), ""))
            except ollama_api.OllamaError as error:
                result.put(([], str(error)))

        def finish():
            try:
                models, error = result.get_nowait()
            except Empty:
                self.ollama_timer = self.root.after(80, finish)
                return
            self.ollama_refresh = None
            self.ollama_model.configure(values=models)
            selected = self.ollama_model.get()
            if models and not selected:
                self.ollama_model.set(models[0])
            if not error and selected and selected not in models:
                error = "O modelo salvo não está instalado. Selecione um dos modelos disponíveis."
            self.ollama_message.set(error or ("Geração local, sem chave de API." if models else
                                    "Nenhum modelo local está disponível."))
            self.update_profile_summary()

        Thread(target=worker, daemon=True).start()
        self.ollama_timer = self.root.after(80, finish)

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
        if self.running or self.restoring:
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

        draft = self.rerun_draft if not nightly else None
        customer_name = draft.customer_name if draft else self.customer_name_field.get().strip()
        product = (self.night_product if nightly else self.product_field).get().strip()
        if not customer_name or product not in smoke.PRODUCT_OPTIONS:
            self.status.set("Preencha o Nome do cliente em Configurações e selecione um Produto.")
            return

        test_inputs = []
        texts = prompts if nightly else (self.prompt_editor.value(),)
        for number, text in enumerate(texts, 1):
            if self.mode == JSON_MODE:
                try:
                    test_inputs.append((smoke.parse_scenario(json.loads(text)), ""))
                except ValueError as error:
                    label = f"Roteiro {number}" if nightly else "Roteiro"
                    self.status.set(f"{label} inválido. {error}")
                    return
            elif not text.strip():
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
        if draft:
            provider = options.get("ai_provider")
            if provider and draft.use_provider_settings and (
                provider.profile.id, provider.profile.model
            ) == (draft.provider_id, draft.model):
                changes = {}
                if isinstance(draft.timeout, (int, float)) and 0 < draft.timeout <= 600:
                    changes["timeout"] = draft.timeout
                limit = 1 if provider.profile.kind == "anthropic" else 2
                if draft.temperature is None or (isinstance(draft.temperature, (int, float))
                                                 and 0 <= draft.temperature <= limit):
                    changes["temperature"] = draft.temperature
                options["ai_provider"] = replace(provider, profile=replace(provider.profile, **changes))
            if not self.verify_history_provider(options, self.start):
                return
            if not messagebox.askyesno("Reexecutar teste", f"Iniciar nova execução em {draft.url}?",
                                       parent=self.root):
                return
        elif not self.save_chat_url() or not self.save_customer_name():
            return

        snapshot = {"url": draft.url if draft else self.preferences.chat_url, "mode": self.mode, "parallel": parallel,
                    "product": product, "customer_name": customer_name,
                    "execution": asdict(draft.execution if draft else self.execution_options),
                    "prompt_text": None if nightly else texts[0],
                    "prompt_origins": self.night_queue.origins if nightly else (),
                    "source_test_id": draft.source if draft else None,
                    "library_origin": self.library_origin if not nightly and texts[0] == self.library_content else None,
                    "prompt_titles": self.night_queue.titles if nightly else (),
                    "prompt_title": "" if nightly else self.prompt_editor.title_var.get().strip(),
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
                self.indicators.set_state("Aguardando horário", "warning")
                self.status.set("Fila agendada. Mantenha o aplicativo aberto e o computador ligado.")
            else:
                self.launch_saved_queue(identifier, options)
            return
        self.launch_execution(snapshot, total, test_inputs, options)
        if draft:
            self.clear_rerun()

    def edit_execution(self):
        dialog = ExecutionDialog(self.root, self.execution_options)
        if dialog.result is not None and self.update_preferences(
            replace(self.preferences, execution=asdict(dialog.result))
        ):
            self.execution_options = dialog.result
            self.update_execution_summary()

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
                    if not messagebox.askyesno("Cancelar agendamento", "Cancelar a execução agendada?",
                                               parent=dialog):
                        return
                    if self.queue_store.cancel(identifier):
                        if not self.running:
                            self.indicators.set_state("Pronto")
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
            elif (self.queue_store and not self.restoring and not self.running and not self.prompt_editor.expanded
                  and not self.night_queue.prompt_editor.expanded):
                self.queue_store.recover_stale()
                for identifier, status, scheduled in self.queue_store.available():
                    if status == "scheduled" and self.app_started <= scheduled <= time():
                        if not self.launch_saved_queue(identifier):
                            # A stale or invalid scheduled profile requires explicit recovery.
                            self.queue_store.unschedule(identifier)
                        break
        except sqlite3.Error:
            self.status.set("Falha ao consultar o agendamento no SQLite.")
        self.queue_timer = self.root.after(2000, self.queue_tick)

    def launch_saved_queue(self, identifier, options=None):
        if self.running or self.restoring:
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
        self.active_prompt_titles = snapshot.get("prompt_titles", ())
        self.active_prompt_title = snapshot.get("prompt_title", "")
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
        for widget in (self.save_button, self.open_folder_button, self.night_view_button, self.copy_button, self.expand_conversation):
            widget.state(["disabled"])
        self.result_status.set("")
        self.conversation.show()
        self.warning.set("")
        self.indicators.set_state("Em execução", "active")
        self.indicators.set_progress(len(self.results), self.total)
        if self.results:
            choices = [f"Teste {number}" for number in sorted(self.results)]
            self.result_field.configure(state="readonly", values=choices)
            self.result_field.set(choices[0])
            self.show_result()
        self.status.set(f"Iniciando {total} teste(s).")
        scenario, objective = test_inputs[0]
        origins, titles = snapshot.get("prompt_origins", ()), snapshot.get("prompt_titles", ())
        history_items = {number: {
            "prompt_text": prompt, "title": titles[number-1] if number <= len(titles) else None,
            **((origins[number-1] if isinstance(origins[number-1], dict) else
                {"source_test_id": origins[number-1]}) if number <= len(origins) else {}),
        } for number, prompt, _, _ in items} if queue_id else {number: {
            "prompt_text": snapshot.get("prompt_text"), "title": snapshot.get("prompt_title"),
            "source_test_id": snapshot.get("source_test_id"),
            **(snapshot.get("library_origin") or {}),
        } for number in range(1, total+1)}
        Thread(
            target=batch.run_batch, args=(snapshot["url"], total, self.parallel, self.events),
            kwargs={"scenario": scenario, "objective": objective, "product": snapshot["product"],
                    "customer_name": snapshot["customer_name"], "headless": bool(queue_id),
                    "test_inputs": tuple(test_inputs) if queue_id else None, "positions": positions,
                    "queue_id": queue_id, "queue_owner": self.queue_owner,
                    "execution": ExecutionOptions(**snapshot["execution"]),
                    "related_profiles": tuple(self.catalog.profiles.values()) if self.catalog else (),
                    "control": self.run_control, "database_path": DEFAULT_DATABASE, "history_items": history_items, **options},
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
            if provider_field.get() == MISSING_PROVIDER_LABEL:
                raise ValueError("Selecione um provedor atual para reexecutar este teste.")
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
                raise ValueError("Salve uma chave em Configurações → Provedores de IA e selecione-a para o teste.")
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
            ollama_model = self.ollama_model.get()
            available = self.ollama_model.cget("values")
            if not ollama_model or (available and ollama_model not in available):
                self.refresh_ollama_models()
                raise ValueError("Selecione um modelo instalado em Configurações → Provedores de IA.")
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
        self.prompt_editor.set_enabled(not running)
        if hasattr(self, "history"):
            self.history.actions_for(self.history.listing.selection())
        self.schedule_button.state(["disabled" if running else "!disabled"])
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
            self.run_control.audit("pause")
            self.indicators.set_state("Pausado", "warning")
            self.run_status.set("Fila pausada. As operações em andamento terminam antes da próxima etapa.")
        else:
            self.run_control.resume()
            self.run_control.audit("resume")
            self.indicators.set_state("Em execução", "active")
            self.run_status.set("")
        self.update_run_buttons()

    def stop_batch(self):
        if self.running and not self.run_control.stopped.is_set():
            self.run_control.stop()
            self.indicators.set_state("Interrompendo", "warning")
            self.run_status.set("Encerrando o lote após as operações em andamento.")
            self.update_run_buttons()

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    number, message = value
                    self.status.set(f"Teste {number}/{self.total} — {message}")
                    if self.run_control.ready.is_set() and not self.run_control.stopped.is_set():
                        if message.startswith(("Intervalo", "Aguardando disponibilidade", "Aguardando nova")):
                            self.indicators.set_state("Aguardando", "warning")
                        else:
                            self.indicators.set_state("Em execução", "active")
                elif kind == "active":
                    self.active_positions.add(value)
                    if self.run_control.ready.is_set() and not self.run_control.stopped.is_set():
                        self.indicators.set_state("Em execução", "active")
                elif kind == "queue_wait":
                    if self.run_control.ready.is_set() and not self.run_control.stopped.is_set():
                        self.status.set(f"Intervalo entre testes · {value} s")
                        self.indicators.set_state("Aguardando intervalo", "warning")
                elif kind == "quota_scope":
                    self.quota_scope, self.quota_metadata = value
                elif kind == "limit":
                    if value not in self.warning.get():
                        previous = self.warning.get()
                        self.warning.set(f"{previous} {value} Nenhum novo teste será iniciado neste lote.".strip())
                elif kind == "storage_error":
                    previous = self.warning.get()
                    if value not in previous:
                        self.warning.set(f"{previous} {value}".strip())
                elif kind == "batch_error":
                    self.warning.set(f"Não foi possível continuar o lote. {value}")
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
        self.indicators.set_progress(ended, self.total)

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
                      "tests": results, "application_version": VERSION, **self.batch_timing.fields(finished=finished)}
            if self.active_prompts is not None:
                report.update(prompts=self.active_prompts, finished=finished, queue_id=self.queue_id)
                if any(self.active_prompt_titles):
                    report["prompt_titles"] = self.active_prompt_titles
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
        if self.active_prompt_title:
            metadata = report[0]["test_metadata"] if isinstance(report, list) else report
            metadata["prompt_title"] = self.active_prompt_title
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"

    def write_report(self, content):
        if self.active_prompts is not None and self.saved_report_path is not None:
            write_text_atomically(self.saved_report_path, content)
        else:
            self.saved_report_path = save_report(self.preferences.results_folder, content)
        return self.saved_report_path

    def finish(self):
        self.set_running(False)
        if self.tabs.select() is self.history_tab:
            self.history.refresh()
        self.last_metrics = 0
        self.refresh_metrics()
        completed = sum(result["termination_reason"] == "completed" for result in self.results.values())
        pending = self.total - len(self.results) - self.previously_ended
        if self.run_control.stopped.is_set() or any(
            result["status"] in {"stopped", "interrupted"} for result in self.results.values()
        ):
            self.indicators.set_state("Interrompido", "warning")
        elif self.warning.get() or any(result["status"] in {"error", "rate_limited"} for result in self.results.values()):
            self.indicators.set_state("Encerrado com ocorrências", "error")
        else:
            self.indicators.set_state("Encerrado", "success")
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
        available = bool(result["turns"] or result["detail"])
        for button in (self.copy_button, self.expand_conversation):
            button.state(["!disabled" if available else "disabled"])

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
        if self.restoring:
            return
        if self.running:
            messagebox.showinfo("Teste em andamento", "Use Parar para encerrar o lote e salvar os resultados.",
                                parent=self.root)
        else:
            self.root.destroy()


def main():
    if getattr(sys, "frozen", False):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    root = tk.Tk()
    first_use = not SETTINGS_FILE.exists()
    app = ChatProbeWindow(root)
    if first_use:
        root.after_idle(lambda: setup_assistant(app))
    root.mainloop()


if __name__ == "__main__":
    main()
