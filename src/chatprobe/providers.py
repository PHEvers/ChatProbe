"""Perfis de IA e adaptação dos protocolos de geração e descoberta."""

import json
import math
import re
from http.client import HTTPException
from email.utils import parsedate_to_datetime
from time import time
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.chatprobe import groq_api, ollama_api
from src.chatprobe.chat_url import validate_chat_url
from src.chatprobe.model_actions import ACTION_SCHEMA, action_messages, decode_action, token_usage


PRESETS = {
    "Groq": ("openai", "https://api.groq.com/openai/v1"),
    "OpenAI": ("openai", "https://api.openai.com/v1"),
    "OpenRouter": ("openai", "https://openrouter.ai/api/v1"),
    "DeepSeek": ("openai", "https://api.deepseek.com"),
    "Mistral AI": ("openai", "https://api.mistral.ai/v1"),
    "Together AI": ("openai", "https://api.together.xyz/v1"),
    "LM Studio": ("openai", "http://127.0.0.1:1234/v1"),
    "API personalizada": ("openai", ""),
    "Ollama": ("ollama", "http://127.0.0.1:11434"),
    "Google Gemini": ("gemini", "https://generativelanguage.googleapis.com/v1beta"),
    "Anthropic Claude": ("anthropic", "https://api.anthropic.com/v1"),
}

ERRORS = {
    "connection": "Servidor indisponível ou conexão TLS recusada.",
    "endpoint": "Endpoint inválido ou operação indisponível neste endereço.",
    "auth": "Autenticação recusada. Confira a chave de API e suas permissões.",
    "model": "Modelo inexistente ou indisponível para esta chave.",
    "timeout": "Tempo limite da solicitação excedido.",
    "rate_limit": "Limite de API atingido ou servidor sobrecarregado.",
    "temporary": "Falha temporária no servidor do provedor.",
    "response": "O provedor recusou a operação ou devolveu uma resposta incompatível.",
}


class ProviderError(Exception):
    def __init__(self, code, retry_after=None, *, daily=False):
        self.code = code
        self.retry_after = retry_after
        self.daily = daily
        super().__init__(ERRORS[code])


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    kind: str
    base_url: str
    model: str = ""
    temperature: float | None = None
    timeout: float = 30
    quota: str = ""
    model_quota: str = ""
    quota_scope: str = ""

    def validate(self):
        if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.id) or not self.name.strip() or not self.name.isprintable()
                or self.kind not in {"openai", "ollama", "gemini", "anthropic"}):
            raise ValueError("Informe um nome e um protocolo válidos.")
        parts = urlsplit(validate_chat_url(self.base_url))
        if parts.query or parts.fragment:
            raise ValueError("A URL base deve conter apenas o servidor e o caminho da API.")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 600:
            raise ValueError("Informe um timeout entre 0 e 600 segundos.")
        if self.temperature is not None and (
            not math.isfinite(self.temperature) or not 0 <= self.temperature <= (1 if self.kind == "anthropic" else 2)
        ):
            raise ValueError("Informe uma temperatura compatível com o protocolo (0 a 1 para Claude, 0 a 2 para os demais).")
        from src.chatprobe.runner.consumption import Quota
        Quota.parse(self.quota)
        Quota.parse(self.model_quota)
        if len(self.quota_scope) > 100 or (self.quota_scope and not self.quota_scope.isprintable()):
            raise ValueError("Informe um identificador de cota válido.")
        return self

    def metadata(self):
        return {"id": self.id, "name": self.name, "protocol": self.kind, "model": self.model,
                "temperature": self.temperature, "timeout_seconds": self.timeout}


@dataclass(frozen=True)
class Action:
    kind: str
    text: str
    usage: dict = field(default_factory=dict)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Provider:
    profile: ProviderProfile
    key: str = field(default="", repr=False)

    def __post_init__(self):
        self.profile.validate()
        if self.key and (not self.key.isascii() or not self.key.isprintable() or any(c.isspace() for c in self.key)):
            raise ProviderError("auth")
        url = urlsplit(self.profile.base_url)
        if self.key and url.scheme != "https" and url.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Use HTTPS para enviar uma chave a um servidor remoto.")

    def request(self, path, payload=None):
        kind = self.profile.kind
        headers = {"Accept": "application/json"}
        if self.key:
            if kind == "anthropic":
                headers["x-api-key"] = self.key
            elif kind == "gemini":
                headers["x-goog-api-key"] = self.key
            else:
                headers["Authorization"] = f"Bearer {self.key}"
        if kind == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.profile.base_url.rstrip("/") + path, data=body, headers=headers)
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.profile.timeout) as response:
                data = json.load(response)
            if not isinstance(data, dict):
                raise ProviderError("response")
            return data
        except HTTPError as error:
            with error:
                status = error.code
                daily = False
                if status in (401, 403):
                    code = "auth"
                elif status in (429, 503):
                    code = "rate_limit"
                    if status == 429:
                        try:
                            detail = error.read(16384).decode("utf-8", errors="replace").lower()
                        except (OSError, HTTPException):
                            detail = ""
                        daily = any(marker in detail for marker in (
                            "per day", "per_day", "perday", "daily limit", "daily quota"))
                elif status in (500, 502):
                    code = "temporary"
                elif status in (408, 504):
                    code = "timeout"
                elif status in (400, 404):
                    try:
                        data = json.loads(error.read(16384))
                        detail = data.get("error", "")
                        if isinstance(detail, dict):
                            detail = " ".join(str(detail.get(key, "")) for key in ("code", "type", "message", "details"))
                        detail = str(detail).lower()
                    except (ValueError, OSError, AttributeError):
                        detail = ""
                    model_error = "model" in detail and any(
                        word in detail for word in ("not found", "not_found", "does not exist", "unavailable")
                    )
                    if "api_key_invalid" in detail or "api key not valid" in detail:
                        code = "auth"
                    else:
                        code = "model" if model_error else "endpoint" if status == 404 else "response"
                else:
                    code = "endpoint" if 300 <= status < 400 or status == 405 else "response"
            raise ProviderError(code, retry_after_seconds(error.headers.get("Retry-After")), daily=daily) from None
        except (TimeoutError, URLError, OSError, HTTPException) as error:
            timeout = isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError)
            raise ProviderError("timeout" if timeout else "connection") from None
        except (ValueError, UnicodeError):
            raise ProviderError("response") from None

    def list_models(self):
        kind = self.profile.kind
        path = "/api/tags" if kind == "ollama" else "/models"
        items = []
        seen = set()
        query = ""
        while True:
            result = self.request(path + query)
            page = result.get("models" if kind in {"ollama", "gemini"} else "data")
            if not isinstance(page, list):
                raise ProviderError("response")
            items.extend(page)
            if kind == "gemini":
                parameter, cursor = "pageToken", result.get("nextPageToken")
            elif kind == "anthropic" and result.get("has_more"):
                parameter, cursor = "after_id", result.get("last_id")
            else:
                break
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen or len(seen) >= 50:
                raise ProviderError("response")
            seen.add(cursor)
            query = "?" + urlencode({parameter: cursor})
        models = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("name" if kind in {"ollama", "gemini"} else "id")
            if not isinstance(name, str) or not name:
                continue
            if kind == "gemini":
                if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                    continue
                name = name.removeprefix("models/")
            if kind == "ollama" and (
                item.get("remote_host") or item.get("remote_model") or name.endswith((":cloud", "-cloud"))
            ):
                continue
            models.add(name)
        return sorted(models)

    def check_connection(self):
        models = self.list_models()
        if self.profile.model and self.profile.model.removeprefix("models/") not in models:
            raise ProviderError("model")
        return models

    def generate(self, objective, turns, **options):
        if not self.profile.model.strip():
            raise ProviderError("model")
        messages = action_messages(objective, turns, **options)
        messages[0]["content"] += (
            ' Responda somente com um objeto JSON com as chaves "action" e "text", sem Markdown.'
        )
        try:
            content, usage = self.complete(messages)
            content = content.strip()
            fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
            kind, text = decode_action(fenced.group(1) if fenced else content)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            raise ProviderError("response") from None
        return Action(kind, text, usage)

    def test_generation(self, *, ledger=None, scopes=()):
        objective = "Inicie relatando que uma impressora fictícia não imprimiu."
        if ledger is None:
            return self.generate(objective, [])
        from src.chatprobe.runner.consumption import ExecutionOptions, LocalQuotaError, generate_with_controls

        def reject_wait(seconds, label):
            raise LocalQuotaError(f"{label}. Consulte novamente em {math.ceil(seconds)} s.")

        return generate_with_controls(self.generate, objective, [], {}, ledger=ledger, scopes=scopes,
                                      test_id=None, policy=ExecutionOptions(max_wait=86400),
                                      checkpoint=lambda: None, wait_for=reject_wait,
                                      records=[], model=self.profile.model)

    def complete(self, messages, *, schema=None):
        kind, model = self.profile.kind, self.profile.model
        temperature = self.profile.temperature
        options = {} if temperature is None else {"temperature": temperature}
        if kind == "ollama":
            data = self.request("/api/chat", {"model": model, "messages": messages,
                                "format": schema or ACTION_SCHEMA, "stream": False, "options": {"num_predict": 1024, **options}})
            return data["message"]["content"], token_usage(data, "prompt_eval_count", "eval_count")
        if kind == "gemini":
            configuration = {"maxOutputTokens": 1024, "responseMimeType": "application/json", "responseSchema": {
                "type": "OBJECT", "properties": {
                    "action": {"type": "STRING", "enum": ["send", "stop", "interrupt"]},
                    "text": {"type": "STRING"},
                }, "required": ["action", "text"],
            }, **options}
            if schema is not None:
                configuration["responseSchema"] = gemini_schema(schema)
            data = self.request("/models/" + quote(model.removeprefix("models/"), safe="") + ":generateContent", {
                "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
                "contents": [{"role": "user", "parts": [{"text": messages[1]["content"]}]}],
                "generationConfig": configuration,
            })
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts if not part.get("thought")), token_usage(
                data.get("usageMetadata", {}), "promptTokenCount", "candidatesTokenCount", "totalTokenCount")
        if kind == "anthropic":
            data = self.request("/messages", {"model": model, "system": messages[0]["content"],
                                "messages": messages[1:], "max_tokens": 1024, **options})
            content = "".join(part["text"] for part in data["content"] if part.get("type") == "text")
            return content, token_usage(data.get("usage", {}), "input_tokens", "output_tokens")
        limit = "max_completion_tokens" if urlsplit(self.profile.base_url).hostname == "api.openai.com" else "max_tokens"
        data = self.request("/chat/completions", {"model": model, "messages": messages, limit: 1024, **options})
        return data["choices"][0]["message"]["content"], token_usage(
            data.get("usage", {}), "prompt_tokens", "completion_tokens", "total_tokens")


def legacy_provider(key, model):
    """Compatibilidade com chamadores que ainda passam o modelo ou a chave legada."""
    if model:
        action, credential = ollama_api.next_action, model
    else:
        action, credential = groq_api.next_action, key

    def generate(objective, turns, **options):
        try:
            decision = action(credential, objective, turns, **options)
            return Action(*decision, getattr(decision, "usage", {}))
        except (groq_api.GroqLimitError, ollama_api.OllamaLimitError) as error:
            raise ProviderError("rate_limit", getattr(error, "retry_after", None),
                                daily=getattr(error, "daily", False)) from None

    return generate


def provider_failure(error):
    if isinstance(error, (groq_api.GroqLimitError, ollama_api.OllamaLimitError)):
        return ProviderError("rate_limit")
    return None


def legacy_metadata(key, model):
    if model:
        return ProviderProfile("legacy-ollama", "Ollama", *PRESETS["Ollama"], model=model,
                               temperature=0.3, timeout=180).metadata()
    if key:
        return ProviderProfile("legacy-groq", "Groq", *PRESETS["Groq"], model=groq_api.MODEL).metadata()
    return None


def retry_after_seconds(value, now=None):
    if not value:
        return None
    try:
        return float(int(value)) if value.isdecimal() else max(
            0, parsedate_to_datetime(value).timestamp() - (time() if now is None else now))
    except (ValueError, TypeError, OverflowError):
        return None


def gemini_schema(value):
    if isinstance(value, list):
        return [gemini_schema(item) for item in value]
    if isinstance(value, dict):
        return {key: item.upper() if key == "type" else gemini_schema(item)
                for key, item in value.items() if key != "additionalProperties"}
    return value
