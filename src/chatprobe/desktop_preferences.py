"""Preferências do aplicativo e gravação local dos resultados."""

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from src.chatprobe.chat_url import DEFAULT_CHAT_URL, validate_chat_url


SETTINGS_FILE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config") / "ChatProbe" / "settings.json"
DEFAULT_RESULTS_FOLDER = Path.home() / "ChatProbe" / "Resultados"


@dataclass(frozen=True)
class Preferences:
    results_folder: Path = DEFAULT_RESULTS_FOLDER
    dark_mode: bool = False
    mode: str = ""
    ollama_model: str = ""
    customer_name: str = ""
    night_prompts: tuple[str, ...] = ()
    night_prompt_titles: tuple[str, ...] = ()
    night_prompt_origins: tuple[str | dict | None, ...] = ()
    chat_url: str = DEFAULT_CHAT_URL
    execution: dict | None = None


def load_preferences():
    try:
        values = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Preferences()
    if not isinstance(values, dict):
        return Preferences()
    folder = values.get("results_folder")
    if not isinstance(folder, str) or not folder or "\x00" in folder or not Path(folder).is_absolute():
        folder = DEFAULT_RESULTS_FOLDER
    prompts = values.get("night_prompts", [])
    titles = values.get("night_prompt_titles", [])
    titles = titles if isinstance(titles, list) else []
    origins = values.get("night_prompt_origins")
    origins = origins if isinstance(origins, list) else []
    entries = [(index, prompt) for index, prompt in enumerate(prompts)
               if isinstance(prompt, str) and prompt.strip()] if isinstance(prompts, list) else []
    try:
        chat_url = validate_chat_url(preference_text(values, "chat_url"))
    except ValueError:
        chat_url = DEFAULT_CHAT_URL
    return Preferences(
        chat_url=chat_url,
        execution=values.get("execution") if isinstance(values.get("execution"), dict) else None,
        results_folder=Path(folder),
        dark_mode=values.get("dark_mode") is True,
        mode=preference_text(values, "mode"),
        ollama_model=preference_text(values, "ollama_model"),
        customer_name=preference_text(values, "customer_name"),
        night_prompts=tuple(prompt for _, prompt in entries),
        night_prompt_titles=tuple(titles[i] if i < len(titles) and isinstance(titles[i], str) else ""
                                  for i, _ in entries) if titles else (),
        night_prompt_origins=tuple(origins[i] if i < len(origins) and isinstance(origins[i], (str, dict)) else None
                                   for i, _ in entries) if origins else (),
    )


def preference_text(values, key):
    value = values.get(key)
    return value.strip() if isinstance(value, str) else ""


def write_text_atomically(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def save_preferences(preferences):
    values = asdict(preferences)
    values["chat_url"] = validate_chat_url(preferences.chat_url)
    values["results_folder"] = str(preferences.results_folder)
    write_text_atomically(SETTINGS_FILE, json.dumps(values, ensure_ascii=False, indent=2) + "\n")


def save_report(folder, content):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = folder / f"chatprobe-{timestamp}-{uuid4().hex[:8]}.json"
    write_text_atomically(path, content)
    return path
