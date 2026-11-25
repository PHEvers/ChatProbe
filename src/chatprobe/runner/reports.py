"""Campos aditivos para os relatórios JSON existentes."""

from src.chatprobe.browser.timing import local_time


TERMINATION_LABELS = {
    "completed": "Conclusão",
    "loop": "Loop de conversa",
    "message_limit": "Limite de mensagens",
    "interrupted": "Interrupção",
    "technical_error": "Erro técnico",
    "local_quota": "Cota local",
    "api_limit": "Limite de API",
}


def legacy_conversation(result):
    """Mantém a lista de turnos e inclui os dados do teste no primeiro turno."""
    turns = result["turns"]
    if not turns:
        return []
    metadata = {key: value for key, value in result.items() if key != "turns"}
    return [{**turns[0], "test_metadata": metadata}, *turns[1:]]


def execution_summary(result):
    reason = TERMINATION_LABELS.get(result.get("termination_reason"), "")
    started_at = local_time(result.get("started_at"))
    ended_at = local_time(result.get("ended_at"))
    duration = result.get("duration_seconds")
    lines = [reason, f"Início {started_at}" if started_at else "",
             f"Fim {ended_at}" if ended_at else "",
             f"Duração {duration:.3f} s" if duration is not None else "",
             result.get("detail", "")]
    return "\n".join(filter(None, lines))
