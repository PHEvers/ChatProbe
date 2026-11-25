import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.chatprobe.storage.library import Library, Prompt, parse_import
from src.chatprobe.storage.sqlite_store import RunStore


class LibraryTests(unittest.TestCase):
    def test_revisions_and_collection_deletion_preserve_prompt(self):
        with TemporaryDirectory() as directory:
            library = Library(RunStore(Path(directory) / "library.sqlite3"))
            group = library.collection("Cupom")
            original = Prompt("Título", "  acentuação\n\n")
            identifier = library.save(original, group)
            library.save(Prompt("Título", "nova versão"), group, identifier)
            self.assertEqual(library.versions(identifier)[1][1], original)
            library.delete_collection(group)
            self.assertIsNone(library.prompts()[0][1])

    def test_text_import_does_not_split_individual_lines(self):
        prompts = parse_import("Título\nlinha\n--- CHATPROBE ---\nOutro\n", ".txt")
        self.assertEqual([prompt.content for prompt in prompts], ["Título\nlinha\n", "Outro\n"])
