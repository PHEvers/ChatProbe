import json
import unittest
from queue import Queue
from unittest.mock import patch

from src.chatprobe import groq_api, ollama_api
from src.chatprobe.browser import smoke
from src.chatprobe.runner import batch


class OllamaTests(unittest.TestCase):
    def test_model_list_excludes_remote_models(self):
        with patch.object(ollama_api, "_request", return_value={"models": [
            {"name": "qwen3:4b"},
            {"name": "qwen3:4b"},
            {"name": "gpt-oss:120b-cloud"},
            {"name": "modelo-remoto", "remote_host": "https://ollama.com"},
        ]}):
            self.assertEqual(ollama_api.list_models(), ["qwen3:4b"])

    def test_batch_uses_local_model_without_key_and_reads_latest_reply(self):
        replies = iter(["Qual o seu nome?", "A impressora está ligada?", "Confira o cabo USB."])

        def chat(argv, *, log, scenario, next_message, turns):
            for message in smoke.guided_messages(next_message, turns):
                turns.append({"message": message, "replies": [next(replies)]})

        events = Queue()
        actions = ["Meu cupom não saiu.", "Sim, está ligada."]
        answers = [
            {"message": {"content": json.dumps({"action": "send", "text": action})}}
            for action in actions
        ]
        with patch.object(smoke, "main", side_effect=chat), patch.object(
            ollama_api, "_request", side_effect=answers,
        ) as request, patch.object(groq_api, "next_action") as groq:
            batch.run_batch(
                "https://example.invalid", 1, 1, events, objective="Testar cupom",
                ollama_model="qwen3:4b", max_customer_messages=3, customer_name="Ana Teste",
            )

        groq.assert_not_called()
        self.assertEqual(request.call_count, 2)
        endpoint, payload = request.call_args.args
        self.assertEqual(endpoint, "/api/chat")
        self.assertEqual(payload["model"], "qwen3:4b")
        self.assertFalse(payload["stream"])
        context = json.loads(payload["messages"][-1]["content"])
        self.assertEqual(context["histórico"][-1]["replies"], ["A impressora está ligada?"])
        result, = [value for kind, value in list(events.queue) if kind == "result"]
        self.assertEqual(result["status"], "completed")
        self.assertEqual([turn["message"] for turn in result["turns"]],
                         [actions[0], "Ana Teste", actions[1]])
        self.assertIn("(3)", result["detail"])
