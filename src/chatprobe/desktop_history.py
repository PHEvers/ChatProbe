"""Consulta, comparação e ações locais do Histórico."""

import json
import sqlite3
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk
from uuid import uuid4

from src.chatprobe.desktop_conversation import ConversationView
from src.chatprobe.desktop_preferences import write_text_atomically
from src.chatprobe.desktop_prompt import PromptEditor
from src.chatprobe.history_reports import export_records, import_reports
from src.chatprobe.runner.reports import TERMINATION_LABELS
from src.chatprobe.storage.history import comparison_differences
from src.chatprobe.storage.history_metadata import display_time


UNKNOWN = "Desconhecido"
STATUS = {"completed": "Concluído", "interrupted": "Interrompido", "error": "Erro",
          "rate_limited": "Limite", "paused": "Pausado", "stopped": "Parado", "running": "Em execução"}
TYPES = {"single": "Individual", "batch": "Lote", "queue": "Fila de testes"}
EVENTS = {"started": "Teste iniciado", "triage_completed": "Triagem concluída", "finished": "Teste encerrado",
          "script_interrupted": "Roteiro interrompido", "pause": "Pausado", "resume": "Retomado"}


def known(value):
    return UNKNOWN if value is None or value == "" else str(value)


def seconds(value):
    return f"{value:.2f} s" if value is not None else UNKNOWN


def describe(record):
    provider = record.get("provider") or {}
    usage = record.get("token_usage") or {}
    rows = [
        ("ID", record["id"]), ("Lote", record.get("batch_id")), ("Origem", record.get("source_test_id")),
        ("Tipo", TYPES.get(record.get("execution_type"))), ("Produto", record.get("product")),
        ("Provedor", provider.get("name")), ("Modelo", provider.get("model")),
        ("Versão", record.get("application_version")), ("URL sanitizada", record.get("url")),
        ("Início", display_time(record.get("started_at"))), ("Fim", display_time(record.get("ended_at"))),
        ("Duração", seconds(record.get("duration_seconds"))),
        ("Status", STATUS.get(record["status"], UNKNOWN)),
        ("Encerramento", TERMINATION_LABELS.get(record.get("termination_reason"))),
        ("Interações", record["interactions"]), ("Requisições", record["requests"]),
        ("Tokens medidos", record["known_tokens"]), ("Tokens estimados", record["estimated_tokens"]),
        ("Tokens de entrada", usage.get("input_tokens")), ("Tokens de saída", usage.get("output_tokens")),
        ("Tempo nas chamadas de IA (inclui transporte)", seconds(record.get("model_seconds"))),
        ("Tempo registrado fora das esperas", seconds(record.get("processing_seconds"))),
    ]
    descriptions = [f"{key} · {known(value)}" for key, value in rows]
    if record.get("detail"):
        descriptions.extend(("", record["detail"]))
    settings = record.get("settings")
    if settings:
        descriptions.extend(("", "Configurações registradas", json.dumps(settings, ensure_ascii=False, indent=2)))
    waits = record.get("wait_seconds")
    if waits:
        descriptions.extend(("", "Esperas registradas"))
        descriptions.extend(f"{key} · {seconds(value)}" for key, value in waits.items())
    response = [turn["response_seconds"] for turn in record["turns"] if "response_seconds" in turn]
    if response:
        descriptions.extend(("", "Tempo do envio até a primeira resposta observada"))
        descriptions.extend(f"Turno {i} · {seconds(turn['response_seconds'])}"
                            for i, turn in enumerate(record["turns"], 1) if "response_seconds" in turn)
    if any(not turn["sent_at"] or any(stamp is None for stamp in turn["reply_received_at"])
           for turn in record["turns"]):
        descriptions.extend(("", "Algumas mensagens deste registro têm horário desconhecido."))
    return "\n".join(descriptions)


class HistoryDetail(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.title = ttk.Label(self, text="Detalhes", style="Section.TLabel", wraplength=340)
        self.title.pack(anchor="w", pady=(0, 4))
        tabs = ttk.Notebook(self, style="History.TNotebook")
        tabs.pack(fill="both", expand=True)
        self.data = PromptEditor(tabs, title=False, expandable=False, readonly=True)
        self.prompt = PromptEditor(tabs, title=False, readonly=True)
        self.events = PromptEditor(tabs, title=False, expandable=False, readonly=True)
        self.conversation = ConversationView(tabs)
        for widget, name in ((self.data, "Dados"), (self.prompt, "Prompt"),
                             (self.conversation, "Conversa"), (self.events, "Eventos")):
            tabs.add(widget, text=name, padding=6)
        tools = ttk.Frame(self)
        tools.pack(fill="x", pady=(5, 0))
        self.copy_conversation = ttk.Button(tools, text="Copiar", command=self.conversation.copy)
        self.copy_conversation.pack(side="left")
        self.expand_conversation = ttk.Button(tools, text="Ampliar", command=self.conversation.expand)
        self.expand_conversation.pack(side="left", padx=5)
        self.clear()

    def clear(self):
        self.title.configure(text="Detalhes")
        self.data.set_value("Selecione um teste na lista.")
        self.prompt.set_value("")
        self.events.set_value("")
        self.conversation.show()
        self.copy_conversation.state(["disabled"])
        self.expand_conversation.state(["disabled"])

    def show(self, record):
        self.title.configure(text=record.get("title") or f"Teste {record['id'][:12]}")
        self.data.set_value(describe(record))
        self.prompt.set_value(record.get("prompt_text") or "O prompt original não está disponível neste registro.")
        lines = [f"{display_time(event['occurred_at'])} · {EVENTS.get(event['kind'], event['kind'])}"
                 for event in record["events"]]
        for call in record["api_calls"]:
            measured = call.get("actual")
            if measured is None:
                measured = (call.get("token_usage") or {}).get("total_tokens")
            lines.append(f"{display_time(call.get('started_at'))} · Requisição {known(call.get('outcome'))} · "
                         f"turno {known(call.get('interaction'))} · {seconds(call.get('processing_seconds'))} · "
                         f"tokens medidos {known(measured)} · estimados {known(call.get('estimated'))}")
        self.events.set_value("\n".join(lines) or "Nenhum evento disponível.")
        self.conversation.show(record["turns"])
        state = ["!disabled"] if record["turns"] else ["disabled"]
        self.copy_conversation.state(state)
        self.expand_conversation.state(state)

    def apply_palette(self, palette):
        for widget in (self.data, self.prompt, self.events, self.conversation):
            widget.apply_palette(palette)


class HistoryView(ttk.Frame):
    def __init__(self, parent, history, rerun, enqueue, *, running):
        super().__init__(parent)
        self.history = history
        self.rerun = rerun
        self.enqueue = enqueue
        self.running = running
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="history")
        self.completed = Queue()
        self.filters = {}
        self.page = 0
        self.pages = 1
        self.generation = 0
        self.detail_generation = 0
        self.last_record = None
        self.palette = None

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 6))
        self.query = ttk.Entry(header, width=21)
        ttk.Label(header, text="Pesquisa").pack(side="left", padx=(0, 5))
        self.query.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.query.bind("<Return>", lambda event: self.refresh(reset=True))
        ttk.Button(header, text="Buscar", command=lambda: self.refresh(reset=True)).pack(side="left")
        ttk.Button(header, text="Filtros...", command=self.edit_filters).pack(side="left", padx=5)
        ttk.Button(header, text="Limpar", command=self.clear_filters).pack(side="left")
        ttk.Button(header, text="Importar JSON...", command=self.import_file).pack(side="right", padx=(5, 0))

        self.indicators = ttk.Label(self, text="Indicadores dos resultados filtrados", style="Subtitle.TLabel",
                                    wraplength=735)
        self.indicators.pack(anchor="w", pady=(0, 6))
        toolbar = ttk.Frame(self)
        toolbar.pack(side="bottom", fill="x", pady=(5, 0))
        self.status = tk.StringVar(value="Abra o Histórico para carregar os registros.")
        ttk.Label(toolbar, textvariable=self.status, wraplength=735, style="Subtitle.TLabel").pack(fill="x")
        actions = ttk.Frame(toolbar)
        actions.pack(fill="x", pady=(4, 0))
        self.rerun_button = ttk.Button(actions, text="Reexecutar teste",
                                       command=lambda: self.retrieve(self.rerun_single))
        self.enqueue_button = ttk.Button(actions, text="Enviar à fila",
                                        command=lambda: self.retrieve(self.enqueue_records))
        self.compare_button = ttk.Button(actions, text="Comparar", command=lambda: self.retrieve(self.compare))
        self.export_button = ttk.Menubutton(actions, text="Exportar")
        menu = tk.Menu(self.export_button, tearoff=False)
        for label, fmt in (("JSON", "json"), ("Markdown", "md"), ("CSV", "csv")):
            menu.add_command(label=label, command=lambda fmt=fmt: self.export(fmt))
        self.export_button.configure(menu=menu)
        self.delete_button = ttk.Button(actions, text="Excluir", command=self.delete)
        for button in (self.rerun_button, self.enqueue_button, self.compare_button, self.export_button,
                       self.delete_button):
            button.pack(side="left", padx=(0, 5))

        split = ttk.Panedwindow(self, orient="horizontal")
        split.pack(fill="both", expand=True)
        left = ttk.Frame(split, padding=(0, 0, 7, 0))
        self.detail = HistoryDetail(split)
        split.add(left, weight=3)
        split.add(self.detail, weight=2)
        order = ttk.Frame(left)
        order.pack(fill="x", pady=(0, 4))
        ttk.Label(order, text="Ordenar por").pack(side="left", padx=(0, 5))
        self.orders = {"Data": "date", "Duração": "duration", "Interações": "interactions", "Tokens": "tokens"}
        self.sort = ttk.Combobox(order, state="readonly", values=tuple(self.orders), width=11)
        self.sort.set("Data")
        self.sort.pack(side="left")
        self.sort.bind("<<ComboboxSelected>>", lambda event: self.refresh(reset=True))
        self.descending = tk.BooleanVar(value=True)
        ttk.Checkbutton(order, text="Decrescente", variable=self.descending,
                        command=lambda: self.refresh(reset=True)).pack(side="left", padx=6)

        navigation = ttk.Frame(left)
        navigation.pack(side="bottom", fill="x", pady=(4, 0))
        self.prev = ttk.Button(navigation, text="Anterior", command=lambda: self.go(-1))
        self.prev.pack(side="left")
        self.pagination = ttk.Label(navigation, text="1 / 1")
        self.pagination.pack(side="left", padx=8)
        self.next = ttk.Button(navigation, text="Próxima", command=lambda: self.go(1))
        self.next.pack(side="left")
        self.prev.state(["disabled"])
        self.next.state(["disabled"])

        items = ttk.Frame(left, width=360, height=180)
        items.pack(fill="both", expand=True)
        items.pack_propagate(False)
        self.listing = ttk.Treeview(items, columns=("title", "date", "status", "product", "model"),
                                    show="headings", selectmode="extended", height=5)
        columns = (("title", "Título / ID", 150), ("date", "Início local", 158),
                   ("status", "Status", 100), ("product", "Produto", 110), ("model", "Modelo", 110))
        for column, label, width in columns:
            self.listing.heading(column, text=label)
            self.listing.column(column, width=width, minwidth=70, stretch=column == "title")
        xscroll = ttk.Scrollbar(items, orient="horizontal", command=self.listing.xview)
        xscroll.pack(side="bottom", fill="x")
        yscroll = ttk.Scrollbar(items, orient="vertical", command=self.listing.yview)
        yscroll.pack(side="right", fill="y")
        self.listing.pack(fill="both", expand=True)
        self.listing.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.listing.bind("<<TreeviewSelect>>", self.selection_changed)
        self.actions_for(())
        self.timer = self.after(80, self.poll)

    def destroy(self):
        self.after_cancel(self.timer)
        self.pool.shutdown(wait=False, cancel_futures=True)
        super().destroy()

    def apply_palette(self, palette):
        self.palette = palette
        self.detail.apply_palette(palette)

    def submit(self, operation, callback, *, valid=lambda: True):
        def perform():
            try:
                self.completed.put((callback, operation(), None, valid))
            except (OSError, TypeError, ValueError, sqlite3.Error) as error:
                detail = "Falha de acesso ao histórico local."
                if isinstance(error, ValueError) and not isinstance(error, json.JSONDecodeError):
                    detail = str(error)
                self.completed.put((callback, None, detail, valid))
        self.pool.submit(perform)

    def poll(self):
        try:
            while True:
                callback, value, error, valid = self.completed.get_nowait()
                if valid():
                    if error:
                        self.status.set(error)
                    else:
                        callback(value)
        except Empty:
            self.timer = self.after(80, self.poll)

    def refresh(self, *, reset=False):
        if not self.history:
            self.status.set("O SQLite local está indisponível.")
            return
        if reset:
            self.page = 0
        self.generation += 1
        generation = self.generation
        args = (self.query.get(), self.filters.copy())
        options = {"page": self.page, "order": self.orders[self.sort.get()], "descending": self.descending.get()}
        self.status.set("Consultando resultados...")
        self.submit(lambda: self.history.page(*args, **options), self.show_page,
                    valid=lambda: self.generation == generation)

    def show_page(self, result):
        previous = self.listing.selection()
        self.listing.delete(*self.listing.get_children())
        self.page, self.pages = result.page, result.pages
        for row in result.rows:
            self.listing.insert("", "end", iid=row["id"], values=(
                row["title"] or row["id"][:12], display_time(row["started_at"]).rsplit(" ", 1)[0],
                STATUS.get(row["status"], UNKNOWN), row["product"] or "-", row["provider"].get("model") or "-",
            ))
        retained = [identifier for identifier in previous if self.listing.exists(identifier)]
        if retained:
            self.listing.selection_set(retained)
        else:
            self.selection_changed()
        stats = result.totals
        self.pagination.configure(text=f"{self.page + 1} / {self.pages}")
        self.prev.state(["!disabled" if self.page > 0 else "disabled"])
        self.next.state(["!disabled" if self.page + 1 < self.pages else "disabled"])
        self.indicators.configure(text=(
            f"Resultados filtrados · total {stats['total']} · conclusões {stats['completed']} · "
            f"interrupções {stats['interrupted']} · erros {stats['errors']} · loops {stats['loops']} · "
            f"{stats['limited']} limites de API\n"
            f"Requisições registradas {known(stats['requests'])} · tokens medidos {known(stats['known_tokens'])} · "
            f"duração média {seconds(stats['average_duration'])}"
        ))
        self.status.set("Ctrl ou Shift para selecionar vários testes." if result.rows else "Nenhum teste encontrado.")

    def actions_for(self, selection):
        size = len(selection)
        enabled = (size == 1 and not self.running(), size >= 1 and not self.running(),
                   size == 2, size >= 1, size >= 1)
        for button, active in zip((self.rerun_button, self.enqueue_button, self.compare_button,
                                   self.export_button, self.delete_button), enabled):
            button.state(["!disabled" if self.history and active else "disabled"])

    def selection_changed(self, event=None):
        selection = self.listing.selection()
        self.actions_for(selection)
        self.detail_generation += 1
        generation = self.detail_generation
        self.last_record = None
        if not selection:
            self.detail.clear()
            return

        def show(records):
            if records:
                self.last_record = records[0]
                self.detail.show(records[0])
        self.submit(lambda: self.history.details(selection[:1]), show,
                    valid=lambda: self.detail_generation == generation)

    def retrieve(self, callback):
        selection = self.listing.selection()
        if selection and self.history:
            self.submit(lambda: self.history.details(selection), callback)

    def rerun_single(self, records):
        if len(records) == 1 and not self.running():
            self.rerun(records[0])

    def enqueue_records(self, records):
        if records and not self.running():
            self.enqueue(records)

    def go(self, direction):
        self.page += direction
        self.refresh()

    def clear_filters(self):
        self.filters.clear()
        self.query.delete(0, "end")
        self.refresh(reset=True)

    def edit_filters(self):
        dialog = tk.Toplevel(self)
        dialog.title("Filtros do Histórico")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        labels = {"from": "Início a partir de", "to": "Início até", "product": "Produto", "provider": "Provedor",
                  "model": "Modelo", "status": "Status", "execution_type": "Tipo de execução",
                  "version": "Versão", "batch": "ID do lote"}
        entries = {}
        for row, (key, label) in enumerate(labels.items()):
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=2)
            choices = STATUS if key == "status" else TYPES if key == "execution_type" else None
            value = self.filters.get(key, "")
            field = ttk.Combobox(dialog, values=("", *choices.values()), state="readonly") if choices else ttk.Entry(dialog)
            if choices:
                field.set(choices.get(value, ""))
            else:
                field.insert(0, value)
            field.grid(row=row, column=1, sticky="ew", padx=8, pady=2)
            entries[key] = field
        ttk.Label(dialog, text="Datas locais AAAA-MM-DD, com HH:MM opcional. Campos de texto usam valor exato.",
                  wraplength=335).grid(row=len(labels), columnspan=2, padx=8, pady=4)

        def apply():
            filters = {key: entry.get().strip() for key, entry in entries.items() if entry.get().strip()}
            for key, choices in (("status", STATUS), ("execution_type", TYPES)):
                if key in filters:
                    filters[key] = next(code for code, label in choices.items() if label == filters[key])
            self.filters = filters
            dialog.destroy()
            self.refresh(reset=True)
        ttk.Button(dialog, text="Aplicar filtros", command=apply, style="Primary.TButton").grid(
            row=len(labels) + 1, column=1, sticky="e", padx=8, pady=8)

    def import_file(self):
        if not self.history:
            return
        source = filedialog.askopenfilename(parent=self, title="Importar relatório JSON",
                                            filetypes=[("Relatório JSON", "*.json")])
        if not source:
            return

        def operation():
            with Path(source).open(encoding="utf-8-sig") as stream:
                try:
                    return import_reports(self.history.store, json.load(stream))
                except json.JSONDecodeError:
                    raise ValueError("O arquivo selecionado não contém JSON válido.") from None

        def show(result):
            self.refresh(reset=True)
            messagebox.showinfo("Importação concluída", f"{result.imported} importados, "
                                f"{result.duplicates} duplicados, {result.invalid} inválidos.", parent=self)
        self.submit(operation, show)

    def export(self, format):
        selection = self.listing.selection()
        if not selection or not self.history:
            return
        filename = filedialog.asksaveasfilename(parent=self, title="Exportar testes selecionados",
                                               initialfile=f"chatprobe-historico-{uuid4().hex[:8]}.{format}",
                                               defaultextension=f".{format}",
                                               filetypes=[(format.upper(), f"*.{format}")],
                                               confirmoverwrite=True)
        if not filename:
            return
        target = Path(filename)
        overwrite = target.exists()

        def operation():
            records = self.history.details(selection)
            if not records:
                raise ValueError("Os testes selecionados já não estão disponíveis.")
            content = export_records(records, format)
            if overwrite:
                write_text_atomically(target, content)
            else:
                with target.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(content)
            return len(records)
        self.submit(operation, lambda count: self.status.set(f"{count} teste(s) exportado(s)."))

    def delete(self):
        selection = self.listing.selection()
        if not selection or not self.history:
            return
        if not messagebox.askyesno("Excluir do Histórico", f"Excluir {len(selection)} teste(s) do banco local? "
                                   "Os relatórios exportados serão preservados.", parent=self):
            return

        def done(count):
            self.refresh()
            messagebox.showinfo("Histórico", f"{count} teste(s) excluído(s).", parent=self)
        self.submit(lambda: self.history.delete(selection), done)

    def compare(self, records):
        if len(records) != 2:
            return
        popup = tk.Toplevel(self)
        popup.title("Comparar testes")
        popup.geometry(f"{min(1180, popup.winfo_screenwidth()-90)}x{min(750, popup.winfo_screenheight()-100)}")
        if self.palette:
            popup.configure(background=self.palette["canvas"])
        differences, unknown = comparison_differences(*records)
        summary = "Condições diferentes · " + (", ".join(differences) or "nenhuma diferença conhecida")
        if unknown:
            summary += "\nCondições sem dados completos · " + ", ".join(unknown)
        ttk.Label(popup, text=summary, wraplength=720).pack(anchor="w", padx=10, pady=8)
        panes = ttk.Panedwindow(popup, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        for record in records:
            detail = HistoryDetail(panes)
            panes.add(detail, weight=1)
            if self.palette:
                detail.apply_palette(self.palette)
            detail.show(record)
