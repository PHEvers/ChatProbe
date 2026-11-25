"""Armazenamento local de execuções com migrações transacionais."""

import json
import sqlite3
from contextlib import closing, contextmanager
from uuid import uuid4

from src.chatprobe.browser.timing import utc_now
from src.chatprobe.desktop_preferences import SETTINGS_FILE
from src.chatprobe.storage.history_metadata import PROVIDER_FIELDS, fields, public_detail, public_settings, public_url, utc_value


DEFAULT_DATABASE = SETTINGS_FILE.with_name("chatprobe.sqlite3")

MIGRATIONS = (
    (
        """CREATE TABLE tests (
            id TEXT PRIMARY KEY NOT NULL,
            batch_id TEXT NOT NULL,
            test_number INTEGER NOT NULL,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds REAL,
            status TEXT NOT NULL,
            termination_reason TEXT,
            UNIQUE (batch_id, test_number)
        )""",
        """CREATE TABLE messages (
            test_id TEXT NOT NULL REFERENCES tests(id),
            turn_number INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'bot')),
            position INTEGER NOT NULL,
            text TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            PRIMARY KEY (test_id, turn_number, role, position)
        )""",
        """CREATE TABLE events (
            id INTEGER PRIMARY KEY,
            test_id TEXT NOT NULL REFERENCES tests(id),
            kind TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )""",
        "CREATE INDEX events_by_test ON events (test_id, id)",
    ),
    (
        """CREATE TABLE prompts (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            mode TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    ),
    (
        "ALTER TABLE tests ADD COLUMN provider_json TEXT",
        "ALTER TABLE tests ADD COLUMN token_usage_json TEXT",
    ),
    (
        """CREATE TABLE api_requests (
            id TEXT PRIMARY KEY, test_id TEXT REFERENCES tests(id), started REAL NOT NULL,
            estimated INTEGER NOT NULL, actual INTEGER, usage_json TEXT, outcome TEXT,
            retry_after REAL, interaction INTEGER, model TEXT
        )""",
        """CREATE TABLE api_scopes (
            request_id TEXT REFERENCES api_requests(id), scope TEXT NOT NULL,
            PRIMARY KEY(request_id,scope)
        )""",
        "CREATE INDEX api_scopes_by_scope ON api_scopes(scope)",
        "CREATE INDEX api_requests_by_start ON api_requests(started)",
        "CREATE TABLE api_cooldowns (scope TEXT PRIMARY KEY, until REAL NOT NULL)",
        """CREATE TABLE execution_queues (
            id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, status TEXT NOT NULL,
            scheduled_at REAL, owner TEXT, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE queue_items (
            queue_id TEXT REFERENCES execution_queues(id), position INTEGER,
            prompt TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
            PRIMARY KEY(queue_id,position)
        )""",
    ),

    (
        "ALTER TABLE tests ADD COLUMN title TEXT",
        "ALTER TABLE tests ADD COLUMN prompt_text TEXT",
        "ALTER TABLE tests ADD COLUMN product TEXT",
        "ALTER TABLE tests ADD COLUMN target_url TEXT",
        "ALTER TABLE tests ADD COLUMN execution_type TEXT",
        "ALTER TABLE tests ADD COLUMN settings_json TEXT",
        "ALTER TABLE tests ADD COLUMN application_version TEXT",
        "ALTER TABLE tests ADD COLUMN detail TEXT",
        "ALTER TABLE tests ADD COLUMN wait_json TEXT",
        "ALTER TABLE tests ADD COLUMN processing_seconds REAL",
        "ALTER TABLE tests ADD COLUMN source_test_id TEXT REFERENCES tests(id) ON DELETE SET NULL",
        "ALTER TABLE tests ADD COLUMN library_prompt_id TEXT",
        "ALTER TABLE tests ADD COLUMN library_collection_id TEXT",
        "ALTER TABLE tests ADD COLUMN library_collection_run_id TEXT",
        "ALTER TABLE tests ADD COLUMN import_hash TEXT",
        "ALTER TABLE tests ADD COLUMN imported_at TEXT",
        "ALTER TABLE tests ADD COLUMN imported_metrics_json TEXT",
        "ALTER TABLE tests ADD COLUMN interactions_recorded INTEGER",
        "ALTER TABLE tests ADD COLUMN requests_recorded INTEGER",
        "ALTER TABLE api_requests ADD COLUMN processing_seconds REAL",
        "CREATE UNIQUE INDEX tests_import_hash ON tests(import_hash)",
        "CREATE INDEX tests_by_start ON tests(started_at DESC,id DESC)",
        "CREATE INDEX tests_by_status ON tests(status,started_at DESC)",
        "CREATE INDEX tests_by_product ON tests(product,started_at DESC)",
        "CREATE INDEX tests_by_provider ON tests(json_extract(provider_json,'$.name'),started_at DESC)",
        "CREATE INDEX tests_by_model ON tests(json_extract(provider_json,'$.model'),started_at DESC)",
        "CREATE INDEX tests_by_type ON tests(execution_type,started_at DESC)",
        "CREATE INDEX tests_by_version ON tests(application_version,started_at DESC)",
        "CREATE INDEX tests_by_origin ON tests(source_test_id)",
        "CREATE INDEX api_requests_by_test ON api_requests(test_id)",
        """UPDATE tests SET (title,prompt_text,product,execution_type,application_version) =
           (SELECT json_extract(snapshot,'$.prompt_titles[' || (position-1) || ']'),
              prompt,json_extract(snapshot,'$.product'),'queue',json_extract(result,'$.application_version')
            FROM queue_items JOIN execution_queues ON execution_queues.id=queue_items.queue_id
            WHERE queue_id=tests.batch_id AND position=tests.test_number)
           WHERE EXISTS (SELECT 1 FROM queue_items WHERE queue_id=tests.batch_id AND position=tests.test_number)""",
        """UPDATE tests SET started_at=substr(started_at,1,23)||'000'||substr(started_at,24)
            WHERE length(started_at)=29 AND started_at LIKE '%+00:00'""",
        """UPDATE tests SET interactions_recorded=(SELECT COUNT(*) FROM messages
            WHERE messages.test_id=tests.id AND role='user')
            WHERE EXISTS (SELECT 1 FROM messages WHERE messages.test_id=tests.id)""",
    ),

    (
        """CREATE TABLE library_collections (
           id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
        "ALTER TABLE prompts ADD COLUMN collection_id TEXT REFERENCES library_collections(id) ON DELETE SET NULL",
        "ALTER TABLE prompts ADD COLUMN metadata TEXT",
        """CREATE TABLE prompt_revisions (
           prompt_id INTEGER REFERENCES prompts(id) ON DELETE CASCADE, version INTEGER, snapshot TEXT NOT NULL,
           created_at TEXT NOT NULL, PRIMARY KEY(prompt_id,version))""",
        "ALTER TABLE tests ADD COLUMN library_prompt_version INTEGER",
        "CREATE INDEX tests_by_library_prompt ON tests(library_prompt_id,started_at)",
        "CREATE INDEX tests_by_library_collection ON tests(library_collection_id,started_at)",
    ),

    (
        """CREATE TABLE evaluation_rubrics(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL, snapshot TEXT NOT NULL)""",
        """CREATE TABLE evaluations(id TEXT PRIMARY KEY,test_id TEXT REFERENCES tests(id) ON DELETE CASCADE,
             created_at TEXT NOT NULL,payload TEXT NOT NULL)""",
        "CREATE INDEX evaluations_by_test ON evaluations(test_id,created_at DESC)",
        """CREATE TABLE evaluation_reviews(id INTEGER PRIMARY KEY, evaluation_id TEXT REFERENCES evaluations(id)
             ON DELETE CASCADE, created_at TEXT NOT NULL,payload TEXT NOT NULL)""",
        "CREATE TABLE evaluation_baselines(scope TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    ),

)


class RunStore:
    def __init__(self, path=DEFAULT_DATABASE, *, timeout=10):
        self.path = path
        self.timeout = timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise sqlite3.DatabaseError("O banco foi criado por uma versão mais recente do ChatProbe.")
            for migration in MIGRATIONS[version:]:
                for statement in migration:
                    connection.execute(statement)
                version += 1
                connection.execute(f"PRAGMA user_version = {version}")

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=self.timeout)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def start_test(self, batch_id, number, mode, started_at, provider=None, history=None):
        test_id = uuid4().hex
        history = history or {}
        with self.transaction() as connection:
            source = history.get("source_test_id")
            if source and not connection.execute("SELECT 1 FROM tests WHERE id=?", (source,)).fetchone():
                source = None
            connection.execute(
                """INSERT INTO tests (id,batch_id,test_number,mode,started_at,status,provider_json,
                   title,prompt_text,product,target_url,execution_type,settings_json,application_version,
                   source_test_id,interactions_recorded,requests_recorded)
                   VALUES (?,?,?,?,?,'running',?,?,?,?,?,?,?,?,?,0,?)""",
                (test_id, batch_id, number, mode, utc_value(started_at) or started_at,
                 json.dumps(fields(provider, PROVIDER_FIELDS)) if provider else None,
                 history.get("title"), history.get("prompt_text"), history.get("product"),
                 public_url(history.get("url")), history.get("execution_type"),
                 json.dumps(public_settings(history.get("settings"))), history.get("application_version"), source,
                 0 if mode == "json" else None),
            )
            connection.execute("""UPDATE tests SET library_prompt_id=?,library_collection_id=?,
                library_collection_run_id=?,library_prompt_version=? WHERE id=?""",
                (history.get("library_prompt_id"),history.get("library_collection_id"),
                 history.get("library_collection_run_id"),history.get("library_prompt_version"),test_id))
            connection.execute(
                "INSERT INTO events (test_id, kind, occurred_at) VALUES (?, 'started', ?)",
                (test_id, started_at),
            )
        return test_id

    def save_turn(self, test_id, number, turn):
        replies = zip(turn["replies"], turn["reply_received_at"], strict=True)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO messages VALUES (?, ?, 'user', 0, ?, ?)
                   ON CONFLICT (test_id, turn_number, role, position) DO NOTHING""",
                (test_id, number, turn["message"], turn["sent_at"]),
            )
            connection.execute(
                "DELETE FROM messages WHERE test_id = ? AND turn_number = ? AND role = 'bot'",
                (test_id, number),
            )
            connection.execute("UPDATE tests SET interactions_recorded=MAX(COALESCE(interactions_recorded,0),?) WHERE id=?",
                               (number, test_id))
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, 'bot', ?, ?, ?)",
                ((test_id, number, position, text, timestamp)
                 for position, (text, timestamp) in enumerate(replies)),
            )

    def event(self, test_id, kind, stamp=None):
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO events (test_id, kind, occurred_at) VALUES (?, ?, ?)",
                (test_id, kind, stamp or utc_now()),
            )

    def finish_test(self, test_id, result):
        with self.transaction() as connection:
            connection.execute(
                """UPDATE tests SET ended_at = ?, duration_seconds = ?, status = ?,
                   termination_reason = ?, token_usage_json = ?, detail=?, wait_json=?,
                   processing_seconds=?, requests_recorded=COALESCE(?,requests_recorded) WHERE id = ?""",
                (utc_value(result["ended_at"]) or result["ended_at"], result["duration_seconds"], result["status"],
                 result["termination_reason"],
                 json.dumps(result["token_usage"]) if "token_usage" in result else None,
                 public_detail(result.get("detail")), json.dumps(result.get("wait_seconds")),
                 result.get("processing_seconds"), len(result["api_calls"]) if "api_calls" in result else None,
                 test_id),
            )
            connection.execute(
                "INSERT INTO events (test_id, kind, occurred_at) VALUES (?, 'finished', ?)",
                (test_id, result["ended_at"]),
            )
