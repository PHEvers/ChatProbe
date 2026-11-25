"""Biblioteca local e snapshots imutáveis de prompts."""

import json
from dataclasses import asdict, dataclass
from uuid import uuid4

from src.chatprobe.browser.smoke import parse_scenario
from src.chatprobe.browser.timing import utc_now


@dataclass(frozen=True)
class Prompt:
    title: str
    content: str
    mode: str = "ai"
    description: str = ""
    expected: str = ""
    tags: str = ""
    product: str = ""
    favorite: bool = False

    def validate(self):
        if not self.title.strip() or not self.content.strip() or self.mode not in {"ai", "json"}:
            raise ValueError("Informe título, conteúdo e modo válidos.")
        if self.mode == "json":
            parse_scenario(json.loads(self.content))


class Library:
    def __init__(self, store):
        self.store = store

    def collections(self):
        with self.store.transaction() as db:
            return db.execute("""SELECT c.id,c.name,c.description,COUNT(p.id) FROM library_collections c
                LEFT JOIN prompts p ON p.collection_id=c.id GROUP BY c.id ORDER BY c.name COLLATE NOCASE""").fetchall()

    def collection(self, name, description="", identifier=None):
        if not name.strip():
            raise ValueError("Informe um nome para a coleção.")
        stamp = utc_now()
        with self.store.transaction() as db:
            if identifier:
                db.execute("UPDATE library_collections SET name=?,description=?,updated_at=? WHERE id=?",
                           (name.strip(), description, stamp, identifier))
            else:
                identifier = uuid4().hex
                db.execute("INSERT INTO library_collections VALUES (?,?,?,?,?)",
                           (identifier, name.strip(), description, stamp, stamp))
        return identifier

    def save(self, prompt, collection=None, identifier=None):
        prompt.validate()
        snapshot = json.dumps(asdict(prompt), ensure_ascii=False)
        stamp = utc_now()
        with self.store.transaction() as db:
            db.execute("BEGIN IMMEDIATE")
            if identifier is None:
                identifier = db.execute("""INSERT INTO prompts(title,mode,content,created_at,updated_at,collection_id,metadata)
                    VALUES(?,?,?,?,?,?,?)""", (prompt.title, prompt.mode, prompt.content, stamp, stamp, collection, snapshot)).lastrowid
            else:
                changed = db.execute("""UPDATE prompts SET title=?,mode=?,content=?,updated_at=?,collection_id=?,metadata=?
                    WHERE id=?""", (prompt.title, prompt.mode, prompt.content, stamp, collection, snapshot, identifier))
                if not changed.rowcount:
                    raise ValueError("Prompt indisponível.")
            previous = db.execute("SELECT version,snapshot FROM prompt_revisions WHERE prompt_id=? ORDER BY version DESC LIMIT 1",
                                  (identifier,)).fetchone()
            if previous is None or previous[1] != snapshot:
                db.execute("INSERT INTO prompt_revisions VALUES (?,?,?,?)",
                           (identifier, 1 if previous is None else previous[0]+1, snapshot, stamp))
        return identifier

    def prompts(self, collection=None, query="", favorite=False, mode="", *,
                product="", tag="", created="", updated="", order="title"):
        sorts = {
            "title": "p.title COLLATE NOCASE",
            "created": "p.created_at DESC",
            "updated": "p.updated_at DESC",
            "last_run": "(SELECT MAX(started_at) FROM tests WHERE library_prompt_id=CAST(p.id AS TEXT)) DESC",
            "runs": "(SELECT COUNT(*) FROM tests WHERE library_prompt_id=CAST(p.id AS TEXT)) DESC",
        }
        if order not in sorts:
            raise ValueError("Ordenação inválida.")
        with self.store.transaction() as db:
            db.create_function("fold", 1, lambda x: (x or "").casefold())
            rows = db.execute(f"""SELECT p.id,p.collection_id,p.title,p.mode,p.content,p.metadata,
                  COALESCE((SELECT MAX(version) FROM prompt_revisions WHERE prompt_id=p.id),1)
                  FROM prompts p LEFT JOIN library_collections c ON c.id=p.collection_id
                  WHERE (? IS NULL OR p.collection_id=?) AND (?='' OR p.mode=?)
                  AND (?=0 OR json_extract(p.metadata,'$.favorite')=1)
                  AND instr(fold(p.title || ' ' || p.content || ' ' || COALESCE(p.metadata,'') || ' ' || COALESCE(c.name,'')),?)>0
                  AND (?='' OR json_extract(p.metadata,'$.product')=?)
                  AND instr(fold(json_extract(p.metadata,'$.tags')),?)>0
                  AND p.created_at>=? AND p.updated_at>=?
                  ORDER BY {sorts[order]},p.id""",
                              (collection, collection, mode, mode, favorite, query.casefold(),
                               product, product, tag.casefold(), created, updated)).fetchall()
        return [(identifier, group, Prompt(**json.loads(data)) if data else Prompt(title, content, mode), revision)
                for identifier, group, title, mode, content, data, revision in rows]

    def versions(self, identifier):
        with self.store.transaction() as db:
            return [(version, Prompt(**json.loads(snapshot)), stamp) for version, snapshot, stamp in db.execute(
                "SELECT version,snapshot,created_at FROM prompt_revisions WHERE prompt_id=? ORDER BY version DESC",
                (identifier,))]

    def delete(self, identifiers):
        with self.store.transaction() as db:
            db.executemany("DELETE FROM prompts WHERE id=?", ((identifier,) for identifier in identifiers))

    def move(self, identifiers, destination):
        with self.store.transaction() as db:
            db.executemany("UPDATE prompts SET collection_id=?,updated_at=? WHERE id=?",
                           ((destination, utc_now(), identifier) for identifier in identifiers))

    def duplicate_collection(self, identifier):
        rows = self.prompts(identifier)
        source = next((row for row in self.collections() if row[0] == identifier), None)
        if source is None:
            raise ValueError("Coleção indisponível.")
        target = self.collection(f"{source[1]} (cópia)", source[2])
        for _, _, prompt, _ in rows:
            self.save(prompt, target)
        return target

    def delete_collection(self, identifier):
        with self.store.transaction() as db:
            db.execute("DELETE FROM library_collections WHERE id=?", (identifier,))


def parse_import(content, suffix):
    if suffix == ".md" and content.startswith("<!-- chatprobe:v1 -->"):
        _, _, payload = content.partition("\n```json\n")
        if payload:
            body, _, _ = payload.partition("\n```")
            return parse_import(body, ".json")
    if suffix == ".json":
        document = json.loads(content)
        if isinstance(document, dict) and "initial_message" in document:
            return [Prompt("Roteiro JSON", content, "json")]
        if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("prompts"), list):
            raise ValueError("Formato de biblioteca JSON inválido.")
        return [Prompt(**item) for item in document["prompts"]]
    import re
    parts = re.split(r"(?m)^--- CHATPROBE ---\r?\n", content)
    return [Prompt(next((line.strip().lstrip("# ") for line in part.splitlines() if line.strip()), "Prompt")[:100],
                   part) for part in parts if part.strip()]


def export_library(rows, suffix):
    exported = {"version": 1, "prompts": [asdict(row[2]) for row in rows]}
    if suffix == ".txt":
        return "\n--- CHATPROBE ---\n".join(row[2].content for row in rows)
    encoded = json.dumps(exported, ensure_ascii=False, indent=2)
    if suffix == ".md":
        return "<!-- chatprobe:v1 -->\n# Biblioteca de prompts\n\n```json\n" + encoded + "\n```\n"
    return encoded + "\n"
