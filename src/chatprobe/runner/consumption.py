"""Reservas transacionais de consumo e recuperação de falhas do provedor."""

import json
import math
from dataclasses import astuple, dataclass
from random import uniform
from time import monotonic, time
from uuid import uuid4
from urllib.parse import urlsplit

from src.chatprobe.model_actions import TESTER_INSTRUCTIONS
from src.chatprobe.providers import ProviderError


@dataclass(frozen=True)
class Quota:
    rpm: int = 0
    rpd: int = 0
    tpm: int = 0
    tpd: int = 0

    @classmethod
    def parse(cls, text):
        try:
            values = [int(value) for value in text.split()]
            if len(values) not in (0, 4) or any(value < 0 for value in values):
                raise ValueError
        except ValueError:
            raise ValueError("Informe RPM RPD TPM TPD, separados por espaço. Use 0 para desativar.") from None
        return cls(*values)

    @property
    def enabled(self):
        return any(astuple(self))

    def intersect(self, other):
        return Quota(*(min(a, b) if a and b else a or b for a, b in zip(astuple(self), astuple(other))))

    def windows(self, estimate):
        return ((self.rpm, 60, 1, False), (self.rpd, 86400, 1, False),
                (self.tpm, 60, estimate, True), (self.tpd, 86400, estimate, True))


@dataclass(frozen=True)
class ExecutionOptions:
    interaction_seconds: float = 0
    test_seconds: float = 0
    queue_quota: str = ""
    retries: int = 0
    max_wait: float = 120

    def __post_init__(self):
        values = (self.interaction_seconds, self.test_seconds, self.max_wait)
        if any(not math.isfinite(value) or value < 0 for value in values) or not 0 <= self.retries <= 10:
            raise ValueError("Use intervalos não negativos e entre 0 e 10 novas tentativas.")
        Quota.parse(self.queue_quota)


class LocalQuotaError(Exception):
    pass


def quota_scope(profile, metadata):
    if profile and profile.quota_scope:
        return "shared:" + profile.quota_scope
    base = urlsplit(profile.base_url if profile else (
        "https://api.groq.com" if metadata["name"] == "Groq" else "http://127.0.0.1:11434"
    ))
    return f"origin:{base.hostname}:{base.port or (443 if base.scheme == 'https' else 80)}"


def quota_scopes(profile, metadata, batch_id, policy, related_profiles=()):
    if not metadata:
        return []
    shared = quota_scope(profile, metadata)
    model = metadata["model"]
    global_quota = Quota.parse(profile.quota) if profile else Quota()
    model_quota = Quota.parse(profile.model_quota) if profile else Quota()
    for candidate in related_profiles:
        if quota_scope(candidate, candidate.metadata()) == shared:
            global_quota = global_quota.intersect(Quota.parse(candidate.quota))
            if candidate.model == model:
                model_quota = model_quota.intersect(Quota.parse(candidate.model_quota))
    scopes = [(shared, global_quota), (f"model:{shared}:{model}", model_quota)]
    if policy.queue_quota:
        scopes.append(("queue:" + batch_id, Quota.parse(policy.queue_quota)))
    return scopes


def estimate_tokens(objective, history, options, output_budget=1024):
    # UTF-8 bytes are a conservative proxy for input tokens, with room for framing.
    context = json.dumps([objective, history, options], ensure_ascii=False)
    return len((TESTER_INSTRUCTIONS + context).encode("utf-8")) + 128 + output_budget


class ConsumptionLedger:
    def __init__(self, store, clock=time):
        self.store = store
        self.clock = clock

    def reserve(self, scopes, estimate, test_id=None, *, interaction=None, model=None):
        """Returns (reservation, delay). Transactions cover all applicable scopes."""
        with self.store.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()
            delay = 0.0
            for scope, quota in scopes:
                cooldown = connection.execute("SELECT until FROM api_cooldowns WHERE scope=?", (scope,)).fetchone()
                if cooldown:
                    delay = max(delay, cooldown[0] - now)
                rows = connection.execute(
                    """SELECT started, COALESCE(actual, estimated) FROM api_requests
                       JOIN api_scopes ON api_scopes.request_id=api_requests.id
                       WHERE scope=? AND started>? ORDER BY started""", (scope, now - 86400),
                ).fetchall() if quota != Quota() else ()
                for limit, period, requested, tokens in quota.windows(estimate):
                    if not limit:
                        continue
                    if requested > limit:
                        raise LocalQuotaError("A reserva estimada excede o limite local configurado.")
                    recent = [(stamp, amount if tokens else 1) for stamp, amount in rows if stamp > now - period]
                    excess = sum(amount for _, amount in recent) + requested - limit
                    if excess > 0:
                        for stamp, amount in recent:
                            excess -= amount
                            if excess <= 0:
                                delay = max(delay, stamp + period - now)
                                break
            if delay > 0:
                return None, delay
            request_id = uuid4().hex
            connection.execute(
                """INSERT INTO api_requests (id,test_id,started,estimated,interaction,model)
                   VALUES (?,?,?,?,?,?)""", (request_id, test_id, now, estimate, interaction, model))
            connection.executemany("INSERT INTO api_scopes VALUES (?,?)",
                                   ((request_id, scope) for scope, _ in scopes))
            return request_id, 0

    def finish(self, request_id, usage, outcome, *, retry_after=None, cooldown=None):
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE api_requests SET actual=?, usage_json=?, outcome=?, retry_after=? WHERE id=?",
                (usage.get("total_tokens"), json.dumps(usage) if usage else None, outcome, retry_after, request_id))

            if cooldown:
                scope, seconds = cooldown
                connection.execute(
                    """INSERT INTO api_cooldowns VALUES (?,?) ON CONFLICT(scope)
                       DO UPDATE SET until=MAX(until,excluded.until)""", (scope, self.clock() + seconds))

    def snapshot(self, scope):
        now = self.clock()
        with self.store.transaction() as connection:
            rows = connection.execute(
                """SELECT started,actual FROM api_requests JOIN api_scopes
                   ON api_scopes.request_id=api_requests.id WHERE scope=? AND started>?""",
                (scope, now - 86400)).fetchall()
        minute = [row for row in rows if row[0] > now - 60]
        def summarize(window):
            return (len(window), sum(actual for _, actual in window if actual is not None),
                    sum(actual is None for _, actual in window))
        return summarize(minute), summarize(rows)


def generate_with_controls(generate, objective, history, options, *, ledger, scopes, test_id,
                           policy, checkpoint, wait_for, records, model=None, clock=monotonic,
                           jitter=uniform):
    started = clock()
    cooldown_scope = scopes[0][0]
    for attempt in range(policy.retries + 1):
        checkpoint()
        reservation = None
        estimate = estimate_tokens(objective, history, options)
        while ledger is not None:
            reservation, delay = ledger.reserve(scopes, estimate, test_id, interaction=len(history)+1, model=model)
            if not delay:
                break
            if clock() - started + delay > policy.max_wait:
                raise LocalQuotaError("A cota local ou a suspensão compartilhada excede a espera configurada.")
            wait_for(delay, "Aguardando disponibilidade da API")
            checkpoint()
        record = {"interaction": len(history) + 1, "attempt": attempt + 1, "estimated_tokens": estimate}
        records.append(record)
        try:
            decision = generate(objective, history, **options)
        except ProviderError as error:
            record["outcome"] = error.code
            retry = error.code in {"rate_limit", "connection", "timeout", "temporary"} and (
                not error.daily or error.retry_after is not None
            )
            delay = error.retry_after
            if delay is None:
                delay = min(60, 2 ** attempt) * jitter(0.5, 1.0)
            if error.retry_after is not None:
                record["retry_after_seconds"] = error.retry_after
            if ledger is not None:
                ledger.finish(reservation, {}, error.code, retry_after=error.retry_after,
                              cooldown=(cooldown_scope, delay) if error.code == "rate_limit" else None)
            if not retry or attempt >= policy.retries or clock() - started + delay > policy.max_wait:
                raise
            wait_for(delay, "Aguardando nova tentativa")
        except Exception:
            record["outcome"] = "technical_error"
            if ledger is not None:
                ledger.finish(reservation, {}, "technical_error")
            raise
        else:
            record.update(outcome="ok", token_usage=decision.usage or None)
            if ledger is not None:
                ledger.finish(reservation, decision.usage, "ok")
            return decision
