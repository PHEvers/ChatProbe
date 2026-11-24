import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from unittest.mock import patch

from src.chatprobe import desktop_preferences as preferences, groq_api, ollama_api
from src.chatprobe.browser import smoke
from src.chatprobe.browser.timing import utc_now
from src.chatprobe.chat_url import DEFAULT_CHAT_URL, validate_chat_url
from src.chatprobe.runner import batch
from src.chatprobe.runner.reports import execution_summary, legacy_conversation
from src.chatprobe.storage.sqlite_store import MIGRATIONS, RunStore


class ContextTests(unittest.TestCase):
    def conversation(self, actions, replies):
        replies = iter(replies)

        def chat(argv, *, log, scenario, next_message, turns):
            for message in smoke.guided_messages(next_message, turns):
                turns.append({"message": message, "replies": [next(replies)]})

        with patch.object(smoke, "main", side_effect=chat), patch.object(
            groq_api, "next_action", side_effect=actions,
        ) as model:
            result = batch.run_test(1, "https://example.invalid", None, "Objetivo", "chave-ficticia",
                                    Event(), Queue())
        return result, model

    def test_each_confirmation_can_respond_to_different_steps(self):
        for word in ("Ok", "Feito", "Sim", "Certo"):
            with self.subTest(word=word):
                result, _ = self.conversation(
                    [("send", "O cupom não saiu"), ("send", word), ("send", word.upper() + "!"),
                     ("stop", "Objetivo atingido.")],
                    ["Reinicie a impressora.", "Confira a fila de impressão.", "Teste concluído."],
                )
                self.assertEqual(len(result["turns"]), 3)
                self.assertEqual(result["termination_reason"], "completed")

    def test_loop_can_return_to_an_earlier_nonconsecutive_step(self):
        result, model = self.conversation(
            [("send", "O cupom não saiu"), ("send", "Sim"), ("send", "Feito"),
             ("send", "SIM!"), ("send", "Sim")],
            ["Você reiniciou?", "Confira o cabo.", "VOCE REINICIOU!"],
        )
        self.assertEqual(result["termination_reason"], "loop")
        self.assertEqual(len(result["turns"]), 3)
        self.assertEqual(model.call_args.kwargs["rejected_message"], "SIM!")

    def test_model_interruption_is_distinct_from_completion(self):
        result, _ = self.conversation(
            [("send", "O cupom não saiu"), ("interrupt", "O suporte exigiu acesso remoto.")],
            ["Abra o acesso remoto."],
        )
        self.assertEqual((result["status"], result["termination_reason"]), ("interrupted", "interrupted"))

    def test_runtime_reasons_keep_legacy_status(self):
        for error, status, reason in [
            (groq_api.GroqLimitError("Limite de teste"), "rate_limited", "api_limit"),
            (ollama_api.OllamaLimitError("Limite de teste"), "rate_limited", "api_limit"),
            (RuntimeError("Falha de teste"), "error", "technical_error"),
            (batch.BatchStopped(), "stopped", "interrupted"),
        ]:
            with self.subTest(reason=reason), patch.object(smoke, "main", side_effect=error):
                result = batch.run_test(1, "https://example.invalid", None, "", None, Event(), Queue())
                self.assertEqual((result["status"], result["termination_reason"]), (status, reason))
        result, _ = self.conversation(
            [("send", f"Mensagem {i}") for i in range(6)], ["Continue."] * 6,
        )
        self.assertEqual((result["status"], result["termination_reason"]), ("completed", "message_limit"))


class UrlPreferenceTests(unittest.TestCase):
    def test_round_trip_and_legacy_default(self):
        with TemporaryDirectory() as directory, patch.object(
            preferences, "SETTINGS_FILE", Path(directory) / "settings.json",
        ):
            preferences.SETTINGS_FILE.write_text('{"customer_name":"Ana"}', encoding="utf-8")
            original = preferences.load_preferences()
            self.assertEqual(original.chat_url, DEFAULT_CHAT_URL)
            updated = replace(original, chat_url="  https://example.invalid:8443/chat?q=1  ")
            preferences.save_preferences(updated)
            self.assertEqual(preferences.load_preferences(), replace(updated, chat_url=updated.chat_url.strip()))
            with self.assertRaises(ValueError):
                preferences.save_preferences(replace(updated, chat_url="file:///chat"))
            self.assertEqual(preferences.load_preferences().chat_url, updated.chat_url.strip())
            preferences.SETTINGS_FILE.write_text('{"chat_url":"javascript:foo"}', encoding="utf-8")
            self.assertEqual(preferences.load_preferences().chat_url, DEFAULT_CHAT_URL)

    def test_rejects_malformed_addresses(self):
        for url in ("", "example.invalid", "https:///chat", "https://name:password@example.invalid",
                    "https://a..b", "https://a b", "https://[::1]wrong", "https://host:65536",
                    "https://host:", "https://host:0"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_chat_url(url)
        self.assertEqual(validate_chat_url("http://[::1]:8080/chat"), "http://[::1]:8080/chat")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "chatprobe.sqlite3"

    def query(self, statement):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute(statement).fetchall()

    def test_fresh_database_migrates_and_is_reopenable(self):
        RunStore(self.path)
        RunStore(self.path)
        self.assertEqual(self.query("PRAGMA user_version"), [(len(MIGRATIONS),)])
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"),
                         [("events",), ("messages",), ("prompts",), ("tests",)])

    def test_migrates_version_one_preserving_data(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in MIGRATIONS[0]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 1")
            connection.execute(
                """INSERT INTO tests (id, batch_id, test_number, mode, started_at, status)
                   VALUES ('original', 'lote', 1, 'json', '2026-01-01T00:00:00+00:00', 'completed')""",
            )
        RunStore(self.path)
        self.assertEqual(self.query("PRAGMA user_version"), [(len(MIGRATIONS),)])
        self.assertEqual(self.query("SELECT id, status FROM tests"), [("original", "completed")])
        self.assertEqual(self.query("SELECT COUNT(*) FROM prompts"), [(0,)])

    def test_does_not_downgrade_newer_schema(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("PRAGMA user_version = 100")
        with self.assertRaises(sqlite3.DatabaseError):
            RunStore(self.path)
        self.assertEqual(self.query("PRAGMA user_version"), [(100,)])

    def test_concurrent_tests_persist_before_ending_and_preserve_partial_turns(self):
        events = Queue()
        waiting = Barrier(2)

        def chat(argv, *, log, scenario, next_message, turns, on_turn):
            self.assertEqual(argv[0], "https://example.invalid/chat")
            log("Triagem concluída.")
            turn = {"message": "Teste de impressão", "sent_at": utc_now(),
                    "replies": [], "reply_received_at": []}
            turns.append(turn)
            on_turn(1, turn)
            waiting.wait(5)
            self.assertEqual(self.query("SELECT COUNT(*) FROM tests WHERE status = 'running'"), [(2,)])
            self.assertEqual(self.query("SELECT COUNT(*) FROM messages WHERE role = 'user'"), [(2,)])
            waiting.wait(5)
            turn.update(replies=["Primeira resposta", "Segunda resposta"],
                        reply_received_at=[utc_now(), utc_now()])
            on_turn(1, turn)
            waiting.wait(5)
            raise RuntimeError("Conexão encerrada")

        with patch.object(smoke, "main", side_effect=chat):
            batch.run_batch("https://example.invalid/chat", 2, 2, events, database_path=self.path)

        results = [value for kind, value in list(events.queue) if kind == "result"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["detail"] == "Conexão encerrada" for result in results), results)
        self.assertEqual(self.query("SELECT COUNT(DISTINCT batch_id) FROM tests"), [(1,)])
        self.assertEqual(self.query("SELECT status, termination_reason FROM tests"),
                         [("error", "technical_error")] * 2)
        self.assertEqual(self.query("SELECT COUNT(*) FROM messages"), [(6,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM events"), [(6,)])
        for result in results:
            self.assertEqual(datetime.fromisoformat(result["started_at"]).tzinfo, UTC)
            self.assertLessEqual(result["started_at"], result["ended_at"])
            self.assertGreaterEqual(result["duration_seconds"], 0)
            self.assertEqual(len(result["turns"][0]["replies"]), 2)

    def test_database_failure_still_produces_json_result(self):
        self.path.mkdir()
        events = Queue()
        with patch.object(smoke, "main"):
            batch.run_batch("https://example.invalid", 1, 1, events, database_path=self.path)
        kinds = [kind for kind, _ in list(events.queue)]
        self.assertEqual(kinds, ["storage_error", "result", "finished"])


class ReportCompatibilityTests(unittest.TestCase):
    def test_legacy_array_remains_readable_and_adds_test_metadata(self):
        first = {"message": "Olá", "replies": ["Olá"], "sent_at": utc_now(), "reply_received_at": [utc_now()]}
        second = {"message": "Sim", "replies": ["Entendi"]}
        result = {"test": 1, "status": "completed", "termination_reason": "completed",
                  "turns": [first, second], "started_at": utc_now(), "ended_at": utc_now(),
                  "duration_seconds": 0.125}
        report = json.loads(json.dumps(legacy_conversation(result)))
        self.assertIsInstance(report, list)
        self.assertEqual([(t["message"], t["replies"]) for t in report],
                         [("Olá", ["Olá"]), ("Sim", ["Entendi"])])
        self.assertEqual(report[0]["test_metadata"]["duration_seconds"], 0.125)
        self.assertNotIn("test_metadata", first)
        self.assertIn("Conclusão", execution_summary(report[0]["test_metadata"]))
        self.assertEqual(execution_summary({"detail": "Dados de um relatório antigo."}),
                         "Dados de um relatório antigo.")
