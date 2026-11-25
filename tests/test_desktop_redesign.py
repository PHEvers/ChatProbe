import json
import unittest
from dataclasses import replace
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from src.chatprobe import desktop_preferences as prefs
from src.chatprobe.browser import smoke
from src.chatprobe.browser.version import VERSION, load_version, windows_resource
from src.chatprobe.runner.batch import run_test
from src.chatprobe.runner.reports import legacy_conversation


class ReleaseTests(unittest.TestCase):
    def test_same_version_can_be_loaded_from_bundle_root_and_windows_metadata(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "VERSION").write_text(VERSION + "\n", encoding="ascii")
            self.assertEqual(load_version(root), VERSION)
            resource = windows_resource(load_version(root))
            self.assertIn(f"StringStruct('ProductVersion', {VERSION!r})", resource)
            self.assertIn(f"prodvers={(*map(int, VERSION.split('.')), 0)}", resource)
            (root / "VERSION").write_text("release one", encoding="ascii")
            with self.assertRaises(ValueError):
                load_version(root)

    def test_report_version_preserves_array_and_turn_shape(self):
        def execute(*args, turns, **kwargs):
            turns.append({"message": "Cliente", "replies": ["Chatbot"]})

        with patch.object(smoke, "main", side_effect=execute):
            result = run_test(1, "https://example.invalid", ("Cliente", []), "", None, Event(), Queue())
        report = json.loads(json.dumps(legacy_conversation(result)))
        self.assertEqual(report[0]["test_metadata"]["application_version"], VERSION)
        self.assertEqual(report[0]["replies"], ["Chatbot"])

    def test_legacy_appearance_and_prompt_order_survive_title_migration(self):
        long_prompt = "\n   Teste com acentuação e uma pausa\n" * 1000
        with TemporaryDirectory() as directory, patch.object(prefs, "SETTINGS_FILE", Path(directory) / "settings.json"):
            prefs.SETTINGS_FILE.write_text(json.dumps(
                {"dark_mode": True, "night_prompts": [long_prompt, "Segundo"], "mode": "Ollama"}), encoding="utf-8")
            legacy = prefs.load_preferences()
            self.assertTrue(legacy.dark_mode)
            self.assertEqual(legacy.night_prompts, (long_prompt, "Segundo"))
            self.assertEqual(legacy.night_prompt_titles, ())
            prefs.save_preferences(replace(legacy, night_prompt_titles=("Primeiro", "Segundo")))
            loaded = prefs.load_preferences()
            self.assertEqual(loaded.night_prompts, legacy.night_prompts)
            self.assertEqual(loaded.night_prompt_titles, ("Primeiro", "Segundo"))
            self.assertTrue(loaded.dark_mode)


class TkTests(unittest.TestCase):
    def setUp(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tk indisponível.")
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("Sessão gráfica indisponível.")
        self.addCleanup(self.root.destroy)
        self.root.geometry("800x620")

    def test_expanded_editor_preserves_unicode_title_and_whitespace(self):
        from src.chatprobe.desktop_prompt import PromptEditor
        from src.chatprobe.desktop_theme import DesktopTheme

        theme = DesktopTheme(self.root)
        editor = PromptEditor(self.root)
        editor.pack(fill="both", expand=True)
        editor.apply_palette(theme.apply(True))
        original = "  Ação e informações especiais ☀\n" * 5000
        editor.set_value(original, "Roteiro extenso")
        editor.set_ai_mode(True)
        self.root.update()
        enlarged = editor.expand()
        self.root.update()
        self.assertEqual(enlarged.value(), original)
        enlarged.text.insert("end", "\n Versão final  ")
        popup = enlarged.winfo_toplevel()
        next(child for child in popup.winfo_children() if child.winfo_class() == "TButton").invoke()
        self.assertEqual(editor.value(), original + "\n Versão final  ")
        self.assertEqual(editor.title_var.get(), "Roteiro extenso")
        self.assertIn("tokens estimados", editor.stats.cget("text"))
        self.assertIsNone(editor.expanded)

    def test_queue_reorder_preserves_original_contents_and_freezes_controls(self):
        from src.chatprobe.desktop_night_queue import NightQueue

        saved = []
        first = "   Ação\n\n" * 1000
        queue = NightQueue(self.root, (first, "Próximo"),
                           lambda *value: bool(saved.append(value) or True), lambda message: None,
                           titles=("Longo", "Breve"))
        queue.pack(fill="both", expand=True)
        queue.load_prompt(1)
        queue.move_prompt(-1)
        self.assertEqual(saved[-1], (("Próximo", first), ("Breve", "Longo"), (None, None)))
        queue.set_running(True)
        self.assertEqual(queue.editor.cget("state"), "disabled")
        self.assertTrue(queue.down.instate(["disabled"]))

    def test_conversation_allows_selection_and_expanded_copy(self):
        from src.chatprobe.desktop_conversation import ConversationView
        from src.chatprobe.desktop_theme import DesktopTheme

        view = ConversationView(self.root)
        view.pack(fill="both", expand=True)
        view.apply_palette(DesktopTheme(self.root).apply(False))
        view.show([{"message": "Oi\n   cliente", "replies": ["  Resposta"]}])
        view.select_all()
        self.assertIn("Oi\n   cliente", view.text.get("sel.first", "sel.last"))
        expanded = view.expand()
        self.assertEqual(expanded.messages, view.messages)
        expanded.copy()
        self.assertIn("Chatbot\n  Resposta", self.root.clipboard_get())


    def test_small_window_keeps_primary_actions_and_execution_states(self):
        from app import desktop
        from src.chatprobe.provider_catalog import ProviderCatalog
        from src.chatprobe.runner.batch import BatchControl

        self.root.tk.call("tk", "scaling", 5 / 3)
        with TemporaryDirectory() as directory:
            path = Path(directory)
            with patch.object(prefs, "SETTINGS_FILE", path / "settings.json"), patch.object(
                desktop, "DEFAULT_DATABASE", path / "tests.sqlite3",
            ), patch.object(desktop, "ProviderCatalog", side_effect=lambda *args, **kwargs: ProviderCatalog(
                *args, **kwargs, path=path / "providers.json",
            )):
                window = desktop.ChatProbeWindow(self.root)
                self.root.geometry("800x620")
                window.mode_field.set(desktop.AI_MODE)
                window.change_mode(None)
                window.tabs.select(list(window.tabs.pages)[1])
                self.root.update()
                for control in (window.night_queue.save_button, window.night_queue.prompt_editor.expander,
                                window.night_run_button, window.schedule_button):
                    self.assertTrue(control.winfo_ismapped())
                    self.assertGreaterEqual(control.winfo_height(), control.winfo_reqheight())
                window.run_control = BatchControl()
                window.set_running(True)
                window.toggle_pause()
                self.assertEqual(window.indicators.state.get(), "Pausado")
                self.assertEqual(window.pause_buttons[0]["text"], "Retomar")
                window.toggle_pause()
                self.assertEqual(window.indicators.state.get(), "Em execução")
                window.stop_batch()
                self.assertTrue(window.stop_buttons[0].instate(["disabled"]))
                self.assertTrue(window.night_run_button.instate(["disabled"]))
