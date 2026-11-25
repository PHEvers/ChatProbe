"""Estado editável de uma reexecução, desvinculado do registro original."""

from dataclasses import dataclass

from src.chatprobe.runner.consumption import ExecutionOptions


@dataclass(frozen=True)
class RerunDraft:
    source: str
    url: str
    customer_name: str
    execution: ExecutionOptions
    provider_id: str | None = None
    model: str | None = None
    temperature: float | None = None
    timeout: float | None = None
    use_provider_settings: bool = True

    @classmethod
    def from_record(cls, record, url, customer_name, execution):
        settings = record.get("settings") or {}
        try:
            saved = settings.get("execution")
            if saved:
                execution = ExecutionOptions(**saved)
        except (TypeError, ValueError):
            pass
        provider = record.get("provider") or {}
        return cls(record["id"], record.get("url") or url,
                   settings.get("customer_name") or customer_name, execution,
                   provider.get("id"), provider.get("model"), provider.get("temperature"),
                   provider.get("timeout_seconds"))
