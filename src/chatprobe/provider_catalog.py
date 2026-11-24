"""Catálogo local sem segredos e acesso isolado às credenciais de cada perfil."""

import json
from dataclasses import asdict, replace
from uuid import uuid4

from src.chatprobe.desktop_preferences import SETTINGS_FILE, write_text_atomically
from src.chatprobe.groq_api import MODEL as LEGACY_GROQ_MODEL
from src.chatprobe.providers import PRESETS, Provider, ProviderProfile


CATALOG_FILE = SETTINGS_FILE.with_name("providers.json")
LEGACY_GROQ = "legacy-groq"
LEGACY_OLLAMA = "legacy-ollama"


class ProviderCatalog:
    def __init__(self, credentials=None, *, path=CATALOG_FILE, legacy_ollama_model="", legacy_mode=""):
        self.path = path
        self.credentials = credentials
        self.session_keys = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.profiles = {
                LEGACY_GROQ: ProviderProfile(LEGACY_GROQ, "Groq", *PRESETS["Groq"],
                                             model=LEGACY_GROQ_MODEL if legacy_mode == "Groq" else ""),
                LEGACY_OLLAMA: ProviderProfile(LEGACY_OLLAMA, "Ollama", *PRESETS["Ollama"],
                                               model=legacy_ollama_model, temperature=0.3, timeout=180),
            }
            self.default = LEGACY_GROQ if legacy_mode == "Groq" else LEGACY_OLLAMA
            self.save(self.profiles, self.default)
        else:
            profiles = [ProviderProfile(**row).validate() for row in data["profiles"]]
            self.profiles = {profile.id: profile for profile in profiles}
            if len(self.profiles) != len(profiles):
                raise ValueError("O catálogo contém identificadores duplicados.")
            self.default = data["default"] if data["default"] in self.profiles else ""

    def save(self, profiles, default):
        payload = {"default": default, "profiles": [asdict(profile) for profile in profiles.values()]}
        write_text_atomically(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self.profiles, self.default = profiles, default

    def upsert(self, profile):
        profile.validate()
        self.save({**self.profiles, profile.id: profile}, self.default or profile.id)

    def duplicate(self, profile):
        copy = replace(profile, id=uuid4().hex, name=f"{profile.name} (cópia)")
        self.upsert(copy)
        return copy

    def remove(self, provider_id):
        self.remove_key(provider_id)
        remaining = {key: value for key, value in self.profiles.items() if key != provider_id}
        default = self.default if self.default in remaining else next(iter(remaining), "")
        self.save(remaining, default)

    def set_default(self, provider_id):
        if provider_id not in self.profiles:
            raise ValueError("Selecione um provedor existente.")
        self.save(self.profiles, provider_id)

    def key_namespace(self, provider_id):
        return f"profile-{provider_id}"

    def set_key(self, profile, key):
        Provider(profile, key)
        if not key:
            raise ValueError("Informe uma chave para salvar.")
        if self.credentials:
            self.credentials.save(self.key_namespace(profile.id), key)
        else:
            self.session_keys[profile.id] = key

    def remove_key(self, provider_id):
        if self.credentials:
            self.credentials.remove(self.key_namespace(provider_id))
        self.session_keys.pop(provider_id, None)

    def resolve(self, profile):
        if self.credentials:
            key = self.credentials.get(self.key_namespace(profile.id))
        else:
            key = self.session_keys.get(profile.id)
        return Provider(profile, key or "")
