"""Backup portátil sem credenciais e diagnóstico local."""

import json
import platform
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from src.chatprobe.browser.timing import utc_now
from src.chatprobe.browser.version import VERSION
from src.chatprobe.desktop_preferences import write_text_atomically
from src.chatprobe.storage.history_metadata import public_url
from src.chatprobe.storage.sqlite_store import MIGRATIONS, RunStore


DATABASE = "chatprobe.sqlite3"
CONFIGS = {"settings.json": {"results_folder", "dark_mode", "mode", "ollama_model", "customer_name",
                            "night_prompts", "night_prompt_titles", "night_prompt_origins", "chat_url", "execution"},
           "providers.json": {"default", "profiles"}}


def config_text(path):
    from src.chatprobe.providers import ProviderProfile
    from dataclasses import asdict
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Configuração incompatível.")
    data = {key: value for key, value in data.items() if key in CONFIGS[path.name]}
    if path.name == "providers.json":
        allowed = ProviderProfile.__dataclass_fields__
        data["profiles"] = [asdict(ProviderProfile(**{k: v for k, v in profile.items() if k in allowed}).validate())
                            for profile in data.get("profiles", [])]
    elif "chat_url" in data:
        data["chat_url"] = public_url(data["chat_url"])
    return json.dumps(data, ensure_ascii=False, indent=2)


def check_database(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        if not 1 <= schema <= len(MIGRATIONS):
            raise ValueError("Versão do schema incompatível.")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("O SQLite do backup não passou na verificação de integridade.")
        required = {"tests", "messages", "events", "prompts"}
        tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required <= tables:
            raise ValueError("Estrutura do banco do ChatProbe inválida.")
        return schema


def copy_database(source, target):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as current,         closing(sqlite3.connect(target, timeout=3)) as backup:
        current.backup(backup)


def create_backup(database, target):
    with TemporaryDirectory() as directory:
        snapshot = Path(directory) / DATABASE
        copy_database(database, snapshot)
        schema = check_database(snapshot)
        with ZipFile(target, "x", ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(
                {"format": 1, "version": VERSION, "schema": schema, "created_at": utc_now()}))
            archive.write(snapshot, DATABASE)
            for name in CONFIGS:
                config = database.with_name(name)
                if config.is_file():
                    archive.writestr(name, config_text(config))


def restore_backup(database, source, safety_backup):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        with ZipFile(source) as archive:
            entries = {item.filename: item for item in archive.infolist()}
            if (len(entries) != len(archive.infolist()) or not {"manifest.json", DATABASE} <= entries.keys()
                    or entries.keys() - {"manifest.json", DATABASE, *CONFIGS}
                    or sum(item.file_size for item in entries.values()) > 2 * 1024**3):
                raise ValueError("Estrutura do backup inválida.")
            manifest = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest, dict) or manifest.get("format") != 1:
                raise ValueError("Formato de backup incompatível.")
            for name in {DATABASE, *CONFIGS} & entries.keys():
                from shutil import copyfileobj
                with archive.open(name) as incoming, (root / name).open("xb") as output:
                    copyfileobj(incoming, output)
        candidate = root / DATABASE
        if manifest.get("schema") != check_database(candidate):
            raise ValueError("O manifesto não corresponde ao schema do banco.")
        configs = {name: config_text(root / name) for name in CONFIGS if (root / name).is_file()}
        RunStore(candidate)
        current = RunStore(database)
        with current.transaction() as db:
            if db.execute("SELECT 1 FROM tests WHERE status='running' LIMIT 1").fetchone() or db.execute(
                "SELECT 1 FROM execution_queues WHERE status='running' LIMIT 1"
            ).fetchone():
                raise ValueError("Encerre as execuções ativas antes de restaurar.")
        create_backup(database, safety_backup)
        old_configs = {}
        try:
            for name, content in configs.items():
                target = database.with_name(name)
                old_configs[target] = target.read_text(encoding="utf-8") if target.exists() else None
                write_text_atomically(target, content)
            copy_database(candidate, database)
        except (OSError, sqlite3.Error):
            for target, content in old_configs.items():
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    write_text_atomically(target, content)
            raise


def diagnostic(database):
    from playwright.sync_api import sync_playwright
    lines = [f"ChatProbe {VERSION}", f"Sistema · {platform.system()} {platform.release()}",
             f"Python · {sys.version_info.major}.{sys.version_info.minor}"]
    try:
        lines.append(f"SQLite · schema {check_database(database)} · íntegro")
    except (OSError, sqlite3.Error, ValueError):
        lines.append("SQLite · indisponível ou incompatível")
    try:
        with sync_playwright() as playwright:
            found = Path(playwright.chromium.executable_path).is_file()
    except Exception:
        found = False
    lines.append(f"Chromium · {'disponível' if found else 'não encontrado'}")
    return "\n".join(lines)
