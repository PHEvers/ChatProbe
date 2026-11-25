"""Metadados de execução autorizados para histórico e exportação."""

import math
import re
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit, urlunsplit


EXECUTION_FIELDS = {"interaction_seconds", "test_seconds", "queue_quota", "retries", "max_wait"}
SETTINGS_FIELDS = {"parallel", "total", "max_customer_messages", "headless", "customer_name"}
PROVIDER_FIELDS = {"id", "name", "protocol", "model", "temperature", "timeout_seconds"}


def fields(value, allowed):
    if not isinstance(value, dict):
        return {}
    return {key: val for key, val in value.items() if key in allowed and
            (val is None or isinstance(val, (str, bool, int, float))) and
            (not isinstance(val, float) or math.isfinite(val))}


def public_settings(value):
    result = fields(value, SETTINGS_FIELDS)
    rules = {"parallel": int, "total": int, "max_customer_messages": int, "headless": bool,
             "customer_name": str}
    result = {key: item for key, item in result.items() if type(item) is rules[key]}
    if isinstance(value, dict):
        raw = fields(value.get("execution"), EXECUTION_FIELDS)
        execution = {}
        for key, item in raw.items():
            if key == "queue_quota":
                valid = isinstance(item, str)
            elif key == "retries":
                valid = type(item) is int and 0 <= item <= 10
            else:
                valid = type(item) in (int, float) and 0 <= item < math.inf
            if valid:
                execution[key] = item
        if execution:
            result["execution"] = execution
    return result


def public_url(value):
    try:
        parts = urlsplit(value or "")
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        # Parameters, credentials, and explicit token paths are excluded from history.
        path = parts.path.partition(";")[0]
        if re.search(r"(?i)/(?:token|apikey|api-key|secret|session)(?:/|$)", unquote(path)):
            path = ""
        return urlunsplit((parts.scheme, host, path, "", ""))
    except (TypeError, ValueError, AttributeError):
        return None


def public_detail(value, *secrets):
    if not isinstance(value, str):
        return ""
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[oculto]")
    value = re.sub(r'https?://[^\s\'"<>]+',
                   lambda match: public_url(match.group()) or "[URL]", value)
    return re.sub(r'(?i)(\bBearer\s+|\b(?:api[_ -]?key|authorization|token)\s*[:=]\s*)[^\s,;"\']+',
                  r'\1[oculto]', value)


def utc_value(value):
    """Preserva a ausência e evita atribuir fuso a um horário legado ingênuo."""
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is not None:
            return date.astimezone(UTC).isoformat(timespec="microseconds")
    except (OverflowError, ValueError):
        pass
    return None


def display_time(value):
    if not value:
        return "Desconhecido"
    return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")


def filter_time(value, *, end=False):
    from datetime import timedelta
    if not value:
        return None
    time = datetime.fromisoformat(value)
    if len(value) == 10 and end:
        time += timedelta(days=1)
    return time.astimezone(UTC).isoformat(timespec="microseconds")
