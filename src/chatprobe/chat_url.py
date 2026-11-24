"""Endereço padrão e validação da configuração do chat."""

from urllib.parse import urlsplit


DEFAULT_CHAT_URL = "https://suportelinx--uat.sandbox.my.site.com/CentralCliente/"
INVALID_URL = "Informe uma URL HTTP ou HTTPS válida, sem usuário ou senha."


def validate_chat_url(value):
    url = value.strip()
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127
           for character in url) or "\\" in url:
        raise ValueError(INVALID_URL)
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError(INVALID_URL) from None
    if (parts.scheme not in {"http", "https"} or not hostname
            or "@" in parts.netloc or "%" in hostname
            or parts.netloc.endswith(":") or port == 0):
        raise ValueError(INVALID_URL)
    return url
