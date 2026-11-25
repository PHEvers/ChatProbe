"""Consultas paginadas e operações sobre o histórico local de testes."""

import json
from sqlite3 import Row
from dataclasses import dataclass
from datetime import UTC, datetime

from src.chatprobe.storage.history_metadata import (
    PROVIDER_FIELDS, fields, filter_time, public_detail, public_settings, public_url, utc_value,
)


FILTERS = {
    "library_prompt": "t.library_prompt_id", "library_collection": "t.library_collection_id",
    "library_collection_run": "t.library_collection_run_id",
    "product": "t.product", "provider": "json_extract(t.provider_json,'$.name')",
    "model": "json_extract(t.provider_json,'$.model')", "status": "t.status",
    "execution_type": "t.execution_type", "version": "t.application_version", "batch": "t.batch_id",
}
SORTS = {
    "date": "started_at", "duration": "duration_seconds", "interactions": "interactions",
    "tokens": "known_tokens",
}
SUMMARY = """
SELECT t.*, t.interactions_recorded AS interactions,
  COALESCE(t.requests_recorded,(SELECT NULLIF(COUNT(*),0) FROM api_requests WHERE test_id=t.id)) AS requests,
  COALESCE(json_extract(t.token_usage_json,'$.total_tokens'),
           (SELECT SUM(actual) FROM api_requests WHERE test_id=t.id)) AS known_tokens,
  COALESCE((SELECT SUM(estimated) FROM api_requests WHERE test_id=t.id),
           json_extract(t.imported_metrics_json,'$.estimated_tokens')) AS estimated_tokens,
  COALESCE((SELECT SUM(processing_seconds) FROM api_requests WHERE test_id=t.id),
           json_extract(t.imported_metrics_json,'$.model_seconds')) AS model_seconds
FROM tests t
"""


@dataclass(frozen=True)
class HistoryPage:
    rows: list
    totals: dict
    page: int
    pages: int


def condition(search, filters):
    parts, values = [], []
    for key, field in FILTERS.items():
        if filters.get(key):
            parts.append(f"{field}=?")
            values.append(filters[key])
    for name, operator in (("from", ">="), ("to", "<")):
        boundary = filter_time(filters.get(name), end=name == "to")
        if boundary:
            parts.append(f"t.started_at {operator} ?")
            values.append(boundary)
    search = search.strip().casefold()
    if search:
        columns = ("t.id", "t.title", "t.prompt_text", "t.product", FILTERS["provider"], FILTERS["model"],
                   "t.termination_reason", "t.detail",
                   """CASE t.termination_reason WHEN 'loop' THEN 'Loop de conversa'
                   WHEN 'completed' THEN 'Conclusão' WHEN 'interrupted' THEN 'Interrupção'
                   WHEN 'technical_error' THEN 'Erro técnico' WHEN 'api_limit' THEN 'Limite de API'
                   WHEN 'message_limit' THEN 'Limite de mensagens' WHEN 'local_quota' THEN 'Cota local' END""")
        parts.append("(" + " OR ".join(f"instr(fold({column}),?)>0" for column in columns) +
                     " OR EXISTS (SELECT 1 FROM messages m WHERE m.test_id=t.id AND instr(fold(m.text),?)>0))")
        values.extend([search] * (len(columns) + 1))
    return (" WHERE " + " AND ".join(parts) if parts else ""), values


def unpack(row):
    data = dict(row)
    data["settings"] = public_settings(json.loads(data.pop("settings_json") or "{}"))
    data["provider"] = fields(json.loads(data.pop("provider_json") or "{}"), PROVIDER_FIELDS)
    data["token_usage"] = json.loads(data.pop("token_usage_json") or "{}")
    data["wait_seconds"] = json.loads(data.pop("wait_json") or "null")
    data["url"] = public_url(data.pop("target_url"))
    data["detail"] = public_detail(data["detail"])
    for field in ("started_at", "ended_at"):
        data[field] = utc_value(data[field])
    return data


class HistoryStore:
    def __init__(self, store):
        self.store = store

    def page(self, search="", filters=None, *, page=0, page_size=30, order="date", descending=True):
        if order not in SORTS or not 1 <= page_size <= 100:
            raise ValueError("Ordenação ou tamanho de página inválido.")
        where, params = condition(search, filters or {})
        sort = SORTS[order]
        direction = "DESC" if descending else "ASC"
        with self.store.transaction() as db:
            db.row_factory = Row
            db.create_function("fold", 1, lambda value: str(value or "").casefold(), deterministic=True)
            db.execute("BEGIN")
            totals = dict(db.execute(f"""SELECT COUNT(*) AS total,
                COUNT(*) FILTER (WHERE termination_reason='completed'
                    OR (status='completed' AND termination_reason IS NULL)) AS completed,
                COUNT(*) FILTER (WHERE status IN ('interrupted','stopped','paused')) AS interrupted,
                COUNT(*) FILTER (WHERE status='error') AS errors,
                COUNT(*) FILTER (WHERE termination_reason='loop') AS loops,
                COUNT(*) FILTER (WHERE termination_reason='api_limit') AS limited,
                SUM(requests) AS requests, SUM(known_tokens) AS known_tokens,
                AVG(duration_seconds) AS average_duration FROM ({SUMMARY}{where})""", params).fetchone())
            pages = max(1, (totals["total"] + page_size - 1) // page_size)
            page = max(0, min(page, pages - 1))
            # Select only summary columns on the paginated path. Large prompts are loaded on demand.
            rows = db.execute(f"""SELECT id,batch_id,title,product,provider_json,status,started_at,
                duration_seconds,interactions,known_tokens,termination_reason,execution_type,application_version
                FROM ({SUMMARY}{where}) ORDER BY NULLIF({sort},'') IS NULL,{sort} {direction},id DESC
                LIMIT ? OFFSET ?""", (*params, page_size, page * page_size)).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["provider"] = fields(json.loads(item.pop("provider_json") or "{}"), PROVIDER_FIELDS)
            item["started_at"] = utc_value(item["started_at"])
            output.append(item)
        return HistoryPage(output, totals, page, pages)

    def matching_ids(self, search="", filters=None, *, pending_evaluation=False):
        where, params = condition(search, filters or {})
        if pending_evaluation:
            where += (" AND " if where else " WHERE ") + (
                "NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.test_id=t.id)")
        with self.store.transaction() as db:
            db.create_function("fold", 1, lambda value: str(value or "").casefold(), deterministic=True)
            return [row[0] for row in db.execute(f"SELECT t.id FROM tests t{where} ORDER BY t.started_at DESC", params)]

    def details(self, identifiers):
        result = []
        with self.store.transaction() as db:
            db.row_factory = Row
            db.execute("BEGIN")
            for identifier in dict.fromkeys(identifiers):
                row = db.execute(SUMMARY + " WHERE t.id=?", (identifier,)).fetchone()
                if row is None:
                    continue
                data = unpack(row)
                data["test"] = data.pop("test_number")
                imported = json.loads(data.pop("imported_metrics_json") or "{}")
                if data["batch_id"].startswith("import:"):
                    data["batch_id"] = None
                data["turns"] = self.turns(db, identifier)
                data["events"] = [dict(event) for event in db.execute(
                    "SELECT kind,occurred_at FROM events WHERE test_id=? ORDER BY NULLIF(occurred_at,'') IS NULL, occurred_at,id", (identifier,))]
                data["api_calls"] = []
                for call in db.execute("""SELECT started,estimated,actual,usage_json,outcome,retry_after,
                                                   interaction,model,processing_seconds
                                            FROM api_requests WHERE test_id=? ORDER BY started,id""", (identifier,)):
                    item = dict(call)
                    item["token_usage"] = json.loads(item.pop("usage_json") or "null")
                    item["started_at"] = datetime.fromtimestamp(item.pop("started"), UTC).isoformat()
                    data["api_calls"].append(item)
                if not data["api_calls"]:
                    data["api_calls"] = imported.get("api_calls", [])
                for event in data["events"]:
                    event["occurred_at"] = utc_value(event["occurred_at"])
                result.append(data)
        return result

    @staticmethod
    def turns(db, identifier):
        turns = {}
        for number, role, text, stamp in db.execute(
            """SELECT turn_number,role,text,occurred_at FROM messages WHERE test_id=?
               ORDER BY turn_number,role DESC,position""", (identifier,),
        ):
            item = turns.setdefault(number, {"replies": [], "reply_received_at": []})
            if role == "user":
                item.update(message=text, sent_at=utc_value(stamp))
            else:
                item["replies"].append(text)
                item["reply_received_at"].append(utc_value(stamp))
        for item in turns.values():
            sent = item.get("sent_at")
            reply = next((stamp for stamp in item["reply_received_at"] if stamp), None)
            if sent and reply:
                seconds = (datetime.fromisoformat(reply) - datetime.fromisoformat(sent)).total_seconds()
                if seconds >= 0:
                    item["response_seconds"] = seconds
        return list(turns.values())

    def delete(self, identifiers):
        count = 0
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            for identifier in dict.fromkeys(identifiers):
                row = db.execute("""SELECT t.status,t.batch_id,t.test_number,q.status
                                    FROM tests t LEFT JOIN execution_queues q ON q.id=t.batch_id WHERE t.id=?""",
                                 (identifier,)).fetchone()
                if row is None:
                    continue
                status, batch, number, queue_status = row
                if status == "running" or queue_status == "running":
                    raise ValueError("Há testes selecionados em andamento ou pertencentes a uma fila ativa.")
                db.execute("UPDATE api_requests SET test_id=NULL WHERE test_id=?", (identifier,))
                db.execute("DELETE FROM events WHERE test_id=?", (identifier,))
                db.execute("DELETE FROM messages WHERE test_id=?", (identifier,))
                db.execute("UPDATE queue_items SET result=NULL WHERE queue_id=? AND position=?", (batch, number))
                db.execute("DELETE FROM tests WHERE id=?", (identifier,))
                count += 1
        return count


def comparison_differences(left, right):
    """Compara apenas condições conhecidas e deixa lacunas explícitas."""
    keys = {"prompt_text": "Prompt ou roteiro", "product": "Produto", "provider": "Provedor e modelo",
            "settings": "Configurações", "url": "URL", "application_version": "Versão"}
    different, unknown = [], []
    for key, label in keys.items():
        first, second = left.get(key), right.get(key)
        if first is None or second is None or (key in {"provider", "settings"} and (not first or not second)):
            unknown.append(label)
        elif first != second:
            different.append(label)
    return different, unknown
