"""Focused tests for the local-only task router and offline voice path."""

import os
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.request import ProxyHandler
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vega_agents import AgentTaskError, AgentTaskManager, OLLAMA_BASE
from vega_practice_lab import PracticeLab
from vega_voice import Ears


def wait_for_status(manager, task_id, expected, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        task = manager.get(task_id)
        if task and task["status"] == expected:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {expected}")


class LocalAgentRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vega-router-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.manager = AgentTaskManager(os.path.join(self.temp.name, "audit.json"))
        def local_models():
            self.manager._ollama_reachable = True
            return ["qwen2.5:3b", "qwen2.5-coder:3b", "vega:latest"]
        self.manager._local_models = local_models
        self.manager._tool_versions = lambda: {
            "qwen_code": "0.26.0", "stakpak": "0.3.88", "mistral_vibe": "3.0.2",
            "kimchi": "1.21.0", "copilot": "1.0.0", "cursor": "1.0.0",
            "cline": "3.0.73", "junie": "1.0.0",
        }
        self.manager._installed_agents = lambda: {
            "cline": True, "cursor": True, "github_copilot": True, "stakpak": True,
            "mistral_vibe": True, "qwen_code": True, "kimchi": True, "junie": True,
        }

    def connect(self, agent, model):
        self.manager._verified_connections[agent] = model
        versions = self.manager._tool_versions()
        self.manager._verified_versions[agent] = versions.get(agent, "")

    def test_auto_recommendations_choose_task_capability(self):
        models = ["qwen2.5:3b", "qwen2.5-coder:3b", "vega:latest"]
        general = self.manager._recommend("general", models)
        coding = self.manager._recommend("coding", models, available_tools={"qwen_code": True})
        devops = self.manager._recommend("devops", models, available_tools={"stakpak": True})
        self.assertEqual((general["tool"], general["model"]), ("ollama", "qwen2.5:3b"))
        self.assertEqual((coding["tool"], coding["model"]), ("qwen_code", "qwen2.5-coder:3b"))
        self.assertEqual((devops["tool"], devops["model"]), ("stakpak", "qwen2.5-coder:3b"))

    def test_auto_uses_the_exact_already_verified_route_for_general_tasks(self):
        self.connect("cline", "qwen2.5-coder:3b")
        options = self.manager.options()
        self.assertEqual(options["recommendations"]["general"]["tool"], "cline")
        self.assertEqual(options["recommendations"]["general"]["model"], "qwen2.5-coder:3b")
        self.manager._run_local_cli_agent = lambda _task: "synthetic local answer"
        task = self.manager.start("general", "auto", "auto", "synthetic question")
        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertEqual((completed["tool"], completed["model"]), ("cline", "qwen2.5-coder:3b"))

    def test_all_agents_are_registered_with_separate_readiness_fields(self):
        tools = {item["id"]: item for item in self.manager.options()["tools"]}
        expected = {"cline", "cursor", "github_copilot", "stakpak", "mistral_vibe", "qwen_code", "kimchi", "junie"}
        self.assertEqual(set(tools) & expected, expected)
        cline = tools["cline"]
        self.assertTrue(cline["installed"])
        self.assertTrue(cline["registered"])
        self.assertFalse(cline["available"])
        self.assertFalse(cline["connected"])
        self.assertIn("Test connection", cline["reason"])
        self.assertEqual(tools["cursor"]["provider_type"], "cloud")
        self.assertEqual(tools["kimchi"]["free_quota_status"], "unknown")

    def test_options_refresh_and_task_id_polling_contract(self):
        self.connect("ollama", "qwen2.5:3b")
        first = self.manager.options()
        self.assertTrue(first["local_only"])
        self.assertEqual(first["endpoint"], OLLAMA_BASE)
        for key in ("models", "agents", "tools", "recommendations", "verified_connections", "source_files"):
            self.assertIn(key, first)
        refreshed = self.manager.refresh()
        self.assertEqual(set(first), set(refreshed))
        self.assertEqual(refreshed["verified_connections"]["ollama"], "qwen2.5:3b")

        self.manager._run_ollama = lambda _task: "synthetic local status result"
        created = self.manager.start("general", "ollama", "qwen2.5:3b", "synthetic status task")
        self.assertRegex(created["id"], r"^[a-f0-9]{32}$")
        self.assertEqual(self.manager.get(created["id"])["id"], created["id"])
        completed = wait_for_status(self.manager, created["id"], "completed")
        polled = self.manager.get(created["id"])
        self.assertEqual(polled["status"], "completed")
        self.assertEqual(polled["output"], "synthetic local status result")
        self.assertEqual(completed["provider_type"], "local")
        self.assertIsNone(self.manager.get("missing-task-id"))

    def test_options_preverify_only_confirmed_default_qwen_ollama_models(self):
        self.manager._ollama_reachable = True
        self.manager._local_models = lambda: ["qwen2.5:3b", "qwen2.5-coder:3b", "vega:latest"]

        options = self.manager.options()

        self.assertEqual(options["verified_connections"]["ollama"], "qwen2.5:3b")
        self.assertEqual(options["verified_models"]["ollama"], ["qwen2.5:3b"])
        self.assertNotIn("vega:latest", options["verified_models"]["ollama"])

    def test_options_do_not_preverify_ollama_without_confirmed_local_qwen_model(self):
        self.manager._ollama_reachable = True
        self.manager._local_models = lambda: ["vega:latest"]

        options = self.manager.options()

        self.assertNotIn("ollama", options["verified_connections"])
        self.assertNotIn("ollama", options["verified_models"])

    def test_unverified_agent_and_custom_cloud_route_fail_closed(self):
        with self.assertRaisesRegex(AgentTaskError, "Test the .* connection|not verified|not.*local|non-local|version"):
            self.manager.start("coding", "cline", "qwen2.5-coder:3b", "synthetic task")
        with self.assertRaises(AgentTaskError):
            self.manager.start("coding", "qwen_code", "qwen2.5-coder:3b", "synthetic task")
        with self.assertRaisesRegex(AgentTaskError, "Free cloud usage"):
            self.manager.start("general", "cursor", "qwen2.5:3b", "synthetic task", provider_mode="free_cloud")
        self.assertEqual(self.manager.recent(), [])

    def test_harmless_local_test_connects_exact_agent_and_model(self):
        self.manager._run_local_cli_agent = lambda task: "VEGA_LOCAL_OK"
        result = self.manager.test_local_connection("cline", "qwen2.5-coder:3b")
        self.assertTrue(result["connected"])
        self.assertEqual(self.manager._current_connections(
            ["qwen2.5:3b", "qwen2.5-coder:3b", "vega:latest"], self.manager._tool_versions())["cline"],
            "qwen2.5-coder:3b")
        self.manager._run_local_cli_agent = lambda task: "local answer"
        created = self.manager.start("coding", "cline", "qwen2.5-coder:3b", "synthetic question")
        completed = wait_for_status(self.manager, created["id"], "completed")
        self.assertEqual(completed["tool"], "cline")

    def test_ollama_verified_route_enables_available_local_models_without_readiness_mismatch(self):
        self.manager._run_ollama = lambda task: "VEGA_LOCAL_OK" if "VEGA_LOCAL_OK" in task["prompt"] else "answer"
        result = self.manager.test_local_connection("ollama", "qwen2.5:3b")
        self.assertTrue(result["connected"])
        options = self.manager.options()
        self.assertIn("qwen2.5:3b", options["verified_models"]["ollama"])
        # Testing one model must NOT verify other untested local models
        self.assertNotIn("qwen2.5-coder:3b", options.get("verified_models", {}).get("ollama", []))
        with self.assertRaises(AgentTaskError):
            self.manager.start("coding", "ollama", "qwen2.5-coder:3b", "task with coder")
        # Testing qwen2.5-coder:3b explicitly verifies it
        result_coder = self.manager.test_local_connection("ollama", "qwen2.5-coder:3b")
        self.assertTrue(result_coder["connected"])
        task_coder = self.manager.start("coding", "ollama", "qwen2.5-coder:3b", "task with coder")
        completed_coder = wait_for_status(self.manager, task_coder["id"], "completed")
        self.assertEqual(completed_coder["model"], "qwen2.5-coder:3b")
        task_general = self.manager.start("general", "ollama", "qwen2.5:3b", "task with general")
        completed_general = wait_for_status(self.manager, task_general["id"], "completed")
        self.assertEqual(completed_general["model"], "qwen2.5:3b")

    def test_quota_unknown_cloud_blocking_and_explicit_verification(self):
        class FakeVega:
            cfg = {"groq_api_key": "synthetic-key", "gemini_api_key": "synthetic-key"}
            def extra_chat(self, *args, **kwargs):
                return ["OK", "model"]
            def gemini_chat(self, *args, **kwargs):
                return ["OK", "model"]
        self.manager.vega = FakeVega()

        # When free allowance is unverified, cloud test connection is blocked
        with self.assertRaisesRegex(AgentTaskError, "free allowance is unverified"):
            self.manager.test_local_connection("groq", "llama-3.3-70b-versatile")

        # When free allowance is unverified, cloud task start is blocked
        with self.assertRaisesRegex(AgentTaskError, r"(?i)free.*unverified"):
            self.manager.start("coding", "groq", "llama-3.3-70b-versatile", "synthetic prompt", provider_mode="free_cloud")

        # Verify free allowance explicitly
        self.manager.verify_free_allowance("groq", True)
        res = self.manager.test_local_connection("groq", "llama-3.3-70b-versatile")
        self.assertTrue(res["connected"])
        self.assertEqual(res["provider"], "Groq Cloud")

        # Now cloud task starts successfully
        self.manager.vega.extra_chat = lambda *args, **kwargs: ["synthetic groq answer", "llama-3.3-70b-versatile"]
        task = self.manager.start("coding", "groq", "llama-3.3-70b-versatile", "synthetic prompt", provider_mode="free_cloud")
        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["output"], "synthetic groq answer")

    def test_cloud_execution_does_not_fall_back_to_other_providers(self):
        class FakeVega:
            cfg = {"groq_api_key": "synthetic-key", "gemini_api_key": "synthetic-key"}
            def extra_chat(self, *args, **kwargs):
                raise RuntimeError("Groq rate limit exceeded")
            def gemini_chat(self, *args, **kwargs):
                raise AssertionError("Must not fall back to Gemini")
        self.manager.vega = FakeVega()
        self.manager.verify_free_allowance("groq", True)
        self.manager._verified_connections["groq"] = "llama-3.3-70b-versatile"
        self.manager._verified_models.setdefault("groq", set()).add("llama-3.3-70b-versatile")
        self.manager._run_ollama = lambda task: (_ for _ in ()).throw(AssertionError("Must not fall back to Ollama"))

        task = self.manager.start("coding", "groq", "llama-3.3-70b-versatile", "synthetic prompt", provider_mode="free_cloud")
        completed = wait_for_status(self.manager, task["id"], "failed")
        self.assertIn("No other provider was contacted", completed["error"])

    def test_truthful_audit_and_task_fields_never_report_local_for_cloud(self):
        class FakeVega:
            cfg = {"groq_api_key": "synthetic-key"}
            def extra_chat(self, *args, **kwargs):
                return ["synthetic groq response", "model"]
        self.manager.vega = FakeVega()
        self.manager.verify_free_allowance("groq", True)
        self.manager._verified_connections["groq"] = "llama-3.3-70b-versatile"
        self.manager._verified_models.setdefault("groq", set()).add("llama-3.3-70b-versatile")

        task = self.manager.start("coding", "groq", "llama-3.3-70b-versatile", "synthetic prompt", provider_mode="free_cloud")
        self.assertEqual(task["provider_type"], "cloud")
        self.assertIn("Groq", task["provider"])
        self.assertNotIn("Ollama", task["provider"])

        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertEqual(completed["provider_type"], "cloud")
        self.assertIn("Groq", completed["provider"])

        audit_records = self.manager.audit()
        last_record = audit_records[-1]
        self.assertEqual(last_record["provider_type"], "cloud")
        self.assertIn("Groq", last_record["provider"])
        self.assertNotIn("ollama", last_record["provider"].lower())

    def test_auto_mode_strictly_local_loopback_without_cloud(self):
        class FakeVega:
            cfg = {"groq_api_key": "synthetic-key", "gemini_api_key": "synthetic-key"}
        self.manager.vega = FakeVega()
        models = ["qwen2.5:3b", "qwen2.5-coder:3b", "llama-3.3-70b-versatile"]
        rec = self.manager._recommend("coding", models, vega=self.manager.vega)
        self.assertIn(rec["tool"], ("qwen_code", "cline", "ollama"))
        self.assertNotIn(rec["tool"], ("groq", "gemini"))

    def test_task_text_alone_starts_task_without_files_or_attachments(self):
        self.connect("ollama", "qwen2.5:3b")
        self.manager._run_ollama = lambda task: "answer to text alone"
        task = self.manager.start("general", "ollama", "qwen2.5:3b", "Explain cloud computing")
        self.assertRegex(task["id"], r"^[a-f0-9]{32}$")
        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["output"], "answer to text alone")
        self.assertEqual(completed["source_files"], [])
        self.assertEqual(completed["attachment_count"], 0)

    def test_cli_safe_mode_banner_does_not_pollute_agent_result(self):
        output = ("⚠ SAFE MODE — customizations disabled.\n"
                  "📖 Reading prompt from file: C:\\Temp\\vega-agent-task-123\\task.txt\n"
                  "┌─ Final Agent Response ───\n│ VEGA_LOCAL_OK\n└─\n")
        self.assertEqual(AgentTaskManager._clean_cli_output(output), "VEGA_LOCAL_OK")

    def test_custom_vega_model_is_manual_only_and_local(self):
        options = self.manager.options()
        vega = next(item for item in options["models"] if item["id"] == "vega:latest")
        self.assertFalse(vega["recommendable"])
        self.assertEqual(vega["purpose"], "custom purpose unverified")
        self.assertNotEqual(options["recommendations"]["general"]["model"], "vega:latest")
        self.connect("ollama", "vega:latest")
        self.manager._run_ollama = lambda task: "synthetic local response"
        task = self.manager.start("general", "ollama", "vega:latest", "synthetic model task")
        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertEqual(completed["model"], "vega:latest")

    def test_local_agent_environment_drops_cloud_credentials_and_blocks_nonlocal_proxy(self):
        root = Path(self.temp.name) / "isolated"
        root.mkdir()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret", "ANTHROPIC_API_KEY": "synthetic-secret"}):
            env = self.manager._local_cli_env(str(root))
        self.assertEqual(env["OPENAI_API_KEY"], "")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:9")
        self.assertEqual(env["NO_PROXY"], "127.0.0.1,localhost")
        self.assertEqual(env["COPILOT_PROVIDER_BASE_URL"], OLLAMA_BASE)
        self.assertEqual(env["COPILOT_OFFLINE"], "true")
        self.assertEqual(env["USERPROFILE"], str(root / "profile"))

    def test_unavailable_local_model_blocks_task_before_prompt_dispatch(self):
        self.manager._local_models = lambda: []
        self.manager._run_ollama = lambda task: self.fail("task text must not be dispatched")
        with self.assertRaises(AgentTaskError):
            self.manager.start("general", "ollama", "qwen2.5:3b", "synthetic task")
        self.assertEqual(self.manager.recent(), [])

    def test_unverified_agent_is_rejected_before_selected_source_is_read(self):
        source = Path(self.temp.name) / "vega_example.py"
        source.write_text("safe = True\n", encoding="utf-8")
        self.manager._read_selected_sources = lambda _files: self.fail("source must not be read for an unverified agent")
        request = json.dumps({"vega_agent_request": 1, "text": "synthetic task", "files": ["vega_example.py"]})
        with self.assertRaises(AgentTaskError):
            self.manager.start("coding", "cline", "qwen2.5-coder:3b", request)

    def test_selected_source_context_is_scoped_redacted_and_not_audited(self):
        source = Path(self.temp.name) / "vega_example.py"
        source.write_text('API_KEY = "synthetic-secret-value"\nanswer = 42\n', encoding="utf-8")
        (Path(self.temp.name) / "config.json").write_text('{"private": true}\n', encoding="utf-8")
        self.connect("ollama", "qwen2.5-coder:3b")
        captured = {}
        self.manager._run_ollama = lambda task: captured.setdefault("prompt", task["prompt"]) and "synthetic local result"
        request = json.dumps({"vega_agent_request": 1, "text": "review this source", "files": ["vega_example.py"]})
        task = self.manager.start("coding", "ollama", "qwen2.5-coder:3b", request)
        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertIn("answer = 42", captured["prompt"])
        self.assertIn("REDACTED CREDENTIAL-RELATED SOURCE LINE", captured["prompt"])
        self.assertNotIn("synthetic-secret-value", captured["prompt"])
        self.assertEqual(completed["source_files"], ["vega_example.py"])
        self.assertNotIn("synthetic-secret-value", str(self.manager.audit()))
        self.assertEqual(self.manager.source_inventory(), [{"path": "vega_example.py", "bytes": source.stat().st_size}])

    def test_multiple_external_text_attachments_are_optional_and_redacted(self):
        self.connect("ollama", "qwen2.5:3b")
        captured = {}
        self.manager._run_ollama = lambda task: captured.setdefault("prompt", task["prompt"]) and "synthetic local result"
        request = json.dumps({
            "vega_agent_request": 1,
            "text": "Summarize these notes",
            "files": [],
            "attachments": [
                {"name": "notes.md", "text": "Cloud notes\nAPI_KEY = 'synthetic-secret-value'"},
                {"name": "steps.txt", "text": "Step one\nStep two"},
            ],
        })
        task = self.manager.start("general", "ollama", "qwen2.5:3b", request)
        completed = wait_for_status(self.manager, task["id"], "completed")
        self.assertIn("Cloud notes", captured["prompt"])
        self.assertIn("Step two", captured["prompt"])
        self.assertIn("REDACTED CREDENTIAL-RELATED ATTACHMENT LINE", captured["prompt"])
        self.assertNotIn("synthetic-secret-value", captured["prompt"])
        self.assertEqual(completed["attachment_count"], 2)
        self.assertEqual(self.manager.audit()[-1]["selected_attachment_count"], 2)
        self.assertNotIn("notes.md", str(self.manager.audit()))

    def test_external_attachments_reject_unsupported_secret_and_oversized_files(self):
        payload = {"vega_agent_request": 1, "text": "inspect", "files": [], "attachments": []}
        for attachment, message in (
            ({"name": "report.pdf", "text": "not parsed"}, "supported text file"),
            ({"name": "client-secret.txt", "text": "synthetic key"}, "Credential and secret"),
            ({"name": "large.txt", "text": "x" * 256001}, "larger than"),
        ):
            payload["attachments"] = [attachment]
            with self.subTest(name=attachment["name"]), self.assertRaisesRegex(AgentTaskError, message):
                self.manager.start("general", "ollama", "qwen2.5:3b", json.dumps(payload))
        self.assertEqual(self.manager.recent(), [])

    def test_loopback_endpoint_bypasses_proxies(self):
        self.assertEqual(OLLAMA_BASE, "http://127.0.0.1:11434")
        self.assertFalse(any(isinstance(handler, ProxyHandler) for handler in self.manager._opener.handlers))

    def test_cancellation_stops_a_running_task(self):
        entered, release = threading.Event(), threading.Event()

        def blocked_run(task):
            entered.set()
            release.wait(5)
            return "synthetic result"

        self.manager._run_ollama = blocked_run
        self.connect("ollama", "qwen2.5:3b")
        created = self.manager.start("general", "ollama", "qwen2.5:3b", "synthetic task")
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.manager.get(created["id"])["status"], "running")
        self.assertTrue(self.manager.cancel(created["id"]))
        release.set()
        task = wait_for_status(self.manager, created["id"], "cancelled")
        self.assertEqual(task["output"], "")
        self.assertEqual([row["status"] for row in self.manager.audit()].count("running"), 1)

    def test_local_model_timeout_is_reported_without_provider_fallback(self):
        class StalledResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def readline(self):
                time.sleep(0.01)
                raise TimeoutError()

            def close(self):
                pass

        with patch("vega_agents.MAX_TASK_SECONDS", 0.05), patch.object(
                self.manager._opener, "open", return_value=StalledResponse()):
            self.connect("ollama", "qwen2.5:3b")
            created = self.manager.start("general", "ollama", "qwen2.5:3b", "synthetic timeout task")
            task = wait_for_status(self.manager, created["id"], "failed")
        self.assertEqual(task["error"], "Local agent task timed out. No other provider was contacted.")

    def test_edit_preview_is_not_applied_until_approval(self):
        source = Path(self.temp.name) / "vega_example.py"
        source.write_text('def greet():\n    return "hello"\n', encoding="utf-8")
        proposed = '--- a/vega_example.py\n+++ b/vega_example.py\n@@ -1,2 +1,2 @@\n-def greet():\n+def say_hello():\n     return "hello"\n'
        self.manager._run_ollama = lambda task: proposed
        self.connect("ollama", "qwen2.5-coder:3b")
        request = json.dumps({"vega_agent_request": 1, "text": "change the function name", "files": ["vega_example.py"]})
        task = self.manager.start("coding", "ollama", "qwen2.5-coder:3b", request,
                                  mode="edit")
        pending = wait_for_status(self.manager, task["id"], "awaiting_approval")
        self.assertIn("-def greet()", pending["preview"])
        self.assertEqual(source.read_text(encoding="utf-8"), 'def greet():\n    return "hello"\n')
        applied = self.manager.approve(task["id"])
        self.assertEqual(applied["status"], "completed")
        self.assertEqual(source.read_text(encoding="utf-8"), 'def say_hello():\n    return "hello"\n')

    def test_discarding_edit_preview_cancels_without_changing_source(self):
        source = Path(self.temp.name) / "vega_discard.py"
        original = 'value = "before"\n'
        source.write_text(original, encoding="utf-8")
        proposed = '--- a/vega_discard.py\n+++ b/vega_discard.py\n@@ -1 +1 @@\n-value = "before"\n+value = "proposal"\n'
        self.manager._run_ollama = lambda _task: proposed
        self.connect("ollama", "qwen2.5-coder:3b")
        request = json.dumps({"vega_agent_request": 1, "text": "propose a change", "files": [source.name]})
        created = self.manager.start("coding", "ollama", "qwen2.5-coder:3b", request, mode="edit")
        pending = wait_for_status(self.manager, created["id"], "awaiting_approval")
        self.assertIn("proposal", pending["preview"])
        self.assertTrue(self.manager.cancel(created["id"]))
        discarded = self.manager.get(created["id"])
        self.assertEqual(discarded["status"], "cancelled")
        self.assertEqual(discarded["approval"], "cancelled")
        self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_edit_preview_cannot_target_data_or_outside_paths(self):
        manager = AgentTaskManager(os.path.join(self.temp.name, "audit.json"))
        with self.assertRaises(AgentTaskError):
            manager._safe_source_target("config.json")
        with self.assertRaises(AgentTaskError):
            manager._safe_source_target("..\\outside.py")

    def test_stale_edit_preview_is_rejected(self):
        source = Path(self.temp.name) / "vega_stale.py"
        source.write_text('value = "before"\n', encoding="utf-8")
        self.manager._run_ollama = lambda task: (
            '--- a/vega_stale.py\n+++ b/vega_stale.py\n@@ -1 +1 @@\n-value = "before"\n+value = "agent"\n')
        self.connect("ollama", "qwen2.5-coder:3b")
        request = json.dumps({"vega_agent_request": 1, "text": "change value", "files": ["vega_stale.py"]})
        task = self.manager.start("coding", "ollama", "qwen2.5-coder:3b", request, mode="edit")
        wait_for_status(self.manager, task["id"], "awaiting_approval")
        source.write_text('value = "user edit"\n', encoding="utf-8")
        with self.assertRaises(AgentTaskError):
            self.manager.approve(task["id"])
        self.assertEqual(source.read_text(encoding="utf-8"), 'value = "user edit"\n')


class OfflineVoiceTests(unittest.TestCase):
    def test_language_mode_selects_cached_cpu_models(self):
        ears = Ears("small.en", "base.en")
        self.assertEqual(ears.set_language_mode("english"), "small.en")
        self.assertEqual(ears.set_language_mode("auto"), "small")
        self.assertEqual(ears.set_language_mode("telugu"), "small")
        self.assertEqual(ears.set_multilingual_model("small"), "small")
        self.assertEqual(ears.set_language_mode("english"), "small.en")
        with self.assertRaises(ValueError):
            ears.set_multilingual_model("large")
        with self.assertRaises(ValueError):
            ears.set_language_mode("cloud")

    def test_auto_mode_detects_telugu_without_network_or_recordings(self):
        ears = Ears("small.en", "base.en")
        ears.set_language_mode("auto")

        class Info:
            language = "te"
            language_probability = 0.95

        segment = type("Segment", (), {"text": "synthetic transcript", "no_speech_prob": 0, "avg_logprob": 0})()
        with patch.object(ears, "_run_model", return_value=([segment], Info())) as run_model:
            result = ears._words(np.zeros(16000, dtype=np.float32))
        self.assertEqual(result, "synthetic transcript")
        self.assertEqual(ears.last_lang, "te")
        self.assertEqual(run_model.call_args.args[0], "small")
        self.assertEqual(run_model.call_args.kwargs["task"], "transcribe")

    def test_online_speech_is_disabled_and_transcription_uses_local_path(self):
        ears = Ears("small.en", "base.en")
        ears.cloud = True  # legacy config must not restore the network path
        synthetic_audio = np.zeros(16000, dtype=np.float32)
        self.assertFalse(ears.cloud_ready())
        self.assertIsNone(ears.google(synthetic_audio))
        with patch.object(ears, "_words", return_value="local synthetic text") as transcribe:
            self.assertEqual(ears.transcribe(synthetic_audio), "local synthetic text")
        transcribe.assert_called_once()

    def test_whisper_load_is_cpu_int8_and_cache_only(self):
        ears = Ears("small.en", "base.en")
        with patch("faster_whisper.WhisperModel") as model:
            model.return_value = object()
            ears.model("small.en")
        self.assertEqual(model.call_args.kwargs["device"], "cpu")
        self.assertEqual(model.call_args.kwargs["compute_type"], "int8")
        self.assertTrue(model.call_args.kwargs["local_files_only"])


class UiWorkflowTests(unittest.TestCase):
    def test_voice_transcript_is_reviewable_and_agent_actions_are_approval_gated(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        agent_js = (ROOT / "web" / "agent_lab.js").read_text(encoding="utf-8")
        settings_js = (ROOT / "web" / "settings.js").read_text(encoding="utf-8")
        lablog_js = (ROOT / "web" / "lablog.js").read_text(encoding="utf-8")
        self.assertIn('id="speech-language-select"', html)
        self.assertIn('id="speech-model-select"', html)
        self.assertIn('id="transcription-status"', html)
        self.assertIn('id="agent-workspace"', html)
        self.assertIn('id="agent-provider-mode"', html)
        self.assertIn('value="free_cloud"', html)
        self.assertIn('id="agent-task-history"', html)
        self.assertIn('id="agent-provider-list"', html)
        self.assertIn('id="agent-settings-providers"', html)
        self.assertIn('id="agent-source-list"', html)
        self.assertIn('id="agent-attachment-input" type="file" multiple', html)
        self.assertIn('id="agent-browse-files"', html)
        self.assertIn('id="agent-attachment-list"', html)
        self.assertIn('id="agent-selected-status"', html)
        self.assertIn('id="agent-edit-preview"', html)
        self.assertNotIn('id="agent-lab-modal"', html)
        self.assertNotIn('id="emoji-btn"', html)
        self.assertNotIn('id="emoji-popover"', html)
        self.assertIn('id="welcome-heading">Welcome to Vega</h1>', html)
        self.assertNotIn(", Hemanth", app_js)
        self.assertIn('chatSearch.value = ""', app_js)
        for ui_source in (html, app_js, settings_js, lablog_js, agent_js):
            self.assertNotRegex(ui_source, r"[\U0001F300-\U0001FAFF]")
        mic_flow = app_js.split("function startRecording()", 1)[1].split("function stopRecording()", 1)[0]
        self.assertIn("promptInput.value = res.text", mic_flow)
        self.assertNotIn("handleSend(true)", mic_flow)
        self.assertIn('"/api/agent/test"', agent_js)
        self.assertIn('"/api/agent/tasks/"', agent_js)
        self.assertIn('provider_mode: chosen.providerMode', agent_js)
        self.assertIn('const settingsTarget = $("agent-settings-providers")', agent_js)
        self.assertIn("Array.isArray(response.agents)", agent_js)
        self.assertNotIn("options.agents.length", agent_js)
        self.assertIn("await file.text()", agent_js)
        self.assertIn("agent-attachment-remove", agent_js)
        self.assertIn('response && response.error', agent_js)
        self.assertIn('error.status === 404', agent_js)
        self.assertIn('task tracking and cancellation remain available', agent_js)
        self.assertIn('typeof data.task.id !== "string"', agent_js)
        self.assertIn('"Not ready: " + reason', agent_js)
        self.assertIn('"Test " + s.tool.label + " with " + s.modelId', agent_js)
        self.assertIn("quota is unknown", agent_js.lower())
        self.assertNotIn("isAgentMode()", app_js)
        self.assertIn("Telugu / Tenglish", app_js)
        self.assertIn("Transcript edited locally", app_js)
        self.assertIn("Review or correct it, then press Send", app_js)


    def test_chat_agent_mode_switch_and_clean_canvas_layout(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        agent_js = (ROOT / "web" / "agent_lab.js").read_text(encoding="utf-8")

        # Chat | Agent switch in header
        self.assertIn('id="mode-chat-tab"', html)
        self.assertIn('id="mode-agent-tab"', html)
        self.assertIn('id="agent-toggle-panel-btn"', html)

        # Clean canvas and composer elements
        self.assertIn('id="agent-messages-container"', html)
        self.assertIn('id="agent-tasks-list"', html)
        self.assertIn('id="agent-composer"', html)
        self.assertIn('id="agent-side-panel"', html)
        self.assertIn('id="agent-route-pill"', html)
        self.assertIn('id="agent-browse-files"', html)
        self.assertIn('id="agent-source-picker"', html)

        # Mode switch logic in app.js
        self.assertIn("function setAppMode(mode)", app_js)
        self.assertIn('modeChatTab.addEventListener("click", () => setAppMode("chat"))', app_js)
        self.assertIn('modeAgentTab.addEventListener("click", () => setAppMode("agent"))', app_js)
        # Switching modes must never silently send prompts
        self.assertNotIn("handleSend();", app_js.split("function setAppMode(mode)")[1].split("window.setAppMode = setAppMode;")[0])
        self.assertNotIn("runTask();", app_js.split("function setAppMode(mode)")[1].split("window.setAppMode = setAppMode;")[0])

        # Agent inline rendering and native multi-file browse
        self.assertIn("getOrCreateTaskTurn", agent_js)
        self.assertIn("addAttachments", agent_js)
        self.assertIn('id="agent-attachment-input" type="file" multiple', html)




class VoiceSecurityFailClosedTests(unittest.TestCase):
    def setUp(self):
        import vega_webview
        self.webview = vega_webview
        self.saved_s = dict(self.webview.S)
        self.webview.S["locked"] = False
        self.webview.S["busy"] = False
        self.webview.S["last_verified"] = 0.0
        self.synthetic_audio = np.zeros(16000, dtype=np.float32)

    def tearDown(self):
        self.webview.S.update(self.saved_s)

    def test_voice_only_fail_closed_missing_audio(self):
        with patch.object(self.webview.sec, "voice_only", return_value=True), \
             patch.object(self.webview.sec, "voice_packages_missing", return_value=[]), \
             patch.object(self.webview.voice, "has_voiceprint", return_value=True), \
             patch.object(self.webview, "run_command") as mock_run:
            self.webview.on_voice_command("open browser", audio=None)
            mock_run.assert_not_called()

    def test_voice_only_fail_closed_missing_voiceprint(self):
        with patch.object(self.webview.sec, "voice_only", return_value=True), \
             patch.object(self.webview.sec, "voice_packages_missing", return_value=[]), \
             patch.object(self.webview.voice, "has_voiceprint", return_value=False), \
             patch.object(self.webview, "run_command") as mock_run:
            self.webview.on_voice_command("open browser", audio=self.synthetic_audio)
            mock_run.assert_not_called()

    def test_voice_only_fail_closed_missing_dependencies(self):
        with patch.object(self.webview.sec, "voice_only", return_value=True), \
             patch.object(self.webview.sec, "voice_packages_missing", return_value=["torch"]), \
             patch.object(self.webview.voice, "has_voiceprint", return_value=True), \
             patch.object(self.webview, "run_command") as mock_run:
            self.webview.on_voice_command("open browser", audio=self.synthetic_audio)
            mock_run.assert_not_called()

    def test_voice_only_fail_closed_scoring_error(self):
        with patch.object(self.webview.sec, "voice_only", return_value=True), \
             patch.object(self.webview.sec, "voice_packages_missing", return_value=[]), \
             patch.object(self.webview.voice, "has_voiceprint", return_value=True), \
             patch.object(self.webview.voice, "score", side_effect=RuntimeError("scoring failure")), \
             patch.object(self.webview, "run_command") as mock_run:
            self.webview.on_voice_command("open browser", audio=self.synthetic_audio)
            time.sleep(0.05)
            mock_run.assert_not_called()

    def test_voice_only_fail_closed_low_score(self):
        with patch.object(self.webview.sec, "voice_only", return_value=True), \
             patch.object(self.webview.sec, "voice_packages_missing", return_value=[]), \
             patch.object(self.webview.voice, "has_voiceprint", return_value=True), \
             patch.object(self.webview.voice, "score", return_value=0.08), \
             patch.object(self.webview, "run_command") as mock_run:
            self.webview.on_voice_command("open browser", audio=self.synthetic_audio)
            time.sleep(0.05)
            mock_run.assert_not_called()

    def test_voice_only_succeeds_when_verified(self):
        with patch.object(self.webview.sec, "voice_only", return_value=True), \
             patch.object(self.webview.sec, "voice_packages_missing", return_value=[]), \
             patch.object(self.webview.voice, "has_voiceprint", return_value=True), \
             patch.object(self.webview.voice, "score", return_value=0.45), \
             patch.object(self.webview, "run_command") as mock_run:
            self.webview.on_voice_command("open browser", audio=self.synthetic_audio)
            time.sleep(0.05)
            mock_run.assert_called_once_with("open browser", spoken=True)


    def test_no_silent_provider_switching_in_agent_lab(self):
        agent_js = (ROOT / "web" / "agent_lab.js").read_text(encoding="utf-8")
        run_task_body = agent_js.split("async function runTask()")[1].split("const s = pick();")[0]
        self.assertNotIn('toolSelect.value = "auto"', run_task_body)
        self.assertNotIn('modelSelect.value = "auto"', run_task_body)
        self.assertIn("Not sent: ", run_task_body)


class AgentSafetyAndPracticeLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vega-safety-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.manager = AgentTaskManager(os.path.join(self.temp.name, "audit.json"), project_root=self.temp.name)
        def local_models():
            self.manager._ollama_reachable = True
            return ["qwen2.5:3b", "qwen2.5-coder:3b"]
        self.manager._local_models = local_models
        self.practice = PracticeLab()

    def test_auto_falls_back_to_local_ollama_when_cli_agents_are_missing(self):
        models = ["qwen2.5:3b", "qwen2.5-coder:3b"]
        # When qwen_code is not available, coding Auto must select ollama with qwen2.5-coder:3b
        coding = self.manager._recommend("coding", models, available_tools={"qwen_code": False, "ollama": True})
        self.assertEqual((coding["tool"], coding["model"]), ("ollama", "qwen2.5-coder:3b"))

        # When stakpak is not available, devops Auto must select ollama with qwen2.5-coder:3b
        devops = self.manager._recommend("devops", models, available_tools={"stakpak": False, "ollama": True})
        self.assertEqual((devops["tool"], devops["model"]), ("ollama", "qwen2.5-coder:3b"))

    def test_auto_handles_no_local_models_gracefully(self):
        rec = self.manager._recommend("coding", [], available_tools={})
        self.assertEqual(rec["model"], "")
        self.assertIn("No local model is installed", rec["reason"])

    def test_verification_resets_after_restart_and_requires_retest(self):
        # A fresh manager simulates Vega restarting
        fresh_manager = AgentTaskManager(os.path.join(self.temp.name, "audit2.json"), project_root=self.temp.name)
        fresh_manager._local_models = lambda: ["qwen2.5:3b", "qwen2.5-coder:3b"]
        fresh_manager._ollama_reachable = True
        
        # Connections dictionary starts empty in memory
        self.assertEqual(fresh_manager._verified_connections, {})
        
        # Task start fails before testing
        with self.assertRaises(AgentTaskError) as ctx:
            fresh_manager.start("general", "ollama", "qwen2.5:3b", "hello")
        self.assertIn("Test", str(ctx.exception))
        
        # Testing verifies the route
        fresh_manager._verified_connections["ollama"] = "qwen2.5:3b"
        fresh_manager._run_ollama = lambda _task: "synthetic answer"
        task = fresh_manager.start("general", "ollama", "qwen2.5:3b", "hello")
        completed = wait_for_status(fresh_manager, task["id"], "completed")
        self.assertEqual(completed["status"], "completed")

    def test_edit_mode_strictly_requires_exactly_one_vega_source_file(self):
        self.manager._verified_connections["ollama"] = "qwen2.5-coder:3b"
        self.manager._run_ollama = lambda _task: "fake output"
        
        # 0 files selected: rejected
        req_zero = json.dumps({"vega_agent_request": 1, "text": "edit code", "files": []})
        with self.assertRaises(AgentTaskError) as ctx:
            self.manager.start("coding", "ollama", "qwen2.5-coder:3b", req_zero, mode="edit")
        self.assertIn("Select exactly one Vega source file", str(ctx.exception))
        
        # 2 files selected: rejected
        req_two = json.dumps({"vega_agent_request": 1, "text": "edit code", "files": ["f1.py", "f2.py"]})
        with self.assertRaises(AgentTaskError) as ctx:
            self.manager.start("coding", "ollama", "qwen2.5-coder:3b", req_two, mode="edit")
        self.assertIn("Select exactly one Vega source file", str(ctx.exception))
        
        # External attachments without a Vega file: rejected (external attachments are context only)
        req_attach_only = json.dumps({
            "vega_agent_request": 1, "text": "edit code", "files": [],
            "attachments": [{"name": "ext.py", "text": "code"}]
        })
        with self.assertRaises(AgentTaskError) as ctx:
            self.manager.start("coding", "ollama", "qwen2.5-coder:3b", req_attach_only, mode="edit")
        self.assertIn("Select exactly one Vega source file", str(ctx.exception))

    def test_practice_lab_in_memory_isolation_and_safety(self):
        # Initial directory and state
        state = self.practice.state()
        self.assertEqual(state["cwd"], "/home/student")
        self.assertTrue(state["simulated"])
        
        # Basic navigation and file creation
        self.assertEqual(self.practice.command("pwd"), "/home/student")
        self.assertEqual(self.practice.command("mkdir testdir"), "Created simulated directory /home/student/testdir")
        self.assertEqual(self.practice.command("touch testdir/file.txt"), "Created simulated file /home/student/testdir/file.txt")
        self.assertEqual(self.practice.command("cd testdir"), "/home/student/testdir")
        self.assertEqual(self.practice.command("pwd"), "/home/student/testdir")
        
        # Reading simulated file
        self.assertIn("Welcome to the disposable Vega Practice Lab.", self.practice.command("cat /home/student/README.txt"))
        
        # DNF package simulation
        search_res = self.practice.command("dnf search httpd")
        self.assertIn("httpd", search_res)
        install_res = self.practice.command("dnf install httpd")
        self.assertIn("Installed httpd in the disposable simulator", install_res)
        
        # RPM query
        rpm_res = self.practice.command("rpm -qi httpd")
        self.assertIn("Apache HTTP Server", rpm_res)
        
        # Reset restores clean state
        self.practice.reset()
        self.assertEqual(self.practice.command("pwd"), "/home/student")
        self.assertNotIn("/home/student/testdir", self.practice.dirs)
        
        # Host commands and shell operators are completely blocked
        with self.assertRaises(ValueError) as ctx:
            self.practice.command("rm -rf /")
        self.assertIn("not in the simulator", str(ctx.exception))
        
        with self.assertRaises(ValueError) as ctx:
            self.practice.command("cat /etc/passwd; id")
        self.assertIn("Shell operators and scripting are disabled", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            self.practice.command("powershell Get-Process")
        self.assertIn("not in the simulator", str(ctx.exception))

    def test_practice_lab_workspace_navigation_in_app_and_html(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        style_css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        
        # Top tab and workspace wrapper
        self.assertIn('id="mode-practice-tab"', html)
        self.assertIn('id="practice-workspace"', html)
        self.assertIn('id="practice-nav-chat"', html)
        self.assertIn('id="practice-nav-agent"', html)
        
        # Navigation in app.js
        self.assertIn('modePracticeTab.addEventListener("click", () => setAppMode("practice"))', app_js)
        self.assertIn('dailyPracticeLab.addEventListener("click", () => setAppMode("practice"))', app_js)
        self.assertIn('practiceNavChat.addEventListener("click", () => setAppMode("chat"))', app_js)
        self.assertIn('practiceNavAgent.addEventListener("click", () => setAppMode("agent"))', app_js)
        
        # CSS full workspace styling
        self.assertIn('.practice-workspace-wrapper', style_css)
        self.assertIn('.daily-icon svg *', style_css)
        self.assertIn('fill: none !important', style_css)

    def test_lock_state_protects_agent_and_practice_lab(self):
        import vega_webview
        saved_locked = vega_webview.S["locked"]
        try:
            vega_webview.S["locked"] = True
            from unittest.mock import MagicMock
            import bottle
            
            # Simulate a request to /api/agent/tasks
            mock_req = MagicMock()
            mock_req.path = "/api/agent/tasks"
            with patch.object(vega_webview, "request", mock_req):
                with self.assertRaises(bottle.HTTPResponse) as ctx:
                    vega_webview.protect_locked_api()
                self.assertEqual(ctx.exception.status_code, 423)
                body = json.loads(ctx.exception.body)
                self.assertTrue(body["locked"])
                self.assertIn("Vega is locked", body["error"])

            # Simulate a request to /api/practice-lab/command
            mock_req.path = "/api/practice-lab/command"
            with patch.object(vega_webview, "request", mock_req):
                with self.assertRaises(bottle.HTTPResponse) as ctx:
                    vega_webview.protect_locked_api()
                self.assertEqual(ctx.exception.status_code, 423)
                body = json.loads(ctx.exception.body)
                self.assertTrue(body["locked"])
                self.assertIn("Vega is locked", body["error"])
        finally:
            vega_webview.S["locked"] = saved_locked


if __name__ == "__main__":
    unittest.main()
