"""Armazenamento local de execuções com migrações transacionais."""

import sqlite3
from contextlib import closing, contextmanager
from uuid import uuid4

from src.chatprobe.browser.timing import utc_now
from src.chatprobe.desktop_preferences import SETTINGS_FILE


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
)


class RunStore:
    def __init__(self, path=DEFAULT_DATABASE):
        self.path = path
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
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection

    def start_test(self, batch_id, number, mode, started_at):
        test_id = uuid4().hex
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO tests (id, batch_id, test_number, mode, started_at, status)
                   VALUES (?, ?, ?, ?, ?, 'running')""",
                (test_id, batch_id, number, mode, started_at),
            )
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
            connection.executemany(
                "INSERT INTO messages VALUES (?, ?, 'bot', ?, ?, ?)",
                ((test_id, number, position, text, timestamp)
                 for position, (text, timestamp) in enumerate(replies)),
            )

    def event(self, test_id, kind):
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO events (test_id, kind, occurred_at) VALUES (?, ?, ?)",
                (test_id, kind, utc_now()),
            )

    def finish_test(self, test_id, result):
        with self.transaction() as connection:
            connection.execute(
                """UPDATE tests SET ended_at = ?, duration_seconds = ?, status = ?,
                   termination_reason = ? WHERE id = ?""",
                (result["ended_at"], result["duration_seconds"], result["status"],
                 result["termination_reason"], test_id),
            )
            connection.execute(
                "INSERT INTO events (test_id, kind, occurred_at) VALUES (?, 'finished', ?)",
                (test_id, result["ended_at"]),
            )
