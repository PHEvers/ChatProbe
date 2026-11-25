"""Importação compatível e exportações locais do Histórico."""

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from uuid import uuid4

from src.chatprobe.browser.timing import utc_now
from src.chatprobe.storage.history_metadata import (
    PROVIDER_FIELDS, fields, public_detail, public_settings, public_url, utc_value,
)


TOKEN_FIELDS = {"input_tokens", "output_tokens", "total_tokens"}
EXPORT_FIELDS = (
    "id", "batch_id", "test", "source_test_id", "title", "prompt_text", "product", "url", "mode",
    "execution_type", "started_at", "ended_at", "duration_seconds", "status", "termination_reason",
    "application_version", "interactions", "requests", "known_tokens", "estimated_tokens",
    "model_seconds", "processing_seconds", "detail", "provider", "settings", "token_usage",
    "wait_seconds", "turns", "events", "api_calls",
)


def text_value(value):
    return value if isinstance(value, str) else None


def identifier_value(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", value) else None


def count_value(value):
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def duration_value(value):
    return float(value) if type(value) in (int, float) and 0 <= value < math.inf else None


def token_fields(value):
    return {key: amount for key, amount in fields(value, TOKEN_FIELDS).items() if count_value(amount) is not None}


@dataclass
class ImportResult:
    imported: int = 0
    duplicates: int = 0
    invalid: int = 0


def candidates(payload):
    if isinstance(payload, list) and payload:
        metadata = payload[0].get("test_metadata", {}) if isinstance(payload[0], dict) else {}
        yield {**(metadata if isinstance(metadata, dict) else {}), "turns": payload}, {}
    elif isinstance(payload, dict) and isinstance(payload.get("tests"), list) and payload["tests"]:
        for item in payload["tests"]:
            yield item, payload
    else:
        raise ValueError("Formato JSON do ChatProbe não reconhecido.")


def by_position(values, number):
    if isinstance(values, list) and number and number <= len(values):
        return text_value(values[number - 1])
    return None


def normalize_import(item, group):
    if not isinstance(item, dict) or not isinstance(item.get("turns"), list):
        raise ValueError("Registro sem lista de turnos.")
    turns = []
    for turn in item["turns"]:
        if (not isinstance(turn, dict) or not isinstance(turn.get("message"), str)
                or not isinstance(turn.get("replies"), list)
                or any(not isinstance(reply, str) for reply in turn["replies"])):
            raise ValueError("Turno inválido.")
        stamps = turn.get("reply_received_at")
        stamps = stamps if isinstance(stamps, list) else []
        turns.append({"message": turn["message"], "sent_at": utc_value(turn.get("sent_at")), "replies": turn["replies"],
                      "reply_received_at": [utc_value(stamps[i]) if i < len(stamps) else None
                                            for i in range(len(turn["replies"]))]})
    number = count_value(item.get("test"))
    provider = fields(item.get("provider"), PROVIDER_FIELDS)
    mode = item.get("mode")
    if mode not in ("json", "groq", "ollama", "ai"):
        mode = {"legacy-groq": "groq", "legacy-ollama": "ollama"}.get(provider.get("id"), "ai" if provider else "unknown")
    prompt = text_value(item.get("prompt_text"))
    if prompt is None:
        prompt = by_position(group.get("prompts"), number)
    if mode == "unknown" and prompt:
        try:
            raw = json.loads(prompt)
            if isinstance(raw, dict) and "initial_message" in raw and "steps" in raw:
                mode = "json"
        except ValueError:
            pass
    result = {
        "id": identifier_value(item.get("id")), "batch_id": identifier_value(item.get("batch_id")) or identifier_value(group.get("queue_id")),
        "test": number, "mode": mode, "title": text_value(item.get("title")) or text_value(item.get("prompt_title"))
        or by_position(group.get("prompt_titles"), number), "prompt_text": prompt,
        "product": text_value(item.get("product")), "url": public_url(item.get("url")),
        "settings": public_settings(item.get("settings")), "provider": provider,
        "started_at": utc_value(item.get("started_at")), "ended_at": utc_value(item.get("ended_at")),
        "duration_seconds": duration_value(item.get("duration_seconds")),
        "processing_seconds": duration_value(item.get("processing_seconds")),
        "status": text_value(item.get("status")) or "unknown",
        "termination_reason": text_value(item.get("termination_reason")),
        "detail": public_detail(item.get("detail")), "token_usage": token_fields(item.get("token_usage")),
        "application_version": text_value(item.get("application_version")) or text_value(group.get("application_version")),
        "execution_type": text_value(item.get("execution_type")) or ("queue" if isinstance(group.get("prompts"), list)
                                                                   else "batch" if group else None),
        "source_test_id": text_value(item.get("source_test_id")),
        "requests": count_value(item.get("requests")),
        "turns": turns,
    }
    if result["status"] == "running":
        result["status"] = "stopped"
        result["termination_reason"] = "interrupted"
    result["events"] = [
        {"kind": public_detail(event.get("kind")), "occurred_at": utc_value(event.get("occurred_at"))}
        for event in item.get("events", []) if isinstance(event, dict) and isinstance(event.get("kind"), str)
    ] if isinstance(item.get("events"), list) else []
    calls = item.get("api_calls")
    result["api_calls"] = [extract_call(call) for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
    if result["requests"] is None and result["api_calls"]:
        result["requests"] = len(result["api_calls"])
    result["estimated_tokens"] = count_value(item.get("estimated_tokens"))
    if result["estimated_tokens"] is None and result["api_calls"]:
        estimates = [call["estimated"] for call in result["api_calls"] if call["estimated"] is not None]
        result["estimated_tokens"] = sum(estimates) if estimates else None
    result["model_seconds"] = duration_value(item.get("model_seconds"))
    measured = [call["actual"] if call["actual"] is not None else call["token_usage"].get("total_tokens")
                for call in result["api_calls"]]
    measured = [value for value in measured if value is not None]
    if "total_tokens" not in result["token_usage"] and measured:
        total = count_value(sum(measured))
        if total is not None:
            result["token_usage"]["total_tokens"] = total
    if result["model_seconds"] is None:
        durations = [call["processing_seconds"] for call in result["api_calls"]
                     if call["processing_seconds"] is not None]
        result["model_seconds"] = duration_value(sum(durations)) if durations else None
    waits = item.get("wait_seconds")
    result["wait_seconds"] = {
        public_detail(label): seconds for label, seconds in waits.items() if duration_value(seconds) is not None
    } if isinstance(waits, dict) else {}
    return result


def extract_call(call):
    return {"started_at": utc_value(call.get("started_at")), "outcome": public_detail(call.get("outcome")),
            "interaction": count_value(call.get("interaction")), "estimated": count_value(
                call.get("estimated", call.get("estimated_tokens"))),
            "actual": count_value(call.get("actual")), "token_usage": token_fields(call.get("token_usage")),
            "processing_seconds": duration_value(call.get("processing_seconds")),
            "retry_after": duration_value(call.get("retry_after", call.get("retry_after_seconds")))}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def import_reports(store, payload):
    outcome = ImportResult()
    group_id = "import:" + digest(payload)[:32]
    with store.transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        for index, (item, group) in enumerate(candidates(payload), 1):
            try:
                record = normalize_import(item, group)
            except (TypeError, ValueError):
                outcome.invalid += 1
                continue
            identifier = record["id"]
            batch_id = record["batch_id"] or group_id
            number = record["test"] or index
            fingerprint = digest(record)
            existing = db.execute("""SELECT 1 FROM tests WHERE import_hash=? OR id=?
                                    OR (batch_id=? AND test_number=?)""",
                                  (fingerprint, identifier, batch_id, number)).fetchone()
            if existing:
                outcome.duplicates += 1
                continue
            identifier = identifier or uuid4().hex
            source = record["source_test_id"]
            if not source or not db.execute("SELECT 1 FROM tests WHERE id=?", (source,)).fetchone():
                source = None
            db.execute("""INSERT INTO tests (id,batch_id,test_number,mode,started_at,ended_at,duration_seconds,
              status,termination_reason,provider_json,token_usage_json,title,prompt_text,product,target_url,
              execution_type,settings_json,application_version,detail,import_hash,imported_at,
              interactions_recorded,requests_recorded,source_test_id,wait_json,processing_seconds,imported_metrics_json)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (identifier, batch_id, number, record["mode"], record["started_at"] or "", record["ended_at"],
                        record["duration_seconds"], record["status"], record["termination_reason"],
                        json.dumps(record["provider"]), json.dumps(record["token_usage"]),
                        record["title"], record["prompt_text"], record["product"], record["url"],
                        record["execution_type"], json.dumps(record["settings"]), record["application_version"],
                        record["detail"], fingerprint, utc_now(), len(record["turns"]), record["requests"], source,
                        json.dumps(record["wait_seconds"]), record["processing_seconds"],
                        json.dumps({name: record[name] for name in ("api_calls", "estimated_tokens", "model_seconds")})))
            for turn_number, turn in enumerate(record["turns"], 1):
                db.execute("INSERT INTO messages VALUES (?,?,'user',0,?,?)",
                           (identifier, turn_number, turn["message"], turn["sent_at"] or ""))
                db.executemany("INSERT INTO messages VALUES (?,?,'bot',?,?,?)", (
                    (identifier, turn_number, position, reply, stamp or "")
                    for position, (reply, stamp) in enumerate(zip(turn["replies"], turn["reply_received_at"]))
                ))
            db.executemany("INSERT INTO events(test_id,kind,occurred_at) VALUES (?,?,?)", (
                (identifier, event["kind"], event["occurred_at"] or "") for event in record["events"]
            ))
            outcome.imported += 1
    return outcome


def export_records(records, format):
    rows = [{key: record[key] for key in EXPORT_FIELDS if key in record} for record in records]
    if format == "json":
        return json.dumps({"format": "chatprobe-history", "tests": rows}, ensure_ascii=False, indent=2) + "\n"
    if format == "csv":
        columns = tuple(key for key in EXPORT_FIELDS[:22] if key not in {"prompt_text", "detail"}) + (
            "provider_name", "model", "input_tokens", "output_tokens")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(columns)
        for record in rows:
            data = {**record, "provider_name": record.get("provider", {}).get("name"),
                    "model": record.get("provider", {}).get("model"),
                    **token_fields(record.get("token_usage"))}
            writer.writerow(safe_cell(data.get(column)) for column in columns)
        return output.getvalue()
    if format != "md":
        raise ValueError("Formato de exportação inválido.")
    lines = ["# Conversas do ChatProbe"]
    for row in rows:
        lines.extend(["", "## Teste " + str(row["id"]), "",
                      markdown_block(json.dumps({key: val for key, val in row.items() if key != "turns"},
                                                ensure_ascii=False, indent=2))])
        for turn in row.get("turns", ()):
            lines.extend(["", "### Cliente · " + (turn.get("sent_at") or "horário desconhecido"),
                          markdown_block(turn["message"])])
            timestamps = turn.get("reply_received_at") or []
            for index, reply in enumerate(turn["replies"]):
                time = timestamps[index] if index < len(timestamps) else None
                lines.extend(["", "### Chatbot · " + (time or "horário desconhecido"), markdown_block(reply)])
    return "\n".join(lines) + "\n"


def safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def markdown_block(text):
    fence = "`" * max(3, max((len(match) + 1 for match in re.findall(r"`+", text)), default=0))
    return f"{fence}\n{text}\n{fence}"
