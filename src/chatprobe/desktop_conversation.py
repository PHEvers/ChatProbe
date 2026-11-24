"""Painel rolável da conversa registrada em um teste."""

import tkinter as tk
from tkinter import ttk
from tkinter.font import Font


SPEAKERS = {"user": "Usuário", "bot": "Bot", "detail": "Encerramento"}


class ConversationView(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.messages = []
        self.palette = None
        self.message_font = Font(self, family="Segoe UI", size=10)
        self.speaker_font = Font(self, family="Segoe UI", size=9, weight="bold")

        self.canvas = tk.Canvas(self, width=320, height=140, highlightthickness=1,
                                takefocus=True, yscrollincrement=18)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self.layout_messages)
        self.canvas.bind("<MouseWheel>", self.scroll_wheel)
        self.canvas.bind("<Button-1>", lambda event: self.canvas.focus_set())
        self.canvas.bind("<Button-3>", self.open_copy_menu)
        self.canvas.bind("<Control-c>", lambda event: self.copy())
        for key, amount, unit in (
            ("Up", -1, "units"), ("Down", 1, "units"),
            ("Prior", -1, "pages"), ("Next", 1, "pages"),
            ("Button-4", -3, "units"), ("Button-5", 3, "units"),
        ):
            self.canvas.bind(f"<{key}>", lambda event, n=amount, u=unit: self.canvas.yview_scroll(n, u))
        self.canvas.bind("<Home>", lambda event: self.canvas.yview_moveto(0))
        self.canvas.bind("<End>", lambda event: self.canvas.yview_moveto(1))
        self.copy_menu = tk.Menu(self, tearoff=False)

    def apply_palette(self, palette):
        self.palette = palette
        self.canvas.configure(
            background=palette["canvas"], highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )
        self.copy_menu.configure(
            background=palette["surface"], foreground=palette["text"],
            activebackground=palette["selected"], activeforeground=palette["text"],
        )
        self.layout_messages()

    def show(self, turns=(), detail=""):
        self.messages = []
        for turn in turns:
            self.messages.append(("user", turn["message"]))
            self.messages.extend(("bot", reply) for reply in turn["replies"] if reply)
        if detail:
            self.messages.append(("detail", detail))
        self.canvas.yview_moveto(0)
        self.layout_messages()

    def layout_messages(self, event=None):
        if not self.palette:
            return
        width = self.canvas.winfo_width()
        if width <= 1:
            return
        position = self.canvas.yview()[0]
        self.canvas.delete("all")
        y = 14
        for index, (role, text) in enumerate(self.messages):
            y = self.draw_bubble(index, role, text, width, y)
        self.canvas.configure(scrollregion=(0, 0, width, max(y, self.canvas.winfo_height())))
        self.canvas.yview_moveto(position)

    def draw_bubble(self, index, role, text, width, top):
        gutter = 14
        padding = 12
        speaker = SPEAKERS[role]
        content_width = max(self.message_font.measure(line) for line in text.split("\n"))
        desired_width = max(content_width, self.speaker_font.measure(speaker)) + 2 * padding
        max_width = max(1, min(width - 2 * gutter, int(width * 0.79)))
        bubble_width = min(desired_width, max_width)
        left = width - gutter - bubble_width if role == "user" else gutter
        if role == "detail":
            left = (width - bubble_width) // 2
            background, label_color = self.palette["surface"], self.palette["muted"]
        else:
            background, label_color = self.palette[f"{role}_message"], self.palette[f"{role}_speaker"]
        tags = (role, f"message:{index}")
        header = self.canvas.create_text(
            left + padding, top + 8, anchor="nw", text=speaker,
            font=self.speaker_font, fill=label_color, tags=tags,
        )
        body = self.canvas.create_text(
            left + padding, self.canvas.bbox(header)[3] + 4, anchor="nw", text=text,
            width=max(1, bubble_width - 2 * padding), font=self.message_font,
            fill=self.palette["text"], tags=tags,
        )
        bottom = self.canvas.bbox(body)[3] + 10
        right = left + bubble_width
        radius = min(10, bubble_width // 2)
        bubble = self.canvas.create_polygon(
            left + radius, top, right - radius, top, right, top, right, top + radius,
            right, bottom - radius, right, bottom, right - radius, bottom,
            left + radius, bottom, left, bottom, left, bottom - radius,
            left, top + radius, left, top,
            smooth=True, splinesteps=12, fill=background, tags=(*tags, "bubble"),
        )
        self.canvas.tag_lower(bubble, header)
        return bottom + 10

    def copy(self, index=None):
        if not self.messages:
            return
        text = (self.messages[index][1] if index is not None else
                "\n\n".join(f"{SPEAKERS[role]}\n{message}" for role, message in self.messages))
        self.clipboard_clear()
        self.clipboard_append(text)

    def open_copy_menu(self, event):
        for tag in self.canvas.gettags("current"):
            if tag.startswith("message:"):
                index = int(tag.split(":")[1])
                self.copy_menu.delete(0, "end")
                self.copy_menu.add_command(label="Copiar mensagem", command=lambda: self.copy(index))
                try:
                    self.copy_menu.tk_popup(event.x_root, event.y_root)
                finally:
                    self.copy_menu.grab_release()
                break

    def scroll_wheel(self, event):
        direction = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(direction * max(1, abs(event.delta) // 120), "units")
        return "break"
