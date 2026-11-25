"""Executa lotes de sessões isoladas e preserva o resultado de cada teste."""

import json
import re
from dataclasses import asdict
import sqlite3
from math import ceil
from time import monotonic
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Condition, Event, Lock
from unicodedata import combining, normalize
from uuid import uuid4

from src.chatprobe.providers import ProviderError, legacy_metadata, legacy_provider, provider_failure
from src.chatprobe.browser import smoke
from src.chatprobe.browser.replies import message_text_key
from src.chatprobe.browser.timing import ExecutionTiming
from src.chatprobe.browser.version import VERSION
from src.chatprobe.storage.sqlite_store import RunStore
from src.chatprobe.runner.consumption import ConsumptionLedger, ExecutionOptions, LocalQuotaError, generate_with_controls, quota_scopes
from src.chatprobe.storage.queues import QueueStore
from src.chatprobe.storage.history_metadata import public_detail, public_settings, public_url


DEFAULT_MAX_CUSTOMER_MESSAGES = 6
CONFIRMATIONS = {"ok", "feito", "sim", "certo"}
OPENING_APPROACHES = (
    "Relate o sintoma diretamente e pergunte como resolver.",
    "Comece com uma pergunta sobre a causa e mencione o sintoma.",
    "Comece pela ação que o cliente estava tentando fazer e descreva o que aconteceu.",
    "Relate o que aconteceu e pergunte qual é a primeira verificação a fazer.",
    "Destaque a condição em que o problema ocorre, se o objetivo informar essa condição.",
    "Peça orientação passo a passo com um relato breve do problema.",
)


class BatchPaused(Exception):
    pass


class BatchStopped(Exception):
    pass


class BatchControl:
    def __init__(self):
        self.ready = Event()
        self.ready.set()
        self.stopped = Event()
        self.changed = Condition()
        self.audit_entries = []

    def pause(self):
        with self.changed:
            self.ready.clear()
            self.changed.notify_all()

    def resume(self):
        with self.changed:
            self.ready.set()
            self.changed.notify_all()

    def stop(self):
        with self.changed:
            self.stopped.set()
            self.ready.set()
            self.changed.notify_all()

    def audit(self, kind):
        from src.chatprobe.browser.timing import utc_now
        with self.changed:
            self.audit_entries.append((kind, utc_now()))

    def events_since(self, started_at):
        with self.changed:
            return [(kind, stamp) for kind, stamp in self.audit_entries if stamp >= started_at]

    def check_stop(self):
        if self.stopped.is_set():
            raise BatchStopped

    def checkpoint(self):
        with self.changed:
            while not self.ready.is_set():
                self.check_stop()
                self.changed.wait()
            self.check_stop()

    def wait(self, seconds, notify=lambda remaining: None):
        deadline = monotonic() + seconds
        while True:
            self.checkpoint()
            with self.changed:
                self.check_stop()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return
                notify(ceil(remaining))
                self.changed.wait(timeout=min(1, remaining))


def message_signature(text):
    plain = "".join(character for character in normalize("NFKD", text) if not combining(character))
    return " ".join(re.findall(r"\w+", message_text_key(plain)))


def support_signature(replies):
    return tuple(filter(None, map(message_signature, replies)))


def repeats_in_context(signature, history):
    if not history:
        return False
    current_context = support_signature(history[-1]["replies"])
    for index, turn in enumerate(history):
        if message_signature(turn["message"]) != signature:
            continue
        previous_context = support_signature(history[index - 1]["replies"]) if index else ()
        if not index or previous_context == current_context:
            return True
        if signature not in CONFIRMATIONS or not current_context:
            return True
    return False


class OpeningVariations:
    def __init__(self):
        self.messages = {}
        self.lock = Lock()

    def context(self, number):
        with self.lock:
            recent = list(self.messages.values())[-8:]
        return {
            "teste": number,
            "abordagem": OPENING_APPROACHES[(number - 1) % len(OPENING_APPROACHES)],
            "aberturas_já_preparadas": recent,
        }

    def reserve(self, signature, message):
        with self.lock:
            if signature in self.messages:
                return False
            self.messages[signature] = message
            return True


def run_test(number, url, scenario, objective, key, paused, events, *, product=smoke.DEFAULT_PRODUCT,
             customer_name=None, opening_variations=None, ollama_model=None, headless=False,
             max_customer_messages=DEFAULT_MAX_CUSTOMER_MESSAGES, control=None, store=None, batch_id=None, ai_provider=None,
             execution=None, ledger=None, related_profiles=(), history_context=None):
    policy = execution or ExecutionOptions()
    timing = ExecutionTiming()
    waits = {}
    api_calls = []
    metadata = ai_provider.profile.metadata() if ai_provider else legacy_metadata(key, ollama_model)
    scopes = quota_scopes(ai_provider.profile if ai_provider else None, metadata,
                          batch_id or "", policy, related_profiles)
    turns = []
    status = "completed"
    detail = ""
    reason = "completed"
    name_sent = False
    provider = ai_provider.profile.name if ai_provider else "Ollama" if ollama_model else "Groq"
    generate = ai_provider.generate if ai_provider else legacy_provider(key, ollama_model) if key or ollama_model else None
    usage = {}
    test_id = None
    mode = "ai" if ai_provider else "ollama" if ollama_model else "groq" if key else "json"
    history_context = history_context or {}
    prompt = history_context.get("prompt_text")
    if prompt is None and objective:
        prompt = objective
    if prompt is None and scenario is not None:
        initial, steps = scenario
        prompt = json.dumps({"initial_message": initial, "steps": [
            {"when_contains": condition, "message": message} for condition, message in steps
        ]}, ensure_ascii=False)
    history = {"title": history_context.get("title"), "prompt_text": prompt,
               "product": product, "url": public_url(url), "application_version": VERSION,
               "execution_type": history_context.get("execution_type", "single"),
               "source_test_id": history_context.get("source_test_id"),
               "settings": public_settings({"customer_name": customer_name or smoke.DEFAULT_CUSTOMER_NAME,
                                            "headless": headless, "max_customer_messages": max_customer_messages,
                                            "parallel": history_context.get("parallel"),
                                            "total": history_context.get("total"), "execution": asdict(policy)})}

    def persist(operation, *args):
        nonlocal store
        if store is None:
            return None
        try:
            return operation(*args)
        except (OSError, sqlite3.Error):
            store = None
            events.put(("storage_error", "Falha na gravação SQLite. Os relatórios JSON continuam disponíveis."))
            return None

    if store is not None:
        test_id = persist(store.start_test, batch_id or uuid4().hex, number, mode, timing.started_at, metadata, history)

    def save_turn(turn_number, turn):
        if store is not None:
            persist(store.save_turn, test_id, turn_number, turn)

    def checkpoint():
        if control is not None:
            control.checkpoint()
        if paused.is_set():
            raise BatchPaused

    def wait_for(seconds, label):
        started = monotonic()
        try:
            (control or BatchControl()).wait(seconds, lambda remaining: progress(f"{label} · {remaining} s"))
        finally:
            waits[label] = waits.get(label, 0) + monotonic() - started

    def before_message():
        if turns and policy.interaction_seconds:
            remaining = policy.interaction_seconds - (monotonic() - last_send)
            if remaining > 0:
                wait_for(remaining, "Intervalo entre interações")
        checkpoint()

    last_send = 0.0

    def record_send():
        nonlocal last_send
        last_send = monotonic()

    def progress(message):
        nonlocal status, detail, reason
        if message.startswith("Roteiro interrompido:"):
            status, detail, reason = "interrupted", message, "interrupted"
        if store is not None:
            if message.startswith("Triagem concluída"):
                persist(store.event, test_id, "triage_completed")
            elif message.startswith("Roteiro interrompido:"):
                persist(store.event, test_id, "script_interrupted")
        events.put(("progress", (number, message)))

    def next_message(history):
        nonlocal detail, name_sent, status, reason
        checkpoint()
        if len(history) >= max_customer_messages:
            reason = "message_limit"
            detail = f"Limite de mensagens do cliente atingido ({max_customer_messages})."
            return None
        if history and smoke.asks_for_customer_name(history[-1]["replies"]):
            if name_sent:
                status, reason = "interrupted", "loop"
                detail = "O chatbot voltou a pedir o nome após receber o nome configurado."
                return None
            name_sent = True
            progress("O chatbot pediu o nome. Enviando o nome configurado.")
            return customer_name or smoke.DEFAULT_CUSTOMER_NAME
        variation = opening_variations if not history else None
        options = {}
        for attempt in range(2):
            checkpoint()
            progress(f"Aguardando a próxima ação de {provider}." if attempt == 0
                     else f"{provider.capitalize()} sugeriu repetir uma mensagem. Solicitando nova resposta.")
            if variation is not None:
                options["opening_variation"] = variation.context(number)
            decision = generate_with_controls(
                generate, objective, history, {"product": product, **options}, ledger=ledger,
                scopes=scopes, test_id=test_id, policy=policy, checkpoint=checkpoint, wait_for=wait_for,
                records=api_calls, model=metadata["model"],
            )
            for metric, count in decision.usage.items():
                usage[metric] = usage.get(metric, 0) + count
            action, text = decision.kind, decision.text
            checkpoint()
            if action in ("stop", "interrupt"):
                if not history:
                    raise ValueError(f"{provider.capitalize()} não gerou o relato inicial. Revise o objetivo do teste.")
                if action == "interrupt":
                    status, reason = "interrupted", "interrupted"
                detail = text
                return None
            signature = message_signature(text)
            if not repeats_in_context(signature, history) and (
                variation is None or variation.reserve(signature, text)
            ):
                return text
            options["rejected_message"] = text
        status, reason = "interrupted", "loop" if variation is None else "interrupted"
        detail = (f"{provider.capitalize()} voltou a sugerir uma abertura já usada neste lote."
                  if variation is not None else
                  f"{provider.capitalize()} voltou a sugerir uma mensagem já enviada. O ChatProbe interrompeu o teste.")
        return None

    try:
        checkpoint()
        args = [url, "--non-interactive", f"--product={product}"]
        if headless:
            args.append("--headless")
        if customer_name is not None:
            args.append(f"--customer-name={customer_name}")
        browser_options = {"checkpoint": checkpoint} if control is not None else {}
        if execution is not None and control is not None:
            browser_options["check_stop"] = control.check_stop
        if store is not None:
            browser_options["on_turn"] = save_turn
        if policy.interaction_seconds:
            browser_options["before_message"] = before_message
            browser_options["on_send"] = record_send
        smoke.main(args, log=progress, scenario=scenario, turns=turns,
                   next_message=next_message if generate else None, **browser_options)
    except BatchStopped:
        status, reason, detail = "stopped", "interrupted", "Teste interrompido a pedido do usuário."
    except LocalQuotaError as error:
        paused.set()
        status, reason, detail = "rate_limited", "local_quota", str(error)
        events.put(("limit", detail))
    except ProviderError as error:
        detail = str(error)
        if error.code == "rate_limit":
            paused.set()
            status, reason = "rate_limited", "api_limit"
            events.put(("limit", detail))
        else:
            status, reason = "error", "technical_error"
    except BatchPaused:
        status, reason, detail = "paused", "api_limit", f"Lote pausado após atingir um limite de {provider}."
    except (Exception, SystemExit) as error:
        failure = provider_failure(error)
        if failure and failure.code == "rate_limit":
            paused.set()
            status, reason, detail = "rate_limited", "api_limit", str(failure)
            events.put(("limit", detail))
        else:
            status, reason, detail = "error", "technical_error", str(error)

    result = {"test": number, "application_version": VERSION, "status": status, "termination_reason": reason, "turns": turns,
              "detail": public_detail(detail, key, ai_provider.key if ai_provider else None), **timing.fields(),
              **history, "mode": mode, "interactions": len(turns),
              "requests": len(api_calls) if generate else 0}
    if batch_id:
        result["batch_id"] = batch_id
    if waits:
        result["wait_seconds"] = {label: round(seconds, 3) for label, seconds in waits.items()}
        result["processing_seconds"] = max(0, round(result["duration_seconds"] - sum(waits.values()), 3))
    if api_calls:
        result["api_calls"] = api_calls
    if metadata is not None:
        result["provider"] = metadata
        if usage:
            result["token_usage"] = usage
    if test_id is not None:
        result["id"] = test_id
    if store is not None:
        if control is not None:
            for kind, stamp in control.events_since(timing.started_at):
                if store is not None:
                    persist(store.event, test_id, kind, stamp)
        if store is not None:
            persist(store.finish_test, test_id, result)
    return result


def run_batch(url, total, parallel, events, *, scenario=None, objective="", key=None,
              product=smoke.DEFAULT_PRODUCT, customer_name=None, ollama_model=None,
              max_customer_messages=DEFAULT_MAX_CUSTOMER_MESSAGES, test_inputs=None, headless=False,
              control=None, database_path=None, ai_provider=None, execution=None,
              queue_id=None, queue_owner=None, positions=None, related_profiles=(), history_items=None):
    paused = Event()
    policy = execution or ExecutionOptions()
    queues = None
    run_control = control if control is not None else BatchControl()
    guided = bool(ai_provider or key or ollama_model)
    opening_variations = OpeningVariations() if guided and test_inputs is None and total > 1 else None
    try:
        if guided and max_customer_messages < 1:
            raise ValueError("O limite de mensagens do cliente deve ser maior que zero.")
        if test_inputs is not None and len(test_inputs) != total:
            raise ValueError("A quantidade de testes deve coincidir com a fila de prompts.")
        store = open_store(database_path, events) if database_path is not None else None
        batch_id = queue_id or uuid4().hex
        queues = QueueStore(store) if queue_id and store else None
        profile = ai_provider.profile if ai_provider else None
        metadata = profile.metadata() if profile else legacy_metadata(key, ollama_model)
        scopes = quota_scopes(profile, metadata, batch_id, policy, related_profiles)
        quota_required = any(quota.enabled for _, quota in scopes)
        if (quota_required or queue_id) and store is None:
            raise ValueError("O SQLite é necessário para aplicar cotas ou executar esta fila.")
        ledger = ConsumptionLedger(store) if guided and store else None
        if ledger and execution is not None:
            events.put(("quota_scope", (scopes[0][0], metadata)))
        cooldown_until = 0.0
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            running = {}
            number = 1

            def has_pending_tests():
                return number <= total and not paused.is_set() and not run_control.stopped.is_set()

            while running or has_pending_tests():
                if not running:
                    run_control.ready.wait()
                if has_pending_tests() and run_control.ready.is_set() and len(running) < parallel:
                    delay = cooldown_until - monotonic()
                    if delay > 0 and not running:
                        run_control.wait(delay, lambda n: events.put(("queue_wait", n)))
                while (has_pending_tests() and run_control.ready.is_set() and len(running) < parallel
                       and monotonic() >= cooldown_until):
                    test_scenario, test_objective = (
                        (scenario, objective) if test_inputs is None else test_inputs[number - 1]
                    )
                    position = positions[number - 1] if positions is not None else number
                    if queues:
                        queues.set_item(queue_id, position, "running")
                    context = {**((history_items or {}).get(position, {})), "parallel": parallel,
                               "total": total, "execution_type": "queue" if queue_id else "batch" if total > 1 else "single"}
                    future = pool.submit(run_test, position, url, test_scenario, test_objective, key,
                                         paused, events, product=product, customer_name=customer_name,
                                         opening_variations=opening_variations, ollama_model=ollama_model,
                                         headless=headless, max_customer_messages=max_customer_messages,
                                         control=control, store=store, batch_id=batch_id, ai_provider=ai_provider,
                                         execution=execution, ledger=ledger, related_profiles=related_profiles, history_context=context)
                    running[future] = position
                    if execution is not None:
                        events.put(("active", position))
                    number += 1
                if running:
                    delay = cooldown_until - monotonic()
                    timeout = delay if delay > 0 and has_pending_tests() and len(running) < parallel else None
                    done, _ = wait(running, timeout=timeout, return_when=FIRST_COMPLETED)
                    if done:
                        cooldown_until = monotonic() + policy.test_seconds
                    for future in done:
                        position = running.pop(future)
                        result = future.result()
                        if queues:
                            queues.set_item(queue_id, position, result["status"], result)
                        events.put(("result", result))
    except BatchStopped:
        pass
    except Exception as error:
        events.put(("batch_error", str(error)))
    finally:
        try:
            if queues:
                queues.close(queue_id, queue_owner)
        except (OSError, sqlite3.Error):
            events.put(("storage_error", "Falha ao fechar o registro da fila. Os itens poderão ser recuperados."))
        finally:
            events.put(("finished", None))


def open_store(path, events):
    try:
        return RunStore(path)
    except (OSError, sqlite3.Error):
        events.put(("storage_error", "Falha ao abrir o SQLite. Os relatórios JSON continuam disponíveis."))
        return None
