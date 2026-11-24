"""Executa um roteiro da interface Streamlit em um subprocesso."""

import argparse
import json
from pathlib import Path
from queue import Queue
from threading import Event

from src.chatprobe.browser.smoke import load_scenario
from src.chatprobe.runner.batch import open_store, run_test
from src.chatprobe.runner.reports import legacy_conversation
from src.chatprobe.storage.sqlite_store import DEFAULT_DATABASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        scenario = load_scenario(args.scenario)
    except (OSError, ValueError) as error:
        parser.error(f"Roteiro inválido: {error}")

    events = Queue()
    store = open_store(DEFAULT_DATABASE, events)
    result = run_test(1, args.url, scenario, "", None, Event(), events, store=store)
    while not events.empty():
        kind, value = events.get_nowait()
        print(value[1] if kind == "progress" else value)
    if result["turns"]:
        report = legacy_conversation(result)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if result["status"] == "error":
        raise SystemExit(result["detail"])


if __name__ == "__main__":
    main()
