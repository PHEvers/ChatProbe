"""Janela nativa para executar roteiros do ChatProbe."""

import json
import os
import sys
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from src.chatprobe.browser import smoke
from src.chatprobe.desktop_keys import KeysTab
from src.chatprobe.runner import batch
from src.chatprobe.windows_credentials import WindowsCredentials


CHAT_URL = "https://suportelinx--uat.sandbox.my.site.com/CentralCliente/"
RESOURCE_ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
EXAMPLE = RESOURCE_ROOT / "examples/prompts/cupom-nao-impresso.json"
JSON_MODE = "Roteiro JSON"
GROQ_MODE = "Groq"
STATUS_LABELS = {
    "completed": "Concluído", "interrupted": "Roteiro interrompido",
    "error": "Erro de execução", "rate_limited": "Limite da Groq", "paused": "Pausado",
}


class ChatProbeWindow:
    def __init__(self, root):
        self.root = root
        self.events = Queue()
        self.running = False
        self.report = None
        self.results = {}
        self.total = 1
        self.mode = JSON_MODE
        self.credentials = WindowsCredentials() if sys.platform == "win32" else None
        self.drafts = {
            JSON_MODE: EXAMPLE.read_text(encoding="utf-8"),
            GROQ_MODE: (
                "Simule um cliente do AutoSystem que finalizou uma venda, mas o cupom "
                "não saiu. Informe que já reiniciou a impressora se o chatbot perguntar. "
                "Encerre quando ele explicar como conferir a configuração da impressora."
            ),
        }
        root.title("ChatProbe")
        root.geometry("900x640")
        root.minsize(590, 470)
        root.protocol("WM_DELETE_WINDOW", self.close)

        content = ttk.Frame(root, padding=20)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="ChatProbe", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(content, text="AutoSystem · Homologação").pack(anchor="w", pady=(0, 16))

        tabs = ttk.Notebook(content)
        tabs.pack(fill="both", expand=True)
        test_tab = ttk.Frame(tabs, padding=12)
        keys_tab = ttk.Frame(tabs, padding=12)
        tabs.add(test_tab, text="Teste")
        tabs.add(keys_tab, text="Chaves de API")

        settings = ttk.Frame(test_tab)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="Modo").grid(row=0, column=0, padx=(0, 10), sticky="w")
        modes = (JSON_MODE, GROQ_MODE) if self.credentials else (JSON_MODE,)
        self.mode_field = ttk.Combobox(settings, values=modes, state="readonly", width=14)
        self.mode_field.current(0)
        self.mode_field.grid(row=0, column=1, sticky="w")
        self.mode_field.bind("<<ComboboxSelected>>", self.change_mode)
        ttk.Label(settings, text="Quantidade de testes").grid(
            row=0, column=2, padx=(16, 6), sticky="w",
        )
        self.total_field = ttk.Entry(settings, width=5)
        self.total_field.insert(0, "1")
        self.total_field.grid(row=0, column=3, sticky="w")
        ttk.Label(settings, text="Testes simultâneos").grid(
            row=0, column=4, padx=(12, 6), sticky="w",
        )
        self.parallel_field = ttk.Entry(settings, width=5)
        self.parallel_field.insert(0, "1")
        self.parallel_field.grid(row=0, column=5, sticky="w")
        self.groq_key_label = ttk.Label(settings, text="Chave Groq")
        self.groq_key = ttk.Combobox(settings, state="readonly", width=26,
                                    postcommand=self.refresh_test_keys)
        self.groq_notice = ttk.Label(
            settings, text="O objetivo e as respostas do chatbot durante o teste são enviados à Groq.",
            wraplength=490,
        )

        panels = ttk.Panedwindow(test_tab, orient="vertical")
        panels.pack(fill="both", expand=True)
        self.editor = ttk.Labelframe(panels, text=JSON_MODE, padding=8)
        results = ttk.Labelframe(panels, text="Conversa", padding=8)
        panels.add(self.editor, weight=1)
        panels.add(results, weight=2)

        self.scenario = ScrolledText(self.editor, wrap="word", height=5)
        self.scenario.pack(fill="both", expand=True)
        self.scenario.insert("1.0", self.drafts[self.mode])

        self.result_field = ttk.Combobox(results, state="disabled", width=24)
        self.result_field.pack(anchor="w", pady=(0, 5))
        self.result_field.bind("<<ComboboxSelected>>", self.show_result)
        self.conversation = ScrolledText(results, wrap="word", height=6, state="disabled")
        self.conversation.pack(fill="both", expand=True)

        actions = ttk.Frame(test_tab)
        actions.pack(fill="x", pady=(12, 0))
        self.run_button = ttk.Button(actions, text="Executar teste", command=self.start)
        self.run_button.pack(side="left")
        self.save_button = ttk.Button(actions, text="Salvar JSON", command=self.save,
                                      state="disabled")
        self.save_button.pack(side="left", padx=8)
        self.status = tk.StringVar(value="Pronto para executar.")
        self.warning = tk.StringVar()
        ttk.Label(test_tab, textvariable=self.status, wraplength=540).pack(
            anchor="w", pady=(6, 0)
        )
        self.warning_label = ttk.Label(test_tab, textvariable=self.warning, wraplength=540,
                                       foreground="#9c4100")
        if self.credentials:
            self.keys_tab = KeysTab(keys_tab, self.credentials, self.refresh_test_keys)
        else:
            ttk.Label(keys_tab, text="Abra o ChatProbe no Windows para gerenciar suas chaves.").pack(
                anchor="w"
            )

    def change_mode(self, event):
        self.drafts[self.mode] = self.scenario.get("1.0", "end-1c")
        self.mode = self.mode_field.get()
        self.scenario.delete("1.0", "end")
        self.scenario.insert("1.0", self.drafts[self.mode])
        if self.mode == GROQ_MODE:
            self.editor.configure(text="Objetivo do teste")
            self.groq_key_label.grid(row=1, column=0, pady=(5, 0), sticky="w")
            self.groq_key.grid(row=1, column=1, columnspan=3, pady=(5, 0), sticky="w")
            self.groq_notice.grid(row=2, column=0, columnspan=6, sticky="w")
            self.refresh_test_keys()
        else:
            self.editor.configure(text=JSON_MODE)
            for widget in (self.groq_key_label, self.groq_key, self.groq_notice):
                widget.grid_remove()

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

    def start(self):
        try:
            total = int(self.total_field.get())
            parallel = int(self.parallel_field.get())
            if not 1 <= parallel <= total:
                raise ValueError
        except ValueError:
            self.status.set("Informe pelo menos 1 teste e um número de simultâneos entre 1 e o total.")
            return

        text = self.scenario.get("1.0", "end-1c").strip()
        scenario = key = None
        if self.mode == GROQ_MODE:
            if not text:
                self.status.set("Descreva o objetivo do teste.")
                return
            name = self.groq_key.get()
            if not name:
                self.status.set("Salve uma chave em Chaves de API e selecione-a para o teste.")
                return
            try:
                key = self.credentials.get("groq", name)
            except (OSError, UnicodeError):
                self.status.set("Não foi possível ler a chave selecionada.")
                return
            if not key:
                self.refresh_test_keys()
                self.status.set("A chave selecionada não está mais salva. Selecione outra.")
                return
        else:
            try:
                scenario = smoke.parse_scenario(json.loads(text))
            except ValueError as error:
                self.status.set(f"Roteiro inválido. {error}")
                return

        self.running = True
        self.report = None
        self.results.clear()
        self.total = total
        self.parallel = parallel
        self.result_field.configure(state="disabled", values=())
        self.result_field.set("")
        for widget in (self.run_button, self.save_button, self.mode_field, self.groq_key,
                       self.total_field, self.parallel_field):
            widget.state(["disabled"])
        self.set_conversation("")
        self.warning.set("")
        self.warning_label.pack_forget()
        self.status.set(f"Iniciando {total} teste(s).")
        Thread(
            target=batch.run_batch, args=(CHAT_URL, total, parallel, self.events),
            kwargs={"scenario": scenario, "objective": text if key else "", "key": key},
        ).start()
        self.root.after(100, self.poll)

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "progress":
                    number, message = value
                    self.status.set(f"Teste {number}/{self.total} — {message}")
                elif kind == "limit":
                    if not self.warning.get():
                        self.warning.set(f"{value} Nenhum novo teste será iniciado neste lote.")
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
            self.root.after(100, self.poll)

    def add_result(self, result):
        self.results[result["test"]] = result
        options = [f"Teste {number}" for number in sorted(self.results)]
        self.result_field.configure(state="readonly", values=options)
        if not self.result_field.get():
            self.result_field.set(options[0])
            self.show_result()
        self.status.set(f"Resultados disponíveis para {len(self.results)} de {self.total} teste(s).")

    def finish(self):
        self.running = False
        for widget in (self.run_button, self.mode_field, self.groq_key,
                       self.total_field, self.parallel_field):
            widget.state(["!disabled"])
        results = [self.results[number] for number in sorted(self.results)]
        completed = sum(result["status"] == "completed" for result in results)
        pending = self.total - len(results)
        self.status.set(
            f"Lote encerrado. {completed}/{self.total} concluído(s). "
            f"{len(results) - completed} interrompido(s) ou com erro. {pending} não iniciado(s)."
        )
        if not results:
            return
        if self.mode == JSON_MODE and self.total == 1 and results[0]["status"] != "error":
            report = results[0]["turns"]
        else:
            report = {"total": self.total, "parallel": self.parallel, "not_started": pending,
                      "tests": results}
        self.report = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        self.save_button.state(["!disabled"])

    def show_result(self, event=None):
        number = int(self.result_field.get().split()[-1])
        result = self.results[number]
        header = STATUS_LABELS[result["status"]]
        if result["detail"]:
            header += f"\n{result['detail']}"
        turns = [
            f"Cliente\n{turn['message']}\n\nBot\n" + "\n".join(turn["replies"])
            for turn in result["turns"]
        ]
        self.set_conversation("\n\n".join([header, *turns]))

    def set_conversation(self, text):
        self.conversation.configure(state="normal")
        self.conversation.delete("1.0", "end")
        self.conversation.insert("1.0", text)
        self.conversation.configure(state="disabled")

    def save(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, initialfile="chatprobe.json", defaultextension=".json",
            filetypes=[("Arquivo JSON", "*.json")],
        )
        if path:
            try:
                Path(path).write_text(self.report, encoding="utf-8")
            except OSError as error:
                messagebox.showerror("Não foi possível salvar o JSON", str(error), parent=self.root)

    def close(self):
        if self.running:
            messagebox.showinfo("Teste em andamento", "Aguarde o teste terminar.",
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
