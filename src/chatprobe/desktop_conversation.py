"""Transcrição selecionável em balões, com visualização ampliada."""

import tkinter as tk
from tkinter import font, ttk

from src.chatprobe.browser.timing import local_time


SPEAKERS = {"user": "Cliente", "bot": "Chatbot", "detail": "Encerramento"}


class ConversationView(ttk.Frame):
    def __init__(self, parent, *, expandable=True):
        super().__init__(parent)
        self.messages = []
        self.palette = None
        self.expanded = None
        self.allow_expand = expandable
        self.bubbles = []
        self.body_font = font.nametofont("TkDefaultFont", root=self)
        self.text = tk.Text(self, width=24, height=6, wrap="word", padx=12, pady=10,
                            state="disabled", relief="flat", font="TkDefaultFont")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.bind("<Control-a>", self.select_all)
        self.text.bind("<Configure>", self.resize)
        self.text.bind("<Button-3>", self.open_copy_menu)
        self.copy_menu = tk.Menu(self, tearoff=False)
        self.copy_menu.add_command(label="Copiar seleção", command=lambda: self.text.event_generate("<<Copy>>"))
        self.copy_menu.add_command(label="Copiar conversa completa", command=self.copy)

    def resize(self, event=None):
        width = (event.width if event else self.text.winfo_width()) - 30
        if width < 60:
            return
        margin = 8
        for tag, role, desired in self.bubbles:
            bubble_width = min(max(110, desired + 14), round(width * 0.78))
            if role == "user":
                left, right = width - bubble_width, margin
            elif role == "bot":
                left, right = margin, width - bubble_width
            else:
                left, right = margin, margin
            self.text.tag_configure(tag, lmargin1=left, lmargin2=left, rmargin=right)

    def apply_palette(self, palette):
        self.palette = palette
        self.text.configure(background=palette["field"], foreground=palette["text"],
                            highlightbackground=palette["border"], highlightcolor=palette["accent"],
                            highlightthickness=1, selectbackground=palette["selection_text"],
                            selectforeground="#ffffff")
        self.copy_menu.configure(background=palette["surface"], foreground=palette["text"],
                                 activebackground=palette["selected"], activeforeground=palette["text"])
        self.text.tag_configure("separator", spacing3=9)
        for role in SPEAKERS:
            background = palette.get(f"{role}_message", palette["surface"])
            foreground = palette.get(f"{role}_speaker", palette["muted"])
            self.text.tag_configure(role, background=background, spacing2=2, spacing3=3)
            if "lmargincolor" in self.text.tag_configure(role):
                self.text.tag_configure(role, lmargincolor=palette["field"], rmargincolor=palette["field"])
            self.text.tag_configure(f"{role}_speaker", foreground=foreground,
                                    font=(self.body_font.actual("family"), 9),
                                    justify="right" if role == "user" else "left", spacing1=3)
        self.text.tag_raise("sel")
        if self.expanded is not None:
            self.expanded.apply_palette(palette)

    def show(self, turns=(), detail=""):
        messages = []
        for turn in turns:
            messages.append(("user", turn["message"], local_time(turn.get("sent_at"))))
            timestamps = turn.get("reply_received_at", [])
            for index, reply in enumerate(turn["replies"]):
                if reply:
                    received = timestamps[index] if index < len(timestamps) else None
                    messages.append(("bot", reply, local_time(received)))
        if detail:
            messages.append(("detail", detail, ""))
        self.set_messages(messages)

    def set_messages(self, messages):
        self.messages = messages
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if self.bubbles:
            self.text.tag_delete(*(tag for tag, _, _ in self.bubbles))
        self.bubbles = []
        for number, (role, message, timestamp) in enumerate(messages):
            heading = f"{SPEAKERS[role]} · {timestamp}" if timestamp else SPEAKERS[role]
            longest = max([heading, *message.split("\n")], key=len)
            tag = f"bubble_{number}"
            self.bubbles.append((tag, role, self.body_font.measure(longest[:150])))
            self.text.insert("end", message + "\n", (role, tag))
            self.text.insert("end", heading + "\n", (f"{role}_speaker", tag))
            self.text.insert("end", "\n", ("separator",))
        self.resize()
        self.text.tag_raise("sel")
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)
        if self.expanded is not None:
            self.expanded.set_messages(messages)

    def select_all(self, event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def copy(self):
        if not self.messages:
            return
        text = "\n\n".join(
            f"{SPEAKERS[role]}" + (f" · {timestamp}" if timestamp else "") + f"\n{message}"
            for role, message, timestamp in self.messages
        )
        self.clipboard_clear()
        self.clipboard_append(text)

    def open_copy_menu(self, event):
        self.copy_menu.entryconfigure(0, state="normal" if self.text.tag_ranges("sel") else "disabled")
        self.copy_menu.entryconfigure(1, state="normal" if self.messages else "disabled")
        try:
            self.copy_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.copy_menu.grab_release()

    def expand(self):
        if self.expanded is not None:
            self.expanded.winfo_toplevel().lift()
            return
        if not self.allow_expand:
            return
        parent = self.winfo_toplevel()
        popup = tk.Toplevel(parent)
        popup.title("Conversa")
        popup.transient(parent)
        popup.geometry(f"{min(1000, parent.winfo_screenwidth() - 100)}x"
                       f"{min(740, parent.winfo_screenheight() - 120)}+38+38")
        self.expanded = ConversationView(popup, expandable=False)
        self.expanded.pack(fill="both", expand=True, padx=14, pady=14)
        if self.palette:
            self.expanded.apply_palette(self.palette)
            popup.configure(background=self.palette["canvas"])
        self.expanded.set_messages(self.messages)

        def close(event=None):
            self.expanded = None
            popup.destroy()

        actions = ttk.Frame(popup)
        actions.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(actions, text="Copiar conversa", command=self.copy).pack(side="left")
        ttk.Button(actions, text="Fechar", command=close).pack(side="right")
        popup.bind("<Escape>", close)
        popup.protocol("WM_DELETE_WINDOW", close)
        return self.expanded
