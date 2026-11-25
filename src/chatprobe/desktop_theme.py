"""Temas dos controles Tk e ttk da janela desktop."""

from tkinter import font, ttk


DARK_THEME = "chatprobe-dark"
LIGHT_THEME = "chatprobe-light"
LISTBOX_OPTIONS = {
    "background": "background", "foreground": "foreground",
    "selectbackground": "selectBackground", "selectforeground": "selectForeground",
}
PALETTES = {
    LIGHT_THEME: {
        "canvas": "#edf2f8", "surface": "#ffffff", "field": "#f8faff",
        "text": "#17253b", "muted": "#52647a",
        "border": "#cbd6e3", "hover": "#e5edf7", "selected": "#dbeaff",
        "accent": "#185bb7", "accent_hover": "#144895", "warning": "#8c4308",
        "success": "#18724c", "error": "#b42333", "selection_text": "#185bb7",
        "active": "#185bb7",
        "user_speaker": "#174f98", "user_message": "#e1eeff",
        "bot_speaker": "#176249", "bot_message": "#e5f5ed",
    },
    DARK_THEME: {
        "canvas": "#141c29", "surface": "#1e2939", "field": "#101722",
        "text": "#f1f5f9", "muted": "#a4b3c7",
        "border": "#334155", "hover": "#3b4c63", "selected": "#304967",
        "accent": "#2563eb", "accent_hover": "#1e50bd", "warning": "#f5b64f",
        "success": "#50d582", "error": "#ff8a91", "selection_text": "#25569b",
        "active": "#93b8ff",
        "user_speaker": "#b6cae1", "user_message": "#244977",
        "bot_speaker": "#b6cae1", "bot_message": "#182335",
    },
}


class DesktopTheme:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        families = {family.casefold() for family in font.families(root)}
        family = next((name for name in ("Segoe UI", "DejaVu Sans", "Helvetica") if name.casefold() in families),
                      font.nametofont("TkDefaultFont", root=root).actual("family"))
        font.nametofont("TkDefaultFont", root=root).configure(family=family, size=10)
        for theme, colors in PALETTES.items():
            if theme not in self.style.theme_names():
                self.style.theme_create(theme, parent="clam", settings=self.settings(colors, family))

    @staticmethod
    def settings(colors, family):
        canvas, surface = colors["canvas"], colors["surface"]
        text, muted = colors["text"], colors["muted"]
        border, hover, selected = colors["border"], colors["hover"], colors["selected"]
        accent, accent_hover = colors["accent"], colors["accent_hover"]
        label_styles = {
            f"{tone}.State.TLabel": {"configure": {"foreground": colors[tone]}}
            for tone in ("active", "success", "warning", "error")
        }
        return {
            **label_styles,
            ".": {"configure": {
                "font": (family, 10), "background": canvas, "foreground": text,
                "fieldbackground": colors["field"], "bordercolor": border, "lightcolor": surface,
                "darkcolor": surface, "troughcolor": canvas, "selectbackground": selected,
                "selectforeground": text, "insertcolor": text, "focuscolor": accent,
            }},
            "Panel.TFrame": {"configure": {"background": surface}},
            "Panel.TLabel": {"configure": {"background": surface}},
            "PanelMuted.TLabel": {"configure": {"background": surface, "foreground": muted}},
            "PanelWarning.TLabel": {"configure": {"background": surface, "foreground": colors["warning"]}},
            "Section.TLabel": {"configure": {"font": (family, 14, "bold")}},
            "PanelHeading.TLabel": {"configure": {"background": surface, "font": (family, 11, "bold")}},
            "Card.TLabelframe": {"configure": {"background": surface, "bordercolor": surface}},
            "Card.TLabelframe.Label": {"configure": {"background": surface, "font": (family, 11, "bold")}},
            "Brand.TLabel": {"configure": {"background": canvas, "font": (family, 20, "bold")}},
            "Version.TLabel": {"configure": {"background": selected, "foreground": text,
                                              "padding": (7, 2), "font": (family, 9, "bold")}},
            "State.TLabel": {"configure": {"background": surface, "foreground": text,
                                            "font": (family, 11, "bold")}},
            "Navigation.TButton": {
                "configure": {"padding": (11, 10), "background": surface, "font": (family, 11, "bold")},
                "map": {"background": [("selected", selected), ("active", hover)],
                        "foreground": [("selected", text)]},
            },
            "Horizontal.TProgressbar": {
                "configure": {"background": accent, "bordercolor": surface, "troughcolor": border,
                              "lightcolor": accent, "darkcolor": accent, "thickness": 8},
            },
            "TScrollbar": {"configure": {"background": surface, "arrowcolor": muted, "width": 13},
                           "map": {"background": [("active", hover)]}},
            "Treeview": {"configure": {"background": colors["field"], "fieldbackground": colors["field"],
                                       "rowheight": 27},
                         "map": {"background": [("selected", selected)], "foreground": [("selected", text)]}},
            "Subtitle.TLabel": {"configure": {"foreground": muted}},
            "Warning.TLabel": {"configure": {"foreground": colors["warning"]}},
            "TButton": {
                "configure": {"background": surface, "padding": (12, 6)},
                "map": {"foreground": [("disabled", muted)],
                        "background": [("pressed", selected), ("active", hover)]},
            },
            "Primary.TButton": {
                "configure": {"background": accent, "foreground": "#ffffff",
                              "font": (family, 10, "bold")},
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
            "History.TNotebook.Tab": {"configure": {"padding": (7, 5)}},
            "TNotebook": {"configure": {"tabmargins": (0, 2, 0, 0)}},
            "TNotebook.Tab": {
                "configure": {"padding": (16, 8)},
                "map": {"background": [("selected", selected), ("active", hover)]},
            },
            "TLabelframe": {"configure": {"relief": "solid", "borderwidth": 1}},
            "TLabelframe.Label": {"configure": {"font": (family, 10, "bold")}},
            "TCheckbutton": {"configure": {
                "indicatorbackground": surface, "indicatorforeground": text, "padding": (0, 3),
            }},
        }

    def apply(self, dark):
        theme = DARK_THEME if dark else LIGHT_THEME
        self.style.theme_use(theme)
        palette = PALETTES[theme]
        background, field, foreground = palette["canvas"], palette["surface"], palette["text"]
        self.root.configure(background=background)
        colors = {
            "background": field, "foreground": foreground,
            "selectbackground": palette["selected"], "selectforeground": foreground,
        }
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
