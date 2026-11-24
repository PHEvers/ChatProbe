"""Editor da fila de prompts para testes noturnos."""

from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


class NightQueue(ttk.Frame):
    def __init__(self, parent, prompts, save_prompts, set_status):
        super().__init__(parent)
        self.prompts = prompts
        self.save_prompts = save_prompts
        self.set_status = set_status
        self.editing = None
        self.running = False
        self.columnconfigure(0, weight=2)
        self.columnconfigure(1, weight=3)
        self.rowconfigure(0, weight=1)

        queue = ttk.Labelframe(self, text="Fila de prompts", padding=8)
        queue.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        queue.columnconfigure(0, weight=1)
        queue.rowconfigure(0, weight=1)
        self.items = ttk.Treeview(queue, show="tree", selectmode="browse", height=6)
        self.items.column("#0", width=220, stretch=True)
        self.items.grid(row=0, column=0, sticky="nsew")
        self.items.bind("<<TreeviewSelect>>", self.select_prompt)
        scrollbar = ttk.Scrollbar(queue, orient="vertical", command=self.items.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.items.configure(yscrollcommand=scrollbar.set)
        self.remove_button = ttk.Button(queue, text="Remover selecionado", command=self.remove_prompt)
        self.remove_button.grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.form = ttk.Labelframe(self, text="Prompt inicial", padding=8)
        self.form.grid(row=0, column=1, sticky="nsew")
        self.editor = ScrolledText(self.form, wrap="word", width=30, height=6, padx=8, pady=6,
                                   font=("Segoe UI", 10))
        self.editor.pack(fill="both", expand=True)
        actions = ttk.Frame(self.form)
        actions.pack(fill="x", pady=(8, 0))
        self.save_button = ttk.Button(actions, command=self.save_prompt, style="Primary.TButton")
        self.save_button.pack(side="left")
        self.new_button = ttk.Button(actions, text="Novo prompt", command=self.new_prompt)
        self.new_button.pack(side="left", padx=(8, 0))
        self.refresh()
        self.new_prompt(focus=False)

    def refresh(self):
        self.items.delete(*self.items.get_children())
        for index, prompt in enumerate(self.prompts):
            preview = " ".join(prompt.split())
            if len(preview) > 100:
                preview = preview[:99] + "…"
            self.items.insert("", "end", iid=str(index), text=f"{index + 1}. {preview}")

    def select_prompt(self, event=None):
        selection = self.items.selection()
        if selection and not self.running:
            index = int(selection[0])
            if self.editing != index:
                self.editing = index
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", self.prompts[index])
            self.save_button.configure(text="Salvar alterações")
            self.remove_button.state(["!disabled"])

    def new_prompt(self, focus=True):
        self.items.selection_remove(*self.items.selection())
        self.editing = None
        self.editor.delete("1.0", "end")
        self.save_button.configure(text="Adicionar à fila")
        self.remove_button.state(["disabled"])
        if focus:
            self.editor.focus_set()

    def save_prompt(self):
        text = self.editor.get("1.0", "end-1c").strip()
        if not text:
            self.set_status("Escreva um prompt para adicionar à fila.")
            return False
        prompts = list(self.prompts)
        if self.editing is None:
            prompts.append(text)
        else:
            prompts[self.editing] = text
        if not self.save_prompts(tuple(prompts)):
            return False
        self.prompts = tuple(prompts)
        self.refresh()
        if self.editing is None:
            self.new_prompt()
        else:
            self.items.selection_set(str(self.editing))
        self.set_status(f"{len(self.prompts)} prompt(s) na fila.")
        return True

    def remove_prompt(self):
        selection = self.items.selection()
        if selection:
            index = int(selection[0])
            prompts = self.prompts[:index] + self.prompts[index + 1:]
            if self.save_prompts(prompts):
                self.prompts = prompts
                self.refresh()
                self.new_prompt()
                self.set_status(f"{len(prompts)} prompt(s) na fila.")

    def commit_draft(self):
        text = self.editor.get("1.0", "end-1c").strip()
        saved = self.prompts[self.editing] if self.editing is not None else ""
        return text == saved or self.save_prompt()

    def set_running(self, running):
        self.running = running
        self.editor.configure(state="disabled" if running else "normal")
        self.items.configure(selectmode="none" if running else "browse")
        for widget in (self.save_button, self.new_button):
            widget.state(["disabled" if running else "!disabled"])
        self.remove_button.state(["disabled" if running or self.editing is None else "!disabled"])
