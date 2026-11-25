"""Tests for the read-only glossary and spoken spec compiler."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.repo_glossary import build_repo_glossary, normalize_spoken_transcript
from integrations.spec_compiler import compile_spoken_spec, revise_spoken_spec
from integrations.vega_ide_spec import is_ide_bridge_enabled, validate_handoff_payload


class RepoGlossaryTests(unittest.TestCase):
    def test_extracts_paths_symbols_flags_and_environment_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "sample_module.py"
            source.write_text(
                "import os\n"
                "def py_compile_file(path):\n    return os.getenv('VEGA_SAMPLE_FLAG')\n"
                "parser.add_argument('--dry-run')\n",
                encoding="utf-8",
            )
            (root / ".env").write_text("SECRET_TOKEN=must-not-be-read\n", encoding="utf-8")
            (root / "config.json").write_text('{"private_symbol": "do not index"}', encoding="utf-8")
            backup = root / "backup_old"
            backup.mkdir()
            (backup / "historical.py").write_text("def HistoricalOnly():\n    pass\n", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.js").write_text("class IgnoredDependency {}", encoding="utf-8")
            terms = build_repo_glossary(temp_dir)
        self.assertIn("sample_module", terms)
        self.assertIn("py_compile_file", terms)
        self.assertIn("VEGA_SAMPLE_FLAG", terms)
        self.assertIn("--dry-run", terms)
        self.assertNotIn("IgnoredDependency", terms)
        self.assertNotIn("private_symbol", terms)
        self.assertNotIn("HistoricalOnly", terms)
        self.assertNotIn("SECRET_TOKEN", terms)

    def test_normalizes_fillers_corrections_and_spoken_identifiers(self):
        result = normalize_spoken_transcript(
            "Um, actually use SQLite instead of Postgres; run pi compile; add SSE updates.",
            ["SQLite", "Postgres", "py_compile", "SSE"],
        )
        self.assertEqual(result["cleaned_text"], "use SQLite; run py_compile; add SSE updates.")
        self.assertIn("SQLite", result["glossary_matches"])
        self.assertIn("py_compile", result["glossary_matches"])
        self.assertIn("SSE", result["glossary_matches"])
        self.assertIn("self-correction", result["transcript_cleanup_note"])


class SpecCompilerTests(unittest.TestCase):
    def test_compile_returns_unapproved_structured_readme_spec(self):
        payload = compile_spoken_spec(
            "Um, add SQLite caching. Out of scope: cloud database support.",
            title="SQLite caching",
        )
        self.assertFalse(payload["approved_by_user"])
        self.assertEqual(payload["status"], "awaiting_approval")
        self.assertEqual(payload["mode"], "plan_only")
        self.assertTrue(validate_handoff_payload(payload)["ok"])
        self.assertIn("## In Scope (This Pass)", payload["readme_spec_markdown"])
        self.assertIn("## Out of Scope / Non-Goals", payload["readme_spec_markdown"])
        self.assertIn("cloud database support", payload["readme_spec_markdown"])

    def test_revision_bumps_and_requires_fresh_approval(self):
        payload = compile_spoken_spec("Add SQLite caching.", title="SQLite caching")
        revised = revise_spoken_spec(payload, "Actually include an offline cache test.")
        self.assertEqual(revised["spec_version"], 2)
        self.assertEqual(revised["parent_spec_id"], payload["spec_id"])
        self.assertFalse(revised["approved_by_user"])
        self.assertIn("offline cache test", revised["readme_spec_markdown"])

    def test_compile_does_not_modify_workspace_and_flag_off_stays_off(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "main.py"
            source.write_text("def add_cache():\n    pass\n", encoding="utf-8")
            before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(is_ide_bridge_enabled())
                payload = compile_spoken_spec("Add an add cache function.", workspace_root=temp_dir)
            after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(payload["approved_by_user"])
        self.assertEqual(payload["status"], "awaiting_approval")


if __name__ == "__main__":
    unittest.main()

