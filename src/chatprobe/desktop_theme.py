"""Temas dos controles Tk e ttk da janela desktop."""

from tkinter import ttk


DARK_THEME = "chatprobe-dark"
LIGHT_THEME = "chatprobe-light"
LISTBOX_OPTIONS = {
    "background": "background", "foreground": "foreground",
    "selectbackground": "selectBackground", "selectforeground": "selectForeground",
}
PALETTES = {
    LIGHT_THEME: {
        "canvas": "#edf2f8", "surface": "#ffffff", "text": "#17253b", "muted": "#52647a",
        "border": "#cbd6e3", "hover": "#e5edf7", "selected": "#dbeaff",
        "accent": "#185bb7", "accent_hover": "#144895", "warning": "#9c4100",
        "user_speaker": "#174f98", "user_message": "#e1eeff",
        "bot_speaker": "#176249", "bot_message": "#e5f5ed",
    },
    DARK_THEME: {
        "canvas": "#141c29", "surface": "#202b3b", "text": "#edf2fa", "muted": "#acbbce",
        "border": "#4b5d75", "hover": "#2a3a52", "selected": "#314866",
        "accent": "#3676ce", "accent_hover": "#2c63af", "warning": "#ffb074",
        "user_speaker": "#a6ceff", "user_message": "#29446c",
        "bot_speaker": "#9ddfc6", "bot_message": "#25453e",
    },
}


class DesktopTheme:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        for theme, colors in PALETTES.items():
            if theme not in self.style.theme_names():
                self.style.theme_create(theme, parent="clam", settings=self.settings(colors))

    @staticmethod
    def settings(colors):
        canvas, surface = colors["canvas"], colors["surface"]
        text, muted = colors["text"], colors["muted"]
        border, hover, selected = colors["border"], colors["hover"], colors["selected"]
        accent, accent_hover = colors["accent"], colors["accent_hover"]
        return {
            ".": {"configure": {
                "font": ("Segoe UI", 10), "background": canvas, "foreground": text,
                "fieldbackground": surface, "bordercolor": border, "lightcolor": surface,
                "darkcolor": surface, "troughcolor": canvas, "selectbackground": selected,
                "selectforeground": text, "insertcolor": text, "focuscolor": accent,
            }},
            "Title.TLabel": {"configure": {"font": ("Segoe UI", 24, "bold")}},
            "Subtitle.TLabel": {"configure": {"foreground": muted}},
            "Warning.TLabel": {"configure": {"foreground": colors["warning"]}},
            "TButton": {
                "configure": {"background": surface, "padding": (12, 6)},
                "map": {"foreground": [("disabled", muted)],
                        "background": [("pressed", selected), ("active", hover)]},
            },
            "Primary.TButton": {
                "configure": {"background": accent, "foreground": "#ffffff",
                              "font": ("Segoe UI", 10, "bold")},
                "map": {"foreground": [("disabled", muted)],
                        "background": [("disabled", surface), ("pressed", accent_hover),
                                       ("active", accent_hover)]},
            },
            "TEntry": {
                "configure": {"padding": (6, 4)},
                "map": {"foreground": [("disabled", muted)],
                        "fieldbackground": [("readonly", surface)]},
            },
            "TCombobox": {
                "configure": {"padding": (6, 4)},
                "map": {"foreground": [("disabled", muted)],
                        "fieldbackground": [("disabled", canvas), ("readonly", surface)],
                        "arrowcolor": [("disabled", muted), ("!disabled", text)]},
            },
            "TNotebook": {"configure": {"tabmargins": (0, 2, 0, 0)}},
            "TNotebook.Tab": {
                "configure": {"padding": (16, 8)},
                "map": {"background": [("selected", selected), ("active", hover)]},
            },
            "TLabelframe": {"configure": {"relief": "solid", "borderwidth": 1}},
            "TLabelframe.Label": {"configure": {"font": ("Segoe UI", 10, "bold")}},
            "TCheckbutton": {"configure": {
                "indicatorbackground": surface, "indicatorforeground": text, "padding": (0, 3),
            }},
        }

    def apply(self, dark, *text_widgets):
        theme = DARK_THEME if dark else LIGHT_THEME
        self.style.theme_use(theme)
        palette = PALETTES[theme]
        background, field, foreground = palette["canvas"], palette["surface"], palette["text"]
        self.root.configure(background=background)
        colors = {
            "background": field, "foreground": foreground,
            "selectbackground": palette["selected"], "selectforeground": foreground,
        }
        for widget in text_widgets:
            widget.configure(
                **colors, insertbackground=foreground, highlightbackground=palette["border"],
                highlightcolor=palette["accent"], highlightthickness=1, relief="flat",
            )
            widget.frame.configure(background=background)
            widget.vbar.configure(background=background, troughcolor=field)
        for option, color in colors.items():
            self.root.option_add(f"*TCombobox*Listbox.{LISTBOX_OPTIONS[option]}", color)
        self._color_lists(str(self.root), colors)
        return palette

    def _color_lists(self, window, colors):
        tk = self.root.tk
        for child in tk.splitlist(tk.call("winfo", "children", window)):
            if tk.call("winfo", "class", child) == "Listbox":
                for option, color in colors.items():
                    tk.call(child, "configure", f"-{option}", color)
            self._color_lists(child, colors)
