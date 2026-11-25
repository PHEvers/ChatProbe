"""Fila persistente com reserva exclusiva e recuperação de itens pendentes."""

import json
from datetime import UTC, datetime, timedelta
from time import time
from uuid import uuid4

from src.chatprobe.browser.timing import utc_now


LEASE_SECONDS = 90


def scheduled_timestamp(value, timezone=""):
    from zoneinfo import ZoneInfo
    value = datetime.fromisoformat(value)
    if timezone and value.tzinfo is not None:
        raise ValueError("Informe o horário sem deslocamento ao selecionar um fuso.")
    if timezone:
        local = value.replace(tzinfo=ZoneInfo(timezone))
        round_trip = datetime.fromtimestamp(local.timestamp(), local.tzinfo).replace(tzinfo=None)
        if round_trip != value or local.replace(fold=1).utcoffset() != local.utcoffset():
            raise ValueError("Escolha um horário fora da transição do horário local.")
        value = local
    timestamp = value.timestamp()
    if timestamp <= time():
        raise ValueError("Escolha data e hora futuras.")
    return timestamp


class QueueStore:
    def __init__(self, store):
        self.store = store

    def create(self, snapshot, prompts, scheduled=None):
        identifier = uuid4().hex
        with self.store.transaction() as db:
            db.execute("INSERT INTO execution_queues VALUES (?,?,?,?,NULL,?)",
                       (identifier, json.dumps(snapshot), "scheduled" if scheduled else "pending",
                        scheduled, utc_now()))
            db.executemany("INSERT INTO queue_items VALUES (?,?,?,'pending',NULL)",
                           ((identifier, i, prompt) for i, prompt in enumerate(prompts, 1)))
        return identifier

    def claim(self, identifier, owner):
        with self.store.transaction() as db:
            return bool(db.execute(
                """UPDATE execution_queues SET status='running',owner=?,updated_at=?
                   WHERE id=? AND status IN ('pending','scheduled','interrupted') AND EXISTS
                   (SELECT 1 FROM queue_items WHERE queue_id=? AND status='pending')""",
                (owner, utc_now(), identifier, identifier)).rowcount)

    def heartbeat(self, identifier, owner):
        with self.store.transaction() as db:
            db.execute("UPDATE execution_queues SET updated_at=? WHERE id=? AND owner=? AND status='running'",
                       (utc_now(), identifier, owner))

    def set_item(self, identifier, position, status, result=None):
        with self.store.transaction() as db:
            db.execute("UPDATE queue_items SET status=?,result=? WHERE queue_id=? AND position=?",
                       (status, json.dumps(result) if result else None, identifier, position))

    def reconcile_running(self, db, identifier):
        db.execute("""UPDATE tests SET status='stopped',termination_reason='interrupted',ended_at=?
                      WHERE batch_id=? AND status='running'""", (utc_now(), identifier))
        db.execute("""UPDATE queue_items SET status=COALESCE(
                      (SELECT status FROM tests WHERE batch_id=? AND test_number=position), 'pending')
                      WHERE queue_id=? AND status='running'""", (identifier, identifier))

    def close(self, identifier, owner):
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            owned = db.execute("SELECT 1 FROM execution_queues WHERE id=? AND owner=? AND status='running'",
                               (identifier, owner)).fetchone()
            if not owned:
                return
            self.reconcile_running(db, identifier)
            db.execute(
                """UPDATE execution_queues SET status=CASE WHEN EXISTS
                   (SELECT 1 FROM queue_items WHERE queue_id=? AND status='pending')
                   THEN 'interrupted' ELSE 'finished' END,owner=NULL,updated_at=? WHERE id=? AND owner=?""",
                (identifier, utc_now(), identifier, owner))

    def cancel(self, identifier):
        with self.store.transaction() as db:
            return bool(db.execute(
                "UPDATE execution_queues SET status='cancelled',updated_at=? WHERE id=? AND status='scheduled'",
                (utc_now(), identifier)).rowcount)

    def unschedule(self, identifier):
        with self.store.transaction() as db:
            db.execute("UPDATE execution_queues SET status='pending' WHERE id=? AND status='scheduled'",
                       (identifier,))

    def recover_stale(self):
        cutoff = (datetime.now(UTC) - timedelta(seconds=LEASE_SECONDS)).isoformat(timespec="milliseconds")
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM execution_queues WHERE status='running' AND updated_at<?",
                              (cutoff,)).fetchall()
            for (identifier,) in rows:
                self.reconcile_running(db, identifier)
                db.execute("UPDATE execution_queues SET status='interrupted',owner=NULL,updated_at=? WHERE id=?",
                           (utc_now(), identifier))

    def available(self):
        with self.store.transaction() as db:
            return db.execute("""SELECT id,status,scheduled_at FROM execution_queues
                                 WHERE status='scheduled' OR (status IN ('interrupted','pending') AND EXISTS
                                 (SELECT 1 FROM queue_items WHERE queue_id=id AND status='pending'))
                                 ORDER BY updated_at""").fetchall()

    def read(self, identifier):
        with self.store.transaction() as db:
            row = db.execute("SELECT snapshot FROM execution_queues WHERE id=?", (identifier,)).fetchone()
            items = db.execute("SELECT position,prompt,status,result FROM queue_items WHERE queue_id=? ORDER BY position",
                               (identifier,)).fetchall()
            restored = []
            for number, prompt, status, result in items:
                payload = json.loads(result) if result else None
                if payload is None and status not in {"pending", "running"}:
                    payload = self.restore_result(db, identifier, number)
                restored.append((number, prompt, status, payload))
        return json.loads(row[0]), restored

    def restore_result(self, db, identifier, number):
        row = db.execute(
            """SELECT id,status,termination_reason,started_at,ended_at,duration_seconds,
               provider_json,token_usage_json FROM tests WHERE batch_id=? AND test_number=?""",
            (identifier, number)).fetchone()
        if row is None:
            return None
        test_id, status, reason, start, end, duration, provider, usage = row
        turns = {}
        for position, role, text, stamp in db.execute(
            """SELECT turn_number,role,text,occurred_at FROM messages WHERE test_id=?
               ORDER BY turn_number,position""", (test_id,)):
            turn = turns.setdefault(position, {"replies": [], "reply_received_at": []})
            if role == "user":
                turn.update(message=text, sent_at=stamp)
            else:
                turn["replies"].append(text)
                turn["reply_received_at"].append(stamp)
        payload = {"id": test_id, "test": number, "status": status, "termination_reason": reason,
                   "started_at": start, "ended_at": end, "duration_seconds": duration,
                   "turns": list(turns.values()), "detail": "Resultado recuperado do SQLite."}
        if provider:
            payload["provider"] = json.loads(provider)
        if usage:
            payload["token_usage"] = json.loads(usage)
        return payload
