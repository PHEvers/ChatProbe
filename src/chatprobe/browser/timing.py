"""Horários UTC para relatórios e duração monotônica das execuções."""

from datetime import UTC, datetime
from time import monotonic


def utc_now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ExecutionTiming:
    def __init__(self):
        self.started_at = utc_now()
        self.start_tick = monotonic()

    def fields(self, *, finished=True):
        return {
            "started_at": self.started_at,
            "ended_at": utc_now() if finished else None,
            "duration_seconds": round(monotonic() - self.start_tick, 3),
        }


def local_time(value):
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M:%S %Z")
