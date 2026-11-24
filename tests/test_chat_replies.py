import unittest
from queue import Queue
from threading import Event
from unittest.mock import Mock, patch

from src.chatprobe import groq_api
from src.chatprobe.browser import smoke
from src.chatprobe.browser.replies import ReplyCapture, message_text_key
from src.chatprobe.runner import batch


class ReplyCaptureTests(unittest.TestCase):
    def setUp(self):
        self.page = Mock()
        self.locator = Mock()
        self.capture = ReplyCapture(self.page, self.locator, use_events=True)
        self.outgoing = {message_text_key("Meu cupom não saiu."), message_text_key("Ana Teste")}
        self.page.evaluate.return_value = []
        self.locator.all_inner_texts.return_value = ["Como posso ajudar?", "Qual o seu nome?"]

    def test_browser_event_supplies_support_reply_after_name(self):
        self.page.evaluate.return_value = ["Como posso ajudar?", "Qual o seu nome?"]
        before = self.capture.snapshot(self.outgoing)
        self.locator.all_inner_texts.return_value = ["Qual o seu nome?", "Ana Teste"]
        self.assertEqual(self.capture._new_replies(before, self.outgoing), [])

        self.page.evaluate.return_value += ["Pode conferir se a impressora está ligada?"]
        self.assertEqual(self.capture._new_replies(before, self.outgoing),
                         ["Pode conferir se a impressora está ligada?"])

    def test_dom_fallback_ignores_echoes_after_history_is_trimmed(self):
        before = self.capture.snapshot(self.outgoing)
        self.locator.all_inner_texts.return_value = [
            "Qual o seu nome?", "  ANA   TESTE ", "Meu cupom não saiu.",
            "Pode conferir se a impressora está ligada?",
        ]
        self.assertEqual(self.capture._new_replies(before, self.outgoing),
                         ["Pode conferir se a impressora está ligada?"])

    def test_dom_fallback_reads_visible_reply_for_non_text_support_event(self):
        self.page.evaluate.return_value = ["Qual o seu nome?"]
        before = self.capture.snapshot(self.outgoing)
        self.page.evaluate.return_value += [None]
        self.locator.all_inner_texts.return_value += ["Escolha o modelo da impressora."]
        self.assertEqual(self.capture._new_replies(before, self.outgoing),
                         ["Escolha o modelo da impressora."])


class GuidedConversationTests(unittest.TestCase):
    def test_configurable_message_limit_counts_the_name_and_can_exceed_the_default(self):
        def chat(argv, *, log, scenario, next_message, turns):
            for message in smoke.guided_messages(next_message, turns):
                reply = "Qual o seu nome?" if not turns else "Pode continuar?"
                turns.append({"message": message, "replies": [reply]})

        def model_action(key, objective, turns, **kwargs):
            return "send", f"Mensagem {len(turns) + 1}"

        with patch.object(smoke, "main", side_effect=chat), patch.object(
            groq_api, "next_action", side_effect=model_action,
        ):
            for limit in (1, 2, 8):
                with self.subTest(limit=limit):
                    result = batch.run_test(
                        1, "https://example.invalid", None, "Testar atendimento", "chave-invalida",
                        Event(), Queue(), customer_name="Ana Teste", max_customer_messages=limit,
                    )
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(len(result["turns"]), limit)
                    self.assertIn(str(limit), result["detail"])
                    if limit > 1:
                        self.assertEqual(result["turns"][1]["message"], "Ana Teste")

    def run_conversation(self, model_actions):
        actions = iter(model_actions)
        support_replies = []

        def model_action(key, objective, turns, **kwargs):
            support_replies.append(turns[-1]["replies"] if turns else [])
            return next(actions)

        def chat(argv, *, log, scenario, next_message, turns):
            responses = iter([
                ["Qual o seu nome?"],
                ["A impressora está ligada?"],
                ["Confira o cabo USB."],
            ])
            for message in smoke.guided_messages(next_message, turns):
                turns.append({"message": message, "replies": next(responses)})

        with patch.object(smoke, "main", side_effect=chat), patch.object(
            groq_api, "next_action", side_effect=model_action,
        ) as model:
            result = batch.run_test(
                1, "https://example.invalid", None, "Testar cupom", "chave-invalida",
                Event(), Queue(), customer_name="Ana Teste",
            )
            return result, model.call_args_list, support_replies

    def test_uses_support_reply_after_name_and_retries_repeated_draft(self):
        result, calls, support_replies = self.run_conversation([
            ("send", "Meu cupom não saiu."),
            ("send", "MEU CUPOM NÃO SAIU!"),
            ("send", "Sim, está ligada."),
            ("stop", "O suporte orientou a conferir o cabo."),
        ])
        self.assertEqual(result["status"], "completed")
        self.assertEqual([turn["message"] for turn in result["turns"]],
                         ["Meu cupom não saiu.", "Ana Teste", "Sim, está ligada."])
        self.assertEqual(support_replies[1], ["A impressora está ligada?"])
        self.assertEqual(calls[2].kwargs["rejected_message"], "MEU CUPOM NÃO SAIU!")

    def test_interrupts_if_model_persists_in_repeating(self):
        result, _, _ = self.run_conversation([
            ("send", "Meu cupom não saiu."),
            ("send", "Meu cupom não saiu."),
            ("send", "Meu cupom não saiu."),
        ])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual([turn["message"] for turn in result["turns"]],
                         ["Meu cupom não saiu.", "Ana Teste"])
