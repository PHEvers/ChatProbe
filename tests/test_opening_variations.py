import json
import unittest
from queue import Queue
from threading import Barrier
from unittest.mock import patch

from src.chatprobe import groq_api
from src.chatprobe.browser import smoke
from src.chatprobe.runner import batch


class OpeningVariationTests(unittest.TestCase):
    def run_batch(self, model, total):
        def chat(argv, *, log, scenario, next_message, turns):
            for message in smoke.guided_messages(next_message, turns):
                turns.append({"message": message, "replies": ["Qual erro aparece?"]})

        events = Queue()
        with patch.object(smoke, "main", side_effect=chat), patch.object(
            groq_api, "next_action", side_effect=model,
        ):
            batch.run_batch(
                "https://example.invalid", total, total, events,
                objective="Testar rejeição 1106 ao emitir NFC-e de combustível",
                key="chave-invalida", product="AUTOSYSTEM",
            )
        results = []
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == "result":
                results.append(value)
        return sorted(results, key=lambda result: result["test"])

    def test_parallel_sessions_retry_a_duplicate_and_keep_the_same_subject(self):
        simultaneous_drafts = Barrier(2)
        approaches = {}

        def model(key, objective, turns, *, product, opening_variation=None, rejected_message=None):
            self.assertEqual(objective, "Testar rejeição 1106 ao emitir NFC-e de combustível")
            self.assertEqual(product, "AUTOSYSTEM")
            if turns:
                self.assertIsNone(opening_variation)
                return "stop", "Fim do teste."
            if rejected_message:
                self.assertEqual(len(opening_variation["aberturas_já_preparadas"]), 1)
                return "send", "Por que a NFC-e de combustível dá rejeição 1106?"
            approaches[opening_variation["teste"]] = opening_variation["abordagem"]
            simultaneous_drafts.wait(timeout=3)
            if opening_variation["teste"] == 1:
                return "send", "A NFC-e de combustível dá rejeição 1106."
            return "send", "A NFC-E DE COMBUSTIVEL DA REJEICAO 1106!"

        results = self.run_batch(model, total=2)

        self.assertEqual([result["status"] for result in results], ["completed", "completed"])
        self.assertNotEqual(approaches[1], approaches[2])
        openings = [result["turns"][0]["message"] for result in results]
        self.assertEqual(len(set(map(batch.message_signature, openings))), 2)
        self.assertTrue(all("1106" in text for text in openings))

    def test_marks_remaining_test_interrupted_if_model_keeps_the_same_opening(self):
        def model(key, objective, turns, **options):
            if turns:
                return "stop", "Fim do teste."
            return "send", "A NFC-e de combustível dá rejeição 1106."

        results = self.run_batch(model, total=2)

        self.assertEqual({result["status"] for result in results}, {"completed", "interrupted"})
        self.assertEqual(sum(len(result["turns"]) for result in results), 1)

    def test_single_run_keeps_the_normal_model_context(self):
        def model(key, objective, turns, *, product):
            if turns:
                return "stop", "Fim do teste."
            return "send", "A NFC-e de combustível dá rejeição 1106."

        results = self.run_batch(model, total=1)

        self.assertEqual(results[0]["status"], "completed")

    def test_provider_receives_opening_approach(self):
        variation = {"teste": 2, "abordagem": "Pergunte a causa do erro."}
        answer = {"choices": [{"message": {"content": '{"action":"send","text":"Qual a causa?"}'}}]}
        with patch.object(groq_api, "_request", return_value=answer) as request:
            groq_api.next_action("chave-invalida", "Rejeição 1106", [], opening_variation=variation)

        messages = request.call_args.args[2]["messages"]
        self.assertEqual(json.loads(messages[-1]["content"])["variação_da_abertura"], variation)
