"""Interface da Biblioteca local."""

import json
import sqlite3
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from uuid import uuid4

from src.chatprobe.desktop_preferences import write_text_atomically
from src.chatprobe.desktop_prompt import PromptEditor
from src.chatprobe.storage.library import Prompt, parse_import


class LibraryView(ttk.Frame):
    def __init__(self, parent, library, open_test, enqueue, history):
        super().__init__(parent)
        self.library, self.open_test, self.enqueue, self.history = library, open_test, enqueue, history
        self.rows, self.groups = {}, {}
        self.identifier = None
        self.selected = set()
        self.status = tk.StringVar()
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.group = ttk.Combobox(bar, width=22, state="readonly")
        self.group.pack(side="left")
        self.group.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        self.query = ttk.Entry(bar)
        self.query.pack(side="left", fill="x", expand=True, padx=5)
        self.query.bind("<Return>", lambda event: self.refresh())
        ttk.Button(bar, text="Buscar", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Coleções...", command=self.manage_collection).pack(side="left")
        ttk.Button(bar, text="Importar...", command=self.import_file).pack(side="left")
        ttk.Label(self, textvariable=self.status).pack(side="bottom", anchor="w", pady=3)
        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=6)
        list_panel, edit = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(list_panel, weight=1)
        panes.add(edit, weight=2)
        self.tree = ttk.Treeview(list_panel, show="tree", selectmode="extended")
        self.tree.column("#0", width=210)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.load)
        buttons = ttk.Frame(list_panel)
        buttons.pack(fill="x")
        for label, command in (("Novo", self.new), ("Excluir", self.delete), ("Exportar", self.export)):
            ttk.Button(buttons, text=label, command=command).pack(side="left")
        self.editor = PromptEditor(edit)
        self.editor.pack(fill="both", expand=True)
        self.fields = {}
        for label, name in (("Descrição", "description"), ("Resultado esperado", "expected"), ("Tags", "tags"),
                            ("Produto", "product")):
            row = ttk.Frame(edit)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=17).pack(side="left")
            self.fields[name] = ttk.Entry(row)
            self.fields[name].pack(fill="x", expand=True)
        options = ttk.Frame(edit)
        options.pack(fill="x")
        self.mode = ttk.Combobox(options, values=("ai", "json"), state="readonly", width=7)
        self.mode.set("ai")
        self.mode.pack(side="left")
        self.favorite = tk.BooleanVar()
        ttk.Checkbutton(options, text="Favorito", variable=self.favorite).pack(side="left")
        actions = ttk.Frame(edit)
        actions.pack(fill="x")
        for label, callback in (("Salvar", self.save), ("Duplicar", lambda: self.save(copy=True)),
                                ("Versões", self.versions), ("Abrir no Teste", self.run),
                                ("Enviar à fila", self.send), ("Histórico", self.show_history)):
            ttk.Button(actions, text=label, command=callback).pack(side="left")
        self.refresh()

    def report(self, operation):
        try:
            return operation()
        except (sqlite3.Error, ValueError, TypeError, OSError) as error:
            self.status.set(str(error) if isinstance(error, ValueError) else "Operação local indisponível.")
            return None

    def refresh(self):
        if not self.library:
            self.status.set("SQLite indisponível.")
            return
        def read():
            current = self.group.get()
            self.groups = {"Todas as coleções": None}
            self.groups.update({f"{name} · {count} [{key[:6]}]": key
                                for key, name, _, count in self.library.collections()})
            self.group.configure(values=tuple(self.groups))
            self.group.set(current if current in self.groups else "Todas as coleções")
            self.rows = {str(row[0]): row for row in self.library.prompts(
                self.groups[self.group.get()], self.query.get())}
            self.tree.delete(*self.tree.get_children())
            for key, (_, _, prompt, version) in self.rows.items():
                self.tree.insert("", "end", iid=key, text=f"{'★ ' if prompt.favorite else ''}{prompt.title} · v{version}")
            self.tree.selection_set(self.selected.intersection(self.rows))
        self.report(read)

    def current_rows(self):
        return [self.rows[key] for key in self.tree.selection()]

    def load(self, event=None):
        self.selected.difference_update(self.rows)
        self.selected.update(self.tree.selection())
        self.status.set(f"{len(self.selected)} selecionado(s)")
        rows = self.current_rows()
        if len(rows) == 1:
            self.identifier = rows[0][0]
            self.fill(rows[0][2])

    def fill(self, prompt):
        self.editor.set_value(prompt.content, prompt.title)
        self.mode.set(prompt.mode)
        self.editor.set_ai_mode(prompt.mode == "ai")
        self.favorite.set(prompt.favorite)
        for name, field in self.fields.items():
            field.delete(0, "end")
            field.insert(0, getattr(prompt, name))

    def new(self):
        self.tree.selection_remove(self.tree.selection())
        self.identifier = None
        self.fill(Prompt("", ""))

    def save(self, copy=False):
        if not self.library:
            return
        prompt = Prompt(self.editor.title_var.get(), self.editor.value(), self.mode.get(),
                        **{key: entry.get() for key, entry in self.fields.items()}, favorite=self.favorite.get())
        collection = self.groups.get(self.group.get())
        if collection is None and str(self.identifier) in self.rows:
            collection = self.rows[str(self.identifier)][1]
        identifier = self.report(lambda: self.library.save(prompt, collection, None if copy else self.identifier))
        if identifier:
            self.identifier = identifier
            self.refresh()
            self.tree.selection_set(str(identifier))
            self.status.set("Prompt salvo.")

    def manage_collection(self):
        if not self.library:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Coleções")
        group = self.groups.get(self.group.get())
        def save(edit=False):
            name = simpledialog.askstring("Coleção", "Nome da coleção", parent=dialog)
            if name:
                description = simpledialog.askstring("Coleção", "Descrição opcional", parent=dialog) or ""
                self.report(lambda: self.library.collection(name, description, group if edit else None))
                self.refresh()
        ttk.Button(dialog, text="Criar", command=save).pack(fill="x")
        if group:
            ttk.Button(dialog, text="Editar", command=lambda: save(True)).pack(fill="x")
            ttk.Button(dialog, text="Excluir coleção", command=lambda: (
                self.report(lambda: self.library.delete_collection(group)), self.refresh()
            ) if messagebox.askyesno("Excluir", "Excluir coleção e manter seus prompts?", parent=dialog) else None).pack(fill="x")
            ttk.Button(dialog, text="Enviar coleção à fila", command=lambda: self.enqueue(
                self.library.prompts(group))).pack(fill="x")

    def delete(self):
        keys = tuple(map(int, self.tree.selection()))
        if keys and messagebox.askyesno("Excluir prompts", f"Excluir {len(keys)} prompt(s)?", parent=self):
            self.report(lambda: self.library.delete(keys))
            self.new()
            self.refresh()

    def export(self):
        rows = self.current_rows() or list(self.rows.values())
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                            initialfile=f"biblioteca-{uuid4().hex[:8]}.json")
        if path:
            self.report(lambda: write_text_atomically(Path(path), json.dumps(
                {"version": 1, "prompts": [asdict(row[2]) for row in rows]}, ensure_ascii=False, indent=2)))

    def import_file(self):
        if not self.library:
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[("Prompts", "*.txt *.md *.json")])
        if not path:
            return
        prompts = self.report(lambda: parse_import(Path(path).read_text(encoding="utf-8-sig"), Path(path).suffix.lower()))
        if not prompts:
            return
        group = self.groups.get(self.group.get())
        existing = {row[2].content for row in self.library.prompts(group) if row[1] == group}
        preview = tk.Toplevel(self)
        preview.title("Prévia da importação")
        editor = PromptEditor(preview, title=False, readonly=True)
        editor.pack(fill="both", expand=True)
        editor.set_value("\n\n".join(f"{p.title}\n{p.content}" for p in prompts))
        def confirm():
            added = invalid = duplicates = 0
            for prompt in prompts:
                if prompt.content in existing:
                    duplicates += 1
                    continue
                try:
                    self.library.save(prompt, group)
                except (ValueError, TypeError):
                    invalid += 1
                else:
                    added += 1
                    existing.add(prompt.content)
            self.refresh()
            self.status.set(f"{added} importados · {duplicates} duplicados · {invalid} inválidos")
            preview.destroy()
        ttk.Button(preview, text="Importar e ignorar duplicados", command=lambda: self.report(confirm)).pack()

    def versions(self):
        if self.identifier:
            dialog = tk.Toplevel(self)
            dialog.title("Versões do prompt")
            for number, prompt, stamp in self.library.versions(self.identifier):
                ttk.Button(dialog, text=f"v{number} · {stamp}", command=lambda prompt=prompt: self.fill(prompt)).pack(fill="x")

    def run(self):
        rows = self.current_rows()
        if len(rows) == 1:
            self.open_test(rows[0])

    def send(self):
        rows = self.current_rows()
        if rows:
            self.enqueue(rows)

    def show_history(self):
        if self.identifier:
            self.history(self.identifier)
