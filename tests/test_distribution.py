import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from src.chatprobe.distribution import check_database, create_backup, restore_backup
from src.chatprobe.storage.sqlite_store import RunStore


class DistributionTests(unittest.TestCase):
    def test_backup_restore_and_credentials_exclusion(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "chatprobe.sqlite3"
            store = RunStore(database)
            store.start_test("b", 1, "json", "2026-01-01T00:00:00+00:00")
            with store.transaction() as db:
                db.execute("UPDATE tests SET status='completed'")
            (root / "settings.json").write_text(json.dumps({"chat_url": "https://example.invalid/x?key=hidden",
                                                             "api_key": "hidden"}), encoding="utf-8")
            backup = root / "backup.zip"
            create_backup(database, backup)
            with ZipFile(backup) as archive:
                self.assertNotIn("hidden", archive.read("settings.json").decode())
            with store.transaction() as db:
                db.execute("UPDATE tests SET status='error'")
            restore_backup(database, backup, root / "safety.zip")
            self.assertTrue(check_database(database))
            with store.transaction() as db:
                self.assertEqual(db.execute("SELECT status FROM tests").fetchone(), ("completed",))

    def test_rejects_zip_with_unexpected_paths(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "invalid.zip"
            with ZipFile(path, "w") as archive:
                archive.writestr("../escape", "blocked")
                archive.writestr("manifest.json", "{}")
                archive.writestr("chatprobe.sqlite3", "")
            with self.assertRaises(ValueError):
                restore_backup(root / "chatprobe.sqlite3", path, root / "safety.zip")
            self.assertFalse((root / "safety.zip").exists())
