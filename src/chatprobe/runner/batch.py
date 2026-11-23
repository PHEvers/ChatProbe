"""Executa lotes de sessões isoladas e preserva o resultado de cada teste."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event

from src.chatprobe import groq_api
from src.chatprobe.browser import smoke


MAX_GUIDED_TURNS = 6


class BatchPaused(Exception):
    pass


def run_test(number, url, scenario, objective, key, paused, events):
    turns = []
    status = "completed"
    detail = ""

    def progress(message):
        nonlocal status, detail
        if message.startswith("Roteiro interrompido:"):
            status, detail = "interrupted", message
        events.put(("progress", (number, message)))

    def next_message(history):
        nonlocal detail
        if paused.is_set():
            raise BatchPaused
        if len(history) == MAX_GUIDED_TURNS:
            detail = f"Encerrado após {MAX_GUIDED_TURNS} mensagens do cliente."
            return None
        progress("Aguardando a próxima ação da Groq.")
        action, text = groq_api.next_action(key, objective, history)
        if paused.is_set():
            raise BatchPaused
        if action == "stop":
            detail = text
            return None
        return text

    try:
        if paused.is_set():
            raise BatchPaused
        smoke.main([url, "--non-interactive"], log=progress, scenario=scenario,
                   next_message=next_message if key else None, turns=turns)
    except groq_api.GroqLimitError as error:
        paused.set()
        status, detail = "rate_limited", str(error)
        events.put(("limit", detail))
    except BatchPaused:
        status, detail = "paused", "Lote pausado após atingir o limite da Groq."
    except (Exception, SystemExit) as error:
        status, detail = "error", str(error)

    return {"test": number, "status": status, "turns": turns, "detail": detail}


def run_batch(url, total, parallel, events, *, scenario=None, objective="", key=None):
    paused = Event()
    try:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            running = set()
            number = 1
            while running or (number <= total and not paused.is_set()):
                while number <= total and len(running) < parallel and not paused.is_set():
                    running.add(pool.submit(run_test, number, url, scenario, objective, key,
                                            paused, events))
                    number += 1
                if running:
                    done, running = wait(running, return_when=FIRST_COMPLETED)
                    for future in done:
                        events.put(("result", future.result()))
    except Exception as error:
        events.put(("batch_error", str(error)))
    finally:
        events.put(("finished", None))
