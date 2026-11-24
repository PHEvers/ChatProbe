"""Autenticação e geração de mensagens de teste pela Groq."""

import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.chatprobe.model_actions import ACTION_SCHEMA, action_messages, decode_action


BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-20b"


class GroqError(Exception):
    pass


class GroqLimitError(GroqError):
    pass


def validate_key_format(key):
    if not key or not key.isascii() or not key.isprintable():
        raise GroqError("A chave salva tem um formato inválido.")
    if key.startswith("grok_"):
        raise GroqError(
            "O prefixo grok_ é de outro serviço. Crie uma chave da Groq em console.groq.com/keys."
        )


def _request(key, endpoint, payload=None):
    validate_key_format(key)
    url = f"{BASE_URL}/{endpoint}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ChatProbe"})
    if payload is not None:
        request.data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    request.add_unredirected_header("Authorization", f"Bearer {key}")
    try:
        with urlopen(request, timeout=30 if payload is not None else 10) as response:
            if response.geturl() != url:
                raise GroqError("A Groq redirecionou a consulta para outro endereço.")
            return json.load(response)
    except HTTPError as error:
        with error:
            if error.code == 401:
                raise GroqError("A Groq recusou a chave. Confira se ela está ativa.") from None
            if error.code == 403:
                raise GroqError(_forbidden_message(error, endpoint)) from None
            if error.code == 429:
                raise GroqLimitError(_limit_message(error)) from None
            raise GroqError(f"A Groq retornou um erro HTTP {error.code}.") from None
    except (OSError, HTTPException):
        raise GroqError("Não foi possível conectar à Groq. Confira a conexão e tente novamente.") from None
    except ValueError:
        raise GroqError("A Groq retornou uma resposta inesperada.") from None


def _read_error_details(error):
    try:
        return error.read(8_192).decode("utf-8", errors="replace").lower()
    except (OSError, HTTPException):
        return ""


def _forbidden_message(error, endpoint):
    details = _read_error_details(error)
    content_type = error.headers.get("Content-Type", "").lower()
    if "error code: 1010" in details or ("cloudflare" in details and "1010" in details):
        return "A conexão foi bloqueada pelo gateway da Groq (HTTP 403, Cloudflare 1010)."
    if "text/html" in content_type:
        return "A consulta recebeu uma página de bloqueio (HTTP 403). Confira a rede ou o proxy."
    if endpoint == "models":
        return "A Groq recusou a consulta de modelos (HTTP 403). Confira o projeto da chave no GroqCloud."
    return f"A Groq recusou a geração (HTTP 403). Confira se o projeto permite o modelo {MODEL}."


def _limit_message(error):
    details = _read_error_details(error)
    if "tokens per" in details or error.headers.get("x-ratelimit-remaining-tokens") == "0":
        message = "Limite de tokens da Groq atingido."
    elif "requests per" in details:
        message = "Limite de requisições da Groq atingido."
    else:
        message = "A Groq atingiu um limite de tokens ou de requisições."
    retry_after = error.headers.get("Retry-After", "")
    if retry_after.isascii() and retry_after.isdigit():
        message += f" Aguarde {retry_after} s antes de tentar de novo."
    return message


def check_api_key(key):
    try:
        models = _request(key, "models")
    except GroqError as error:
        return str(error)
    if not isinstance(models, dict) or not isinstance(models.get("data"), list):
        return "A Groq retornou uma resposta inesperada."
    return "Chave validada pela Groq."


def next_action(key, objective, turns, *, product=None, rejected_message=None, opening_variation=None):
    result = _request(key, "chat/completions", {
        "model": MODEL,
        "messages": action_messages(objective, turns, product=product,
                                    rejected_message=rejected_message, opening_variation=opening_variation),
        "max_completion_tokens": 1024,
        "reasoning_effort": "low",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "chatprobe_action",
                "strict": True,
                "schema": ACTION_SCHEMA,
            },
        },
    })
    try:
        choice = result["choices"][0]
        if choice.get("finish_reason") == "length":
            raise GroqError("A resposta do modelo atingiu o limite de tokens para uma mensagem.")
        return decode_action(choice["message"]["content"])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        pass
    raise GroqError("A Groq não retornou uma ação válida para o teste.")
