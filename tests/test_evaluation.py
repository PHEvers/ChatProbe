import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from src.chatprobe.evaluation.engine import evaluate, regression
from src.chatprobe.evaluation.store import EvaluationStore
from src.chatprobe.providers import ProviderProfile
from src.chatprobe.runner.consumption import ConsumptionLedger
from src.chatprobe.storage.sqlite_store import RunStore


class EvaluationTests(unittest.TestCase):
    def test_local_weight_mandatory_and_regression(self):
        rubric = {"criteria": [
            {"id": "step", "title": "Orientação", "type": "contains", "value": "revisar", "required": True},
            {"id": "finish", "title": "Fim", "type": "status", "value": "completed", "weight": 6},
        ]}
        record = {"id": "record", "turns": [{"message": "Ajuda", "replies": ["Revise"]}], "status": "completed"}
        failed = evaluate(record, rubric)
        record["turns"][0]["replies"] = ["Revisar configuração"]
        passed = evaluate(record, rubric)
        self.assertEqual(failed["classification"], "failed")
        self.assertEqual(passed["score"], 100)
        self.assertEqual(regression(passed, failed), "regression")
        self.assertEqual(regression(passed, evaluate(record, dict(rubric, reference="nova"))), "not_comparable")

    def test_model_evidence_and_separate_ledger(self):
        with TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "test.sqlite3")
            test = store.start_test("b", 1, "json", "2026-01-01T00:00:00+00:00")
            record = {"id": test, "turns": [{"message": "Ajuda", "replies": ["Confira a impressora"]}]}
            rubric = {"reference": "Confira a impressora", "criteria": [
                {"id": "one", "title": "Resposta", "type": "semantic", "value": "Aderência", "required": True}
            ]}
            provider = Mock()
            provider.profile = ProviderProfile("local", "Mock", "openai", "https://example.invalid", "example")
            provider.complete.return_value = ('{"results":[{"id":"one","status":"met","reason":"ok",'
                                              '"evidence":[{"message_id":"1:bot:1","quote":"inexistente"}]}]}',
                                              {"total_tokens": 8})
            result = evaluate(record, rubric, provider=provider, ledger=ConsumptionLedger(store))
            self.assertEqual(result["classification"], "inconclusive")
            self.assertEqual(len(result["calls"]), 1)
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT test_id,actual FROM api_requests").fetchone(), (None, 8))
            history = EvaluationStore(store)
            history.save(result)
            history.review(result["id"], "failed", "Análise humana")
            saved = history.for_test(test)[0]
            self.assertEqual(saved["classification"], "inconclusive")
            self.assertEqual(saved["review"]["classification"], "failed")


    def test_automatic_local_is_idempotent_and_clears_pending_view(self):
        from src.chatprobe.desktop_evaluation import evaluate_automatic
        from src.chatprobe.storage.history import HistoryStore
        with TemporaryDirectory() as directory:
            database = RunStore(Path(directory) / "test.sqlite3")
            identifier = database.start_test("g", 1, "json", "2026-01-01T00:00:00+00:00")
            database.finish_test(identifier, {"ended_at": "2026-01-01T00:00:01+00:00",
                                               "status": "completed", "termination_reason": "completed",
                                               "duration_seconds": 1})
            service = HistoryStore(database)
            evaluations = EvaluationStore(database)
            evaluations.save_rubric("global", {"automatic_local": True, "criteria": [
                {"id": "done", "title": "Concluído", "type": "status", "value": "completed"}
            ]})
            self.assertEqual(service.matching_ids(filters={"batch": "g"}, pending_evaluation=True), [identifier])
            self.assertEqual(evaluate_automatic(service, [identifier]), 1)
            self.assertEqual(evaluate_automatic(service, [identifier]), 0)
            self.assertEqual(service.matching_ids(filters={"batch": "g"}, pending_evaluation=True), [])
