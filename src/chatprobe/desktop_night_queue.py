"""Editor e ordem dos prompts da fila de testes."""

from tkinter import messagebox, ttk

from src.chatprobe.desktop_prompt import PromptEditor


def prompt_preview(prompt, title="", limit=75):
    preview = " ".join((title or prompt).split())
    return preview if len(preview) <= limit else preview[:limit - 1] + "..."


class NightQueue(ttk.Frame):
    def __init__(self, parent, prompts, save_prompts, set_status, *, titles=(), origins=()):
        super().__init__(parent)
        self.prompts = prompts
        self.titles = tuple(titles[index] if index < len(titles) else "" for index in range(len(prompts)))
        self.origins = tuple(origins[index] if index < len(origins) else None for index in range(len(prompts)))
        self.save_prompts = save_prompts
        self.set_status = set_status
        self.editing = None
        self.running = False
        self.columnconfigure(0, weight=2, uniform="queue")
        self.columnconfigure(1, weight=3, uniform="queue")
        self.rowconfigure(0, weight=1)

        queue = ttk.Labelframe(self, text="Prompts na fila", padding=10)
        queue.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        queue.columnconfigure(0, weight=1)
        queue.rowconfigure(0, weight=1)
        self.items = ttk.Treeview(queue, show="tree", selectmode="browse", height=5)
        self.items.column("#0", width=120, minwidth=30, stretch=True)
        self.items.grid(row=0, column=0, sticky="nsew")
        self.items.bind("<<TreeviewSelect>>", self.select_prompt)
        self.items.bind("<Alt-Up>", lambda event: self.move_prompt(-1))
        self.items.bind("<Alt-Down>", lambda event: self.move_prompt(1))
        scrollbar = ttk.Scrollbar(queue, orient="vertical", command=self.items.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.items.configure(yscrollcommand=scrollbar.set)
        actions = ttk.Frame(queue)
        actions.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.up = ttk.Button(actions, text="Subir", command=lambda: self.move_prompt(-1))
        self.down = ttk.Button(actions, text="Descer", command=lambda: self.move_prompt(1))
        self.remove_button = ttk.Button(actions, text="Excluir", command=self.remove_prompt)
        for button in (self.up, self.down, self.remove_button):
            button.pack(side="left", padx=(0, 4))

        self.form = ttk.Labelframe(self, text="Prompt inicial", padding=10)
        self.form.grid(row=0, column=1, sticky="nsew")
        self.prompt_editor = PromptEditor(self.form)
        self.prompt_editor.pack(fill="both", expand=True)
        self.editor = self.prompt_editor.text
        actions = ttk.Frame(self.form)
        actions.pack(side="bottom", fill="x", pady=(8, 0), before=self.prompt_editor)
        self.save_button = ttk.Button(actions, command=self.save_prompt, style="Primary.TButton")
        self.save_button.pack(side="left")
        self.new_button = ttk.Button(actions, text="Novo prompt", command=self.new_prompt)
        self.new_button.pack(side="left", padx=(8, 0))
        self.refresh()
        self.load_prompt(None)

    def refresh(self):
        self.items.delete(*self.items.get_children())
        for index, (prompt, title) in enumerate(zip(self.prompts, self.titles)):
            self.items.insert("", "end", iid=str(index), text=f"{index + 1}. {prompt_preview(prompt, title)}")

    def load_prompt(self, index):
        self.editing = index
        if index is None:
            self.items.selection_remove(*self.items.selection())
            self.prompt_editor.set_value("")
        else:
            self.items.selection_set(str(index))
            self.items.see(str(index))
            self.prompt_editor.set_value(self.prompts[index], self.titles[index])
        self.save_button.configure(text="Adicionar à fila" if index is None else "Salvar alterações")
        self.select_actions()

    def select_actions(self):
        index = self.editing
        for widget, enabled in ((self.up, index is not None and index > 0),
                                (self.down, index is not None and index + 1 < len(self.prompts)),
                                (self.remove_button, index is not None)):
            widget.state(["!disabled" if enabled and not self.running else "disabled"])

    def has_draft(self):
        text, title = self.prompt_editor.value(), self.prompt_editor.title_var.get().strip()
        return (text, title) != ((self.prompts[self.editing], self.titles[self.editing])
                                 if self.editing is not None else ("", ""))

    def discard_draft(self):
        return not self.has_draft() or messagebox.askyesno(
            "Alterações no prompt", "Descartar as alterações ainda não salvas?", parent=self,
        )

    def select_prompt(self, event=None):
        selection = self.items.selection()
        if not selection or self.running:
            return
        index = int(selection[0])
        if self.editing == index:
            return
        if self.discard_draft():
            self.load_prompt(index)
        elif self.editing is None:
            self.items.selection_remove(*selection)
        else:
            self.items.selection_set(str(self.editing))

    def new_prompt(self, focus=True):
        if self.discard_draft():
            self.load_prompt(None)
            if focus:
                self.editor.focus_set()

    def persist(self, prompts, titles, origins):
        if not self.save_prompts(tuple(prompts), tuple(titles), tuple(origins)):
            return False
        self.prompts, self.titles, self.origins = tuple(prompts), tuple(titles), tuple(origins)
        self.refresh()
        return True

    def save_prompt(self):
        text = self.prompt_editor.value()
        title = self.prompt_editor.title_var.get().strip()
        if not text.strip():
            self.set_status("Escreva um prompt para adicionar à fila.")
            return False
        prompts, titles, origins = list(self.prompts), list(self.titles), list(self.origins)
        index = self.editing
        if index is None:
            prompts.append(text)
            titles.append(title)
            origins.append(None)
        else:
            if isinstance(origins[index], dict) and prompts[index] != text:
                origins[index] = dict(origins[index], library_prompt_version=None)
            prompts[index], titles[index] = text, title
        if not self.persist(prompts, titles, origins):
            return False
        self.load_prompt(index)
        self.set_status(f"{len(self.prompts)} prompt(s) na fila.")
        return True

    def remove_prompt(self):
        index = self.editing
        if index is not None and not self.running and messagebox.askyesno(
            "Excluir prompt", "Excluir o prompt selecionado da fila?", parent=self,
        ):
            if self.persist(self.prompts[:index] + self.prompts[index + 1:],
                            self.titles[:index] + self.titles[index + 1:],
                            self.origins[:index] + self.origins[index + 1:]):
                self.load_prompt(None)

    def move_prompt(self, delta):
        index = self.editing
        if self.running or index is None or not 0 <= index + delta < len(self.prompts):
            return "break"
        if self.has_draft() and not self.save_prompt():
            return "break"
        other = index + delta
        prompts, titles, origins = list(self.prompts), list(self.titles), list(self.origins)
        prompts[index], prompts[other] = prompts[other], prompts[index]
        titles[index], titles[other] = titles[other], titles[index]
        origins[index], origins[other] = origins[other], origins[index]
        if self.persist(prompts, titles, origins):
            self.load_prompt(other)
        return "break"

    def append_history(self, records):
        if not self.commit_draft():
            return False
        first = len(self.prompts)
        if self.persist(self.prompts + tuple(item["prompt_text"] for item in records),
                        self.titles + tuple(item.get("title") or "" for item in records),
                        self.origins + tuple(item["id"] for item in records)):
            self.load_prompt(first)
            return True
        return False

    def commit_draft(self):
        return not self.has_draft() or self.save_prompt()

    def set_running(self, running):
        self.running = running
        self.prompt_editor.set_enabled(not running)
        self.items.configure(selectmode="none" if running else "browse")
        for widget in (self.save_button, self.new_button):
            widget.state(["disabled" if running else "!disabled"])
        self.select_actions()
