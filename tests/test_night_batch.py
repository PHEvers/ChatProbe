import unittest
from queue import Queue
from unittest.mock import patch

from src.chatprobe import ollama_api
from src.chatprobe.browser import smoke
from src.chatprobe.runner import batch


class NightBatchTests(unittest.TestCase):
    def test_night_batch_executes_each_prompt_without_a_browser_window(self):
        events = Queue()
        prompts = ("Relate um erro de impressão.", "Relate uma rejeição fiscal.")

        def chat(argv, *, log, scenario, next_message, turns):
            self.assertIn("--headless", argv)
            for message in smoke.guided_messages(next_message, turns):
                turns.append({"message": message, "replies": ["Entendido."]})

        def model_action(model, objective, turns, *, product):
            return "send", objective

        with patch.object(smoke, "main", side_effect=chat), patch.object(
            ollama_api, "next_action", side_effect=model_action,
        ):
            batch.run_batch(
                "https://example.invalid", 2, 1, events, ollama_model="qwen3:4b",
                max_customer_messages=1, test_inputs=tuple((None, prompt) for prompt in prompts),
                headless=True,
            )

        results = [value for kind, value in iter(events.get_nowait, ("finished", None)) if kind == "result"]
        self.assertEqual([result["status"] for result in results], ["completed", "completed"])
        self.assertEqual([result["turns"][0]["message"] for result in results], list(prompts))

    def test_stop_preserves_started_conversation_and_skips_pending_tests(self):
        events = Queue()
        control = batch.BatchControl()
        recorded = {"message": "Meu cupom não saiu.", "replies": ["Qual o seu nome?"]}

        def chat(argv, *, log, scenario, next_message, turns, checkpoint):
            turns.append(recorded)
            control.stop()
            checkpoint()

        with patch.object(smoke, "main", side_effect=chat) as browser:
            batch.run_batch("https://example.invalid", 3, 1, events, control=control)

        browser.assert_called_once()
        result, = [value for kind, value in iter(events.get_nowait, ("finished", None)) if kind == "result"]
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["turns"], [recorded])
