import json
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch

from src.chatprobe.browser import smoke
from src.chatprobe.provider_catalog import LEGACY_OLLAMA, ProviderCatalog
from src.chatprobe.providers import Provider, ProviderError, ProviderProfile
from src.chatprobe.runner.batch import run_batch


ACTION = '{"action":"send","text":"Meu cupom não saiu."}'


class ProtocolTests(unittest.TestCase):
    def test_normalizes_four_protocols_and_token_counts(self):
        cases = [
            ("openai", "/chat/completions",
             {"choices": [{"message": {"content": ACTION}}],
              "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}),
            ("ollama", "/api/chat",
             {"message": {"content": ACTION}, "prompt_eval_count": 2, "eval_count": 3}),
            ("gemini", "/models/example:generateContent",
             {"candidates": [{"content": {"parts": [{"text": ACTION}]}}],
              "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3, "totalTokenCount": 5}}),
            ("anthropic", "/messages",
             {"content": [{"type": "text", "text": ACTION}], "usage": {"input_tokens": 2, "output_tokens": 3}}),
        ]
        for kind, endpoint, response in cases:
            with self.subTest(kind=kind), patch.object(Provider, "request", return_value=response) as request:
                profile = ProviderProfile("profile", "Teste", kind, "https://example.invalid",
                                          model="example", temperature=0.4)
                action = Provider(profile).test_generation()
                self.assertEqual(action.kind, "send")
                self.assertEqual(action.usage, {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5})
                self.assertEqual(request.call_args.args[0], endpoint)
                payload = request.call_args.args[1]
                self.assertNotIn("key", payload)
                if kind == "gemini":
                    self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
                if kind == "anthropic":
                    self.assertIn("system", payload)
                if kind == "ollama":
                    self.assertEqual(payload["options"]["temperature"], 0.4)

    def test_api_auth_headers_are_protocol_specific(self):
        expected = {"openai": "Authorization", "ollama": "Authorization",
                    "anthropic": "X-api-key", "gemini": "X-goog-api-key"}
        for kind, header in expected.items():
            with self.subTest(kind=kind), patch("src.chatprobe.providers.build_opener") as opener:
                stream = opener.return_value.open.return_value.__enter__.return_value
                stream.read.return_value = "{}"
                profile = ProviderProfile("profile", "Teste", kind, "https://example.invalid")
                Provider(profile, "test-secret").request("/models")
                request = opener.return_value.open.call_args.args[0]
                expected_value = "Bearer test-secret" if header == "Authorization" else "test-secret"
                self.assertEqual(request.get_header(header), expected_value)
                self.assertNotIn("test-secret", request.full_url)

    def test_http_statuses_and_redirects_are_safe(self):
        observed = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                observed.append(self.path)
                status, body = {
                    "/ok/models": (200, {"data": [{"id": "present"}]}),
                    "/bad/models": (401, {"error": "test-secret"}),
                    "/limit/models": (429, {}),
                    "/redirect/models": (307, {}),
                    "/missing/models": (404, {}),
                }[self.path]
                self.send_response(status)
                if status == 307:
                    self.send_header("Location", "/ok/models")
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_POST(self):
                observed.append(self.path)
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200 if payload["model"] == "present" else 400)
                self.end_headers()
                self.wfile.write(json.dumps({
                    "choices": [{"message": {"content": ACTION}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3},
                }).encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = f"http://127.0.0.1:{server.server_port}"
                profile = ProviderProfile("profile", "Local", "openai", root + "/ok")
                self.assertEqual(Provider(profile).check_connection(), ["present"])
                self.assertEqual(Provider(replace(profile, model="present")).check_connection(), ["present"])
                with self.assertRaisesRegex(ProviderError, "Modelo inexistente"):
                    Provider(replace(profile, model="missing")).check_connection()
                for route, code in (("bad", "auth"), ("limit", "rate_limit"),
                                    ("redirect", "endpoint"), ("missing", "endpoint")):
                    with self.subTest(route=route), self.assertRaises(ProviderError) as error:
                        Provider(replace(profile, base_url=f"{root}/{route}"), "test-secret").check_connection()
                    self.assertEqual(error.exception.code, code)
                    self.assertNotIn("test-secret", str(error.exception))
                self.assertEqual(observed.count("/ok/models"), 3)
                self.assertEqual(Provider(replace(profile, model="present")).test_generation().usage["total_tokens"], 5)
                self.assertIn("/ok/chat/completions", observed)
            finally:
                server.shutdown()


class CatalogTests(unittest.TestCase):
    def test_migration_is_idempotent_and_keys_are_session_only_without_windows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            catalog = ProviderCatalog(path=path, legacy_ollama_model="legacy-model")
            original = catalog.profiles[LEGACY_OLLAMA]
            catalog.set_key(original, "test-secret")
            duplicate = catalog.duplicate(original)
            catalog.set_default(duplicate.id)
            self.assertEqual(catalog.resolve(original).key, "test-secret")
            self.assertEqual(catalog.resolve(duplicate).key, "")
            self.assertNotIn("test-secret", path.read_text())
            reopened = ProviderCatalog(path=path, legacy_ollama_model="different")
            self.assertEqual(reopened.default, duplicate.id)
            self.assertEqual(reopened.profiles[LEGACY_OLLAMA].model, "legacy-model")
            self.assertEqual(reopened.resolve(original).key, "")
            catalog.remove(duplicate.id)
            self.assertNotIn(duplicate.id, catalog.profiles)

    def test_auth_is_rejected_over_plain_remote_http(self):
        profile = ProviderProfile("remote", "Remoto", "openai", "http://example.invalid/v1")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            Provider(profile, "test-secret")


class ProviderRunnerTests(unittest.TestCase):
    def test_records_immutable_profile_and_usage_without_key(self):
        profile = ProviderProfile("example", "Local", "openai", "https://example.invalid", model="unit")
        provider = Provider(profile, "test-secret")
        events = Queue()

        def chat(argv, *, log, scenario, next_message, turns, on_turn):
            self.assertEqual(next_message(turns), "Meu cupom não saiu.")
            turns.append({"message": "Meu cupom não saiu.", "replies": ["Entendido."]})
            self.assertIsNone(next_message(turns))

        response = {"choices": [{"message": {"content": ACTION}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3}}
        with TemporaryDirectory() as directory:
            database = Path(directory) / "run.sqlite3"
            with patch.object(smoke, "main", side_effect=chat), patch.object(
                Provider, "request", return_value=response,
            ):
                run_batch("https://example.invalid", 1, 1, events, objective="Teste", ai_provider=provider,
                          max_customer_messages=1, database_path=database)
            result, = [value for kind, value in list(events.queue) if kind == "result"]
            self.assertEqual(result["termination_reason"], "message_limit")
            self.assertEqual(result["provider"]["model"], "unit")
            self.assertEqual(result["token_usage"]["total_tokens"], 5)
            self.assertNotIn("test-secret", json.dumps(result))
            with closing(sqlite3.connect(database)) as connection:
                saved = connection.execute("SELECT provider_json, token_usage_json FROM tests").fetchone()
            self.assertEqual(json.loads(saved[0]), result["provider"])
            self.assertEqual(json.loads(saved[1]), result["token_usage"])
