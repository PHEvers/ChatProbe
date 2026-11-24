"""Executa lotes de sessões isoladas e preserva o resultado de cada teste."""

import re
import sqlite3
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event, Lock
from unicodedata import combining, normalize
from uuid import uuid4

from src.chatprobe import groq_api, ollama_api
from src.chatprobe.browser import smoke
from src.chatprobe.browser.replies import message_text_key
from src.chatprobe.browser.timing import ExecutionTiming
from src.chatprobe.storage.sqlite_store import RunStore


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

    def stop(self):
        self.stopped.set()
        self.ready.set()

    def check_stop(self):
        if self.stopped.is_set():
            raise BatchStopped


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
             max_customer_messages=DEFAULT_MAX_CUSTOMER_MESSAGES, control=None, store=None, batch_id=None):
    timing = ExecutionTiming()
    turns = []
    status = "completed"
    detail = ""
    reason = "completed"
    name_sent = False
    provider = "o Ollama" if ollama_model else "a Groq"
    next_action = ollama_api.next_action if ollama_model else groq_api.next_action
    model_or_key = ollama_model or key
    test_id = None

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
        mode = "ollama" if ollama_model else "groq" if key else "json"
        test_id = persist(store.start_test, batch_id or uuid4().hex, number, mode, timing.started_at)

    def save_turn(turn_number, turn):
        if store is not None:
            persist(store.save_turn, test_id, turn_number, turn)

    def checkpoint():
        if control is not None:
            control.check_stop()
        if paused.is_set():
            raise BatchPaused

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
            progress(f"Aguardando a próxima ação d{provider}." if attempt == 0
                     else f"{provider.capitalize()} sugeriu repetir uma mensagem. Solicitando nova resposta.")
            if variation is not None:
                options["opening_variation"] = variation.context(number)
            action, text = next_action(model_or_key, objective, history, product=product, **options)
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
        if store is not None:
            browser_options["on_turn"] = save_turn
        smoke.main(args, log=progress, scenario=scenario, turns=turns,
                   next_message=next_message if model_or_key else None, **browser_options)
    except BatchStopped:
        status, reason, detail = "stopped", "interrupted", "Teste interrompido a pedido do usuário."
    except (groq_api.GroqLimitError, ollama_api.OllamaLimitError) as error:
        paused.set()
        status, reason, detail = "rate_limited", "api_limit", str(error)
        events.put(("limit", detail))
    except BatchPaused:
        status, reason, detail = "paused", "api_limit", f"Lote pausado após atingir um limite d{provider}."
    except (Exception, SystemExit) as error:
        status, reason, detail = "error", "technical_error", str(error)

    result = {"test": number, "status": status, "termination_reason": reason, "turns": turns,
              "detail": detail, **timing.fields()}
    if test_id is not None:
        result["id"] = test_id
    if store is not None:
        persist(store.finish_test, test_id, result)
    return result


def run_batch(url, total, parallel, events, *, scenario=None, objective="", key=None,
              product=smoke.DEFAULT_PRODUCT, customer_name=None, ollama_model=None,
              max_customer_messages=DEFAULT_MAX_CUSTOMER_MESSAGES, test_inputs=None, headless=False,
              control=None, database_path=None):
    paused = Event()
    run_control = control if control is not None else BatchControl()
    guided = bool(key or ollama_model)
    opening_variations = OpeningVariations() if guided and test_inputs is None and total > 1 else None
    try:
        if guided and max_customer_messages < 1:
            raise ValueError("O limite de mensagens do cliente deve ser maior que zero.")
        if test_inputs is not None and len(test_inputs) != total:
            raise ValueError("A quantidade de testes deve coincidir com a fila de prompts.")
        store = open_store(database_path, events) if database_path is not None else None
        batch_id = uuid4().hex
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            running = set()
            number = 1

            def has_pending_tests():
                return number <= total and not paused.is_set() and not run_control.stopped.is_set()

            while running or has_pending_tests():
                if not running:
                    run_control.ready.wait()
                while has_pending_tests() and run_control.ready.is_set() and len(running) < parallel:
                    test_scenario, test_objective = (
                        (scenario, objective) if test_inputs is None else test_inputs[number - 1]
                    )
                    future = pool.submit(run_test, number, url, test_scenario, test_objective, key,
                                         paused, events, product=product, customer_name=customer_name,
                                         opening_variations=opening_variations, ollama_model=ollama_model,
                                         headless=headless, max_customer_messages=max_customer_messages,
                                         control=control, store=store, batch_id=batch_id)
                    running.add(future)
                    number += 1
                if running:
                    done, running = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        events.put(("result", future.result()))
    except Exception as error:
        events.put(("batch_error", str(error)))
    finally:
        events.put(("finished", None))


def open_store(path, events):
    try:
        return RunStore(path)
    except (OSError, sqlite3.Error):
        events.put(("storage_error", "Falha ao abrir o SQLite. Os relatórios JSON continuam disponíveis."))
        return None
