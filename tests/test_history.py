import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from src.chatprobe.browser import smoke
from src.chatprobe.browser.version import VERSION
from src.chatprobe.history_reports import export_records, import_reports
from src.chatprobe.history_rerun import RerunDraft
from src.chatprobe.runner.batch import run_test
from src.chatprobe.runner.consumption import ConsumptionLedger, ExecutionOptions, Quota
from src.chatprobe.storage.history import HistoryStore, comparison_differences
from src.chatprobe.storage.history_metadata import filter_time, public_url, utc_value
from src.chatprobe.storage.sqlite_store import MIGRATIONS, RunStore


SENT = "2026-11-25T10:00:00.000+00:00"
REPLY = "2026-11-25T10:00:02.000+00:00"
PROMPT = "  Cenário de impressão\n\n" * 2000 + "  "
PROVIDER = {"id": "example", "name": "IA fictícia", "model": "modelo-teste", "temperature": 0.3}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "history.sqlite3"
        self.store = RunStore(self.path)
        self.history = HistoryStore(self.store)

    def add(self, number, *, stamp=SENT, title="Cupom", product="AUTOSYSTEM", status="completed",
            reason="completed", duration=2.0, source=None):
        identifier = self.store.start_test(
            "batch", number, "ai", stamp, PROVIDER,
            {"title": title, "prompt_text": PROMPT, "product": product,
             "url": "https://user:password@example.invalid/chat?token=sensitive#session",
             "execution_type": "queue", "application_version": VERSION, "source_test_id": source,
             "settings": {"parallel": 2, "api_key": "sensitive", "execution": {"interaction_seconds": 1, "key": "bad"}}},
        )
        self.store.save_turn(identifier, 1, {"message": "Falha no cupom", "sent_at": SENT,
                                            "replies": ["  Orientação com acentuação\n Segunda linha"],
                                            "reply_received_at": [REPLY]})
        if status != "running":
            self.store.finish_test(identifier, {"ended_at": REPLY, "duration_seconds": duration,
                                                "status": status, "termination_reason": reason,
                                                "token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                                                "detail": "Detalhe em https://example.invalid/chat?secret=sensitive"})
        return identifier

    def test_snapshot_preserves_original_prompt_conversation_and_timestamps(self):
        identifier = self.add(1)
        ledger = ConsumptionLedger(self.store, lambda: 1800000000)
        reservation, _ = ledger.reserve([("test", Quota())], 19, identifier, interaction=1)
        ledger.finish(reservation, {"total_tokens": 5}, "ok", processing_seconds=0.5)
        original, = self.history.details([identifier])
        self.assertEqual(original["prompt_text"], PROMPT)
        self.assertEqual(original["turns"][0]["response_seconds"], 2)
        self.assertEqual(original["turns"][0]["replies"], ["  Orientação com acentuação\n Segunda linha"])
        self.assertEqual(original["model_seconds"], 0.5)
        self.assertEqual((original["known_tokens"], original["estimated_tokens"], original["requests"]), (5, 19, 1))
        self.assertEqual(original["url"], "https://example.invalid/chat")
        self.assertEqual(original["settings"], {"parallel": 2, "execution": {"interaction_seconds": 1}})
        self.assertNotIn("sensitive", export_records([original], "json"))

    def test_combined_filters_unicode_search_aggregate_and_page(self):
        first = self.add(1, title="Primeiro", status="interrupted", reason="loop")
        newest = self.add(2, stamp="2026-11-25T16:30:00-03:00", title="Segundo", duration=7)
        other = self.add(3, product="SELLER", stamp="2026-11-26T01:00:00+00:00")
        filters = {"product": "AUTOSYSTEM", "provider": "IA fictícia", "model": "modelo-teste",
                   "execution_type": "queue", "batch": "batch", "version": VERSION,
                   "from": "2026-11-25T00:00:00+00:00", "to": "2026-11-26T00:00:00+00:00"}
        page = self.history.page("ACENTUAÇÃO", filters, page_size=1)
        self.assertEqual([row["id"] for row in page.rows], [newest])
        self.assertEqual(page.pages, 2)
        self.assertEqual(page.totals["loops"], 1)
        self.assertEqual(page.totals["known_tokens"], 10)
        self.assertEqual(self.history.page("loop de conversa").rows[0]["id"], first)
        ordered = self.history.page(filters=filters, order="duration", descending=False, page_size=1, page=1)
        self.assertEqual(ordered.rows[0]["id"], newest)
        self.assertNotEqual(ordered.rows[0]["id"], other)
        self.assertEqual(self.history.page("'; DROP TABLE tests;--").totals["total"], 0)

    def test_utc_and_zone_conversion(self):
        self.assertEqual(utc_value("2026-11-25T16:30:00-03:00"), "2026-11-25T19:30:00.000000+00:00")
        self.assertIsNone(utc_value("2026-11-25T16:30:00"))
        self.assertEqual(filter_time("2026-11-25T04:15:00+05:45"), "2026-11-24T22:30:00.000000+00:00")
        self.assertEqual(public_url("https://a:b@example.invalid/api/token/secret?key=sensitive"),
                         "https://example.invalid")

    def test_rerun_records_origin_without_mutating_source(self):
        identifier = self.add(1)
        original, = self.history.details([identifier])
        draft = RerunDraft.from_record(original, "https://other.invalid", "Ana", ExecutionOptions())
        self.assertEqual(draft.url, "https://example.invalid/chat")
        self.assertEqual(draft.execution.interaction_seconds, 1)
        with patch.object(smoke, "main"):
            result = run_test(1, draft.url, ("novo", []), "", None, Event(), Queue(), store=self.store,
                              history_context={"prompt_text": PROMPT, "source_test_id": draft.source})
        self.assertNotEqual(result["id"], draft.source)
        original_after, rerun = self.history.details([draft.source, result["id"]])
        self.assertEqual(original_after, original)
        self.assertEqual(rerun["source_test_id"], draft.source)
        differences, unknown = comparison_differences(original, rerun)
        self.assertIn("Configurações", differences)
        self.assertIn("Provedor e modelo", unknown)

    def test_import_formats_duplicate_invalid_missing_dates_and_roundtrip(self):
        legacy = [{"message": "  Olá\n", "replies": ["Resposta"]}]
        report = {"total": 2, "prompts": ['{"initial_message":"Olá","steps":[]}', "Outro"],
                  "tests": [{"id": "one", "test": 1, "turns": legacy, "status": "completed",
                             "settings": {"api_key": "hide-me"}, "provider": {"key": "hide-me"},
                             "url": "https://a:b@example.invalid/?secret=hide-me",
                             "api_calls": [{"estimated_tokens": 10, "token_usage": {"total_tokens": 3}}]},
                            {"test": 2, "turns": [{"message": 123, "replies": []}]}]}
        result = import_reports(self.store, report)
        self.assertEqual((result.imported, result.invalid), (1, 1))
        self.assertEqual(import_reports(self.store, report).duplicates, 1)
        self.assertEqual(import_reports(self.store, legacy).imported, 1)
        self.assertEqual(import_reports(self.store, legacy).duplicates, 1)
        record, = self.history.details(["one"])
        self.assertIsNone(record["started_at"])
        self.assertIsNone(record["turns"][0]["sent_at"])
        self.assertEqual(record["turns"][0]["message"], "  Olá\n")
        self.assertEqual(record["mode"], "json")
        self.assertEqual(record["estimated_tokens"], 10)
        for format in ("json", "md", "csv"):
            self.assertNotIn("hide-me", export_records([record], format))
        self.assertIn("  Olá\n", export_records([record], "md"))
        self.assertIn("application_version", export_records([record], "csv"))
        self.assertNotIn("prompt_text", export_records([record], "csv"))
        self.assertEqual(import_reports(self.store, json.loads(export_records([record], "json"))).duplicates, 1)

    def test_delete_is_atomic_and_preserves_ledger_and_referential_integrity(self):
        original = self.add(1)
        child = self.add(2, source=original)
        active = self.add(3, status="running")
        ledger = ConsumptionLedger(self.store, lambda: 1800000000)
        identifier, _ = ledger.reserve([("scope", Quota())], 5, original)
        with self.assertRaises(ValueError):
            self.history.delete([original, active])
        self.assertTrue(self.history.details([original]))
        self.assertEqual(self.history.delete([original]), 1)
        self.assertEqual(self.history.details([original]), [])
        self.assertIsNone(self.history.details([child])[0]["source_test_id"])
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT test_id FROM api_requests WHERE id=?", (identifier,)).fetchone(), (None,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_reads_during_concurrent_writes(self):
        def write(index):
            return self.add(index)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(write, index) for index in range(1, 14)]
            snapshots = [self.history.page(page_size=3) for _ in range(6)]
        self.assertTrue(all(future.result() for future in futures))
        self.assertTrue(all(len(snapshot.rows) <= 3 for snapshot in snapshots))
        self.assertEqual(self.history.page().totals["total"], 13)

    def test_migrates_phase_four_queue_metadata(self):
        path = self.path.with_name("phase-four.sqlite3")
        with sqlite3.connect(path) as db:
            for migration in MIGRATIONS[:4]:
                for sql in migration:
                    db.execute(sql)
            db.execute("PRAGMA user_version=4")
            db.execute("""INSERT INTO tests(id,batch_id,test_number,mode,started_at,status)
                          VALUES('legacy','q',1,'json',?,'completed')""", (SENT,))
            db.execute("INSERT INTO execution_queues VALUES('q',?,'finished',NULL,NULL,?)",
                       (json.dumps({"product": "AUTOSYSTEM", "prompt_titles": ["Nome"]}), REPLY))
            db.execute("INSERT INTO queue_items VALUES('q',1,?,'completed',?)",
                       (PROMPT, json.dumps({"application_version": VERSION})))
        migrated = HistoryStore(RunStore(path))
        record, = migrated.details(["legacy"])
        self.assertEqual((record["title"], record["prompt_text"], record["application_version"]), ("Nome", PROMPT, VERSION))
        self.assertEqual(migrated.page().totals["total"], 1)
        RunStore(path)


class HistoryTkTests(unittest.TestCase):
    def test_filters_selection_comparison_rerun_and_queue_at_small_window(self):
        from time import monotonic, sleep
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tk indisponível.")
        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("Sessão gráfica indisponível.")
        self.addCleanup(root.destroy)
        from app import desktop
        from src.chatprobe import desktop_preferences
        from src.chatprobe.desktop_history import HistoryDetail
        from src.chatprobe.provider_catalog import ProviderCatalog

        def wait_for(predicate):
            deadline = monotonic() + 4
            while monotonic() < deadline:
                root.update()
                if predicate():
                    return
                sleep(0.02)
            self.fail("Interface não concluiu a operação assíncrona.")

        with TemporaryDirectory() as directory:
            path = Path(directory)
            database = path / "history.sqlite3"
            report = {"tests": [
                {"id": name, "test": i, "title": name, "mode": "json", "product": "AUTOSYSTEM",
                 "prompt_text": '{"initial_message":"Cupom","steps":[]}', "status": "completed",
                 "termination_reason": "completed", "url": "https://example.invalid/chat",
                 "turns": [{"message": "Cupom", "replies": ["Confirme o nome"]}]}
                for i, name in enumerate(("Primeiro", "Segundo"), 1)
            ]}
            import_reports(RunStore(database), report)
            with patch.object(desktop, "DEFAULT_DATABASE", database), patch.object(
                desktop_preferences, "SETTINGS_FILE", path / "settings.json",
            ), patch.object(desktop, "ProviderCatalog", side_effect=lambda *args, **kwargs: ProviderCatalog(
                *args, **kwargs, path=path / "providers.json",
            )), patch.object(desktop.messagebox, "askyesno", return_value=True):
                app = desktop.ChatProbeWindow(root)
                root.geometry("800x620")
                app.tabs.select(app.history_tab)
                view = app.history
                wait_for(lambda: len(view.listing.get_children()) == 2)
                self.assertTrue(view.listing.winfo_ismapped())
                self.assertTrue(view.export_button.winfo_ismapped())
                view.query.insert(0, "Confirme")
                view.filters = {"status": "completed", "product": "AUTOSYSTEM"}
                view.refresh(reset=True)
                wait_for(lambda: view.status.get().startswith("Ctrl"))
                view.listing.selection_set(("Primeiro", "Segundo"))
                wait_for(lambda: view.compare_button.instate(["!disabled"]))
                view.compare_button.invoke()
                wait_for(lambda: any(isinstance(component, HistoryDetail)
                                     for widget in view.winfo_children() if widget.winfo_class() == "Toplevel"
                                     for pane in widget.winfo_children() for component in pane.winfo_children()))
                view.listing.selection_set("Primeiro")
                wait_for(lambda: view.last_record and view.last_record["id"] == "Primeiro")
                self.assertEqual(view.detail.conversation.messages[0][1], "Cupom")
                view.rerun_button.invoke()
                wait_for(lambda: app.rerun_draft is not None)
                self.assertFalse(app.running)
                self.assertEqual(app.tabs.select(), app.test_tab)
                self.assertEqual(app.rerun_draft.source, "Primeiro")
                with patch.object(app, "launch_execution") as launch:
                    app.start()
                self.assertEqual(launch.call_args.args[0]["source_test_id"], "Primeiro")
                app.enqueue_history(view.history.details(("Primeiro", "Segundo")))
                self.assertEqual(app.night_queue.origins, ("Primeiro", "Segundo"))
                self.assertEqual(app.tabs.select(), app.queue_tab)
