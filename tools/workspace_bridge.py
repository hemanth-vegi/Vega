"""
tools/workspace_bridge.py - Safe Local Workspace Hooks & Desktop Integration for Vega v2.2.

Features:
1. Safe Clipboard Explanation (reads error from clipboard, generates 1-click fix in Friend/Pro persona).
2. Explicit On-Demand Screen Vision (captures single frame via mss/PIL.ImageGrab, analyzes via Gemini Vision).
3. Strictly Allowlisted Workspace Actions (open_vscode, git_summary, docker_summary).
4. Guarded Global Hotkey Hook (Alt+Shift+V) - strictly non-fatal in Linux/headless CI.
"""
from __future__ import annotations

import os
import io
import subprocess
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("vega.tools.workspace_bridge")

# Allowlisted local repository and project paths for open_vscode and git_summary
ALLOWED_WORKSPACES = {
    os.path.abspath(".").lower(),
    os.path.dirname(os.path.abspath(".")).lower(),
    os.environ.get("VEGA_WORKSPACE_ROOT", os.path.dirname(os.path.abspath("."))).lower(),
}
if os.environ.get("VEGA_ORCHESTRATOR_DIR"):
    ALLOWED_WORKSPACES.add(os.environ["VEGA_ORCHESTRATOR_DIR"].lower())


def _is_path_allowed(target_path: str) -> bool:
    """Verifies candidate path is within an allowlisted workspace boundary."""
    if not target_path:
        return False
    norm = os.path.normcase(os.path.abspath(target_path)).lower()
    for allowed in ALLOWED_WORKSPACES:
        if norm == allowed or norm.startswith(allowed + os.sep):
            return True
    return False


def explain_clipboard(persona: str = "friend") -> Dict[str, Any]:
    """
    Safely reads current clipboard text, diagnoses errors/code in the chosen persona,
    and returns an explanation with a suggested fix.
    """
    # 0. Hermetic CI Mode
    if os.environ.get("VEGA_CI_MODE") == "1":
        return {
            "ok": True,
            "clipboard_text": "AssertionError: Command 'git clean -fdx' should be BLOCKED",
            "explanation": "CI Mock: The test checked regex flags against -fdx. Use -df to match the security pattern.",
            "suggested_fix": 'verdict, reason = check_command_safety("git clean -df")',
            "persona": persona,
        }

    # Lazy import pyperclip for headless Linux CI parity
    try:
        import pyperclip
        text = pyperclip.paste()
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Clipboard reading unavailable: {exc}",
        }

    if not text or not text.strip():
        return {
            "ok": False,
            "error": "Clipboard is empty. Copy an error message or code snippet first.",
        }

    clean_text = text.strip()[:4000]

    try:
        from llm.engine import generate_for_task
        from voice.friend_voice import build_persona_instruction

        system_prompt = build_persona_instruction(persona, english_coach=False)
        user_prompt = (
            f"Here is an error or code snippet from my clipboard:\n\n```\n{clean_text}\n```\n\n"
            "Diagnose the root cause and provide a clear, 1-click copyable fix."
        )

        res = generate_for_task(user_prompt, task_type="fast", system_instruction=system_prompt)
        explanation = res.get("content", "Could not analyze clipboard content.")

        return {
            "ok": True,
            "clipboard_text": clean_text[:500],
            "explanation": explanation,
            "persona": persona,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Analysis failed: {exc}",
        }


def look_at_screen(prompt: str = "What is on my screen? Help me understand and fix any visible errors.") -> Dict[str, Any]:
    """
    Captures a single screenshot on explicit user command and analyzes it with Gemini Vision.
    Respects Low-Data / Offline Mode by failing closed cleanly.
    """
    # 0. Hermetic CI Mode
    if os.environ.get("VEGA_CI_MODE") == "1":
        return {
            "ok": True,
            "analysis": "CI Mock: Screen shows VS Code editor with passing unit tests and clean terminal state.",
            "badge": "CI Mock",
        }

    try:
        from llm.engine import is_offline_or_low_data
        restricted, net_mode = is_offline_or_low_data()
        if restricted:
            return {
                "ok": False,
                "offline": True,
                "message": f"Screen vision analysis is disabled in {net_mode} to preserve bandwidth.",
            }
    except Exception:
        pass

    # Lazy import screen capture libraries
    image_bytes = None
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            sct_img = sct.grab(monitor)
            from PIL import Image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            # Downscale slightly for fast transmission
            img.thumbnail((1280, 720))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            image_bytes = buf.getvalue()
    except Exception:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.thumbnail((1280, 720))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=80)
            image_bytes = buf.getvalue()
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Screen grab failed (headless or missing display): {exc}",
            }

    if not image_bytes:
        return {"ok": False, "error": "Could not capture display buffer."}

    try:
        from voice.friend_voice import get_persona_mode, build_persona_instruction
        persona, coach = get_persona_mode()
        sys_instr = build_persona_instruction(persona, english_coach=coach)

        from llm.engine import chat_get
        # Call 5-brain router / Gemini Vision directly with image attachment
        res = chat_get(
            prompt=prompt,
            task_type="fast",
            images=[image_bytes],
            system_instruction=sys_instr,
            force_provider="gemini"
        )
        if res.get("status") == "success" or res.get("content"):
            content = res.get("content") or res.get("text", "")
            return {
                "ok": True,
                "analysis": content,
                "badge": res.get("badge", "Gemini Vision"),
                "persona": persona,
            }
        else:
            return {
                "ok": False,
                "error": res.get("error", "Vision model returned error."),
            }
    except Exception as exc:
        return {"ok": False, "error": f"Screen analysis error: {exc}"}


def execute_workspace_action(action: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Strictly allowlisted local workspace commands.
    Never executes arbitrary LLM-generated shell commands.
    """
    p = params or {}
    act = (action or "").strip().lower()

    if act == "open_vscode":
        target = p.get("path", ".").strip()
        abs_target = os.path.abspath(target)
        if not _is_path_allowed(abs_target):
            return {"ok": False, "error": f"Security restriction: Path '{abs_target}' is outside authorized workspaces."}

        try:
            cmd = ["code", abs_target]
            subprocess.Popen(cmd, shell=False)
            return {"ok": True, "message": f"Opened VS Code at '{abs_target}'"}
        except Exception as exc:
            return {"ok": False, "error": f"Failed to launch VS Code: {exc}"}

    elif act == "git_summary":
        target = p.get("path", ".").strip()
        abs_target = os.path.abspath(target)
        if not _is_path_allowed(abs_target):
            return {"ok": False, "error": f"Security restriction: Path '{abs_target}' is outside authorized workspaces."}

        try:
            st_res = subprocess.run(
                ["git", "status", "--short"],
                cwd=abs_target,
                capture_output=True,
                text=True,
                timeout=4,
            )
            log_res = subprocess.run(
                ["git", "log", "-1", "--oneline"],
                cwd=abs_target,
                capture_output=True,
                text=True,
                timeout=4,
            )
            return {
                "ok": True,
                "status": st_res.stdout.strip(),
                "latest_commit": log_res.stdout.strip(),
            }
        except Exception as exc:
            return {"ok": False, "error": f"Git summary failed: {exc}"}

    elif act == "docker_summary":
        try:
            res = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
                return {
                    "ok": True,
                    "running_containers": len(lines),
                    "containers": lines,
                }
            else:
                return {"ok": True, "running_containers": 0, "containers": [], "notice": "Docker daemon not active."}
        except Exception as exc:
            return {"ok": True, "running_containers": 0, "containers": [], "notice": f"Docker check skipped: {exc}"}

    return {
        "ok": False,
        "error": f"Action '{action}' is not in the security allowlist. Only open_vscode, git_summary, and docker_summary are permitted.",
    }


def setup_global_hotkey(callback=None) -> bool:
    """Registers Alt+Shift+V global hotkey safely. Non-fatal in Linux/CI environments."""
    if os.name != "nt" or os.environ.get("VEGA_CI_MODE") == "1":
        return False
    try:
        import keyboard
        if callback:
            keyboard.add_hotkey("alt+shift+v", callback)
            return True
    except Exception:
        pass
    return False
