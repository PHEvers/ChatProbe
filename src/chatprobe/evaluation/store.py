"""Persistência separada das avaliações automáticas e humanas."""

import json

from src.chatprobe.browser.timing import utc_now


class EvaluationStore:
    def __init__(self, store):
        self.store = store

    def load_rubric(self, scopes):
        with self.store.transaction() as db:
            for scope in scopes:
                row = db.execute("SELECT revision,snapshot FROM evaluation_rubrics WHERE scope=?", (scope,)).fetchone()
                if row:
                    return dict(json.loads(row[1]), revision=row[0])
        return {"criteria": [], "threshold": 80, "reference": ""}

    def save_rubric(self, scope, data):
        data = {key: data[key] for key in ("criteria", "threshold", "reference", "expected", "provider_id", "automatic_local")
                if key in data}
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False)
        with self.store.transaction() as db:
            db.execute("INSERT INTO evaluation_rubrics VALUES(?,1,?) ON CONFLICT(scope) DO UPDATE SET "
                       "revision=revision+1,snapshot=excluded.snapshot WHERE snapshot<>excluded.snapshot",
                       (scope, encoded))
        return self.load_rubric([scope])

    def save(self, payload):
        with self.store.transaction() as db:
            db.execute("INSERT INTO evaluations VALUES(?,?,?,?)", (payload["id"], payload["test_id"],
                       payload["created_at"], json.dumps(payload, ensure_ascii=False)))

    def for_test(self, test_id):
        with self.store.transaction() as db:
            evaluations = [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM evaluations WHERE test_id=? ORDER BY created_at DESC,id DESC", (test_id,))]
            for item in evaluations:
                item["reviews"] = [dict(json.loads(data), created_at=stamp) for data, stamp in db.execute(
                    "SELECT payload,created_at FROM evaluation_reviews WHERE evaluation_id=? ORDER BY id",
                    (item["id"],))]
                if item["reviews"]:
                    item["review"] = item["reviews"][-1]
        return evaluations

    def review(self, evaluation_id, classification, reason, criteria=None):
        if classification not in {"passed", "failed", "inconclusive"} or not reason.strip():
            raise ValueError("Informe classificação e justificativa.")
        with self.store.transaction() as db:
            from src.chatprobe.evaluation.engine import STATUSES
            row = db.execute("SELECT payload FROM evaluations WHERE id=?", (evaluation_id,)).fetchone()
            if not row:
                raise ValueError("Avaliação indisponível.")
            allowed = {item["id"] for item in json.loads(row[0])["results"]}
            if (criteria is not None and (not isinstance(criteria, dict)
                    or any(key not in allowed or not isinstance(value, str) or value not in STATUSES
                           for key, value in criteria.items()))):
                raise ValueError("Revisão de critérios inválida.")
            db.execute("INSERT INTO evaluation_reviews(evaluation_id,created_at,payload) VALUES(?,?,?)",
                       (evaluation_id, utc_now(), json.dumps({"classification": classification, "reason": reason, "criteria": criteria or {}})))

    def baseline(self, scope, evaluations=None):
        with self.store.transaction() as db:
            if evaluations is not None:
                db.execute("INSERT INTO evaluation_baselines VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET "
                           "payload=excluded.payload", (scope, json.dumps(evaluations, ensure_ascii=False)))
                return evaluations
            row = db.execute("SELECT payload FROM evaluation_baselines WHERE scope=?", (scope,)).fetchone()
        return json.loads(row[0]) if row else []
