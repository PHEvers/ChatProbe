"""Lista modelos instalados e gera mensagens pelo Ollama local."""

import json
from contextlib import closing
from http.client import HTTPConnection, HTTPException

from src.chatprobe.model_actions import ACTION_SCHEMA, action_messages, decode_action


class OllamaError(Exception):
    pass


def _request(endpoint, payload=None):
    timeout = 2 if payload is None else 180
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    method = "GET" if body is None else "POST"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    try:
        with closing(HTTPConnection("127.0.0.1", 11434, timeout=timeout)) as connection:
            connection.request(method, endpoint, body=body, headers=headers)
            with connection.getresponse() as response:
                if response.status == 404 and payload is not None:
                    raise OllamaError("O modelo selecionado não está disponível. Confira os modelos no Ollama.")
                if response.status != 200:
                    raise OllamaError(f"O Ollama retornou um erro HTTP {response.status}.")
                return json.load(response)
    except TimeoutError:
        raise OllamaError("O Ollama demorou a responder. Tente novamente com menos testes simultâneos.") from None
    except (OSError, HTTPException):
        raise OllamaError("Abra o Ollama neste computador e tente novamente. Endereço local 127.0.0.1:11434.") from None
    except ValueError:
        raise OllamaError("O Ollama retornou uma resposta inesperada.") from None


def list_models():
    result = _request("/api/tags")
    if not isinstance(result, dict) or not isinstance(result.get("models"), list):
        raise OllamaError("O Ollama não retornou a lista de modelos instalados.")
    return sorted({
        item["name"] for item in result["models"]
        if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]
        and not item.get("remote_host") and not item.get("remote_model")
        and not item["name"].endswith((":cloud", "-cloud"))
    })


def next_action(model, objective, turns, *, product=None, rejected_message=None, opening_variation=None):
    result = _request("/api/chat", {
        "model": model,
        "messages": action_messages(objective, turns, product=product,
                                    rejected_message=rejected_message, opening_variation=opening_variation),
        "format": ACTION_SCHEMA,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.3, "num_predict": 512},
    })
    if isinstance(result, dict) and result.get("done_reason") == "length":
        raise OllamaError("A resposta do Ollama atingiu o limite de geração de uma mensagem.")
    try:
        return decode_action(result["message"]["content"])
    except (AttributeError, KeyError, TypeError, ValueError):
        raise OllamaError("O Ollama não retornou uma ação válida para o teste.") from None
