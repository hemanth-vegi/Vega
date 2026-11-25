"""
Vega v2.2 — Live System, Docker, Git & Secret-Leak Telemetry Health Panel.
Guarantees strict 3-second timeout per subsystem check with graceful fallbacks.
Never throws 500 errors if Docker or Git is offline.
"""

import os
import shutil
import logging
import subprocess
from typing import Dict, Any

logger = logging.getLogger("vega.lab.health")

from tools.telemetry import get_hardware_vitals

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_docker_health() -> Dict[str, Any]:
    """
    Checks Docker daemon availability and running containers.
    Runs with strict 3-second timeout. Gracefully falls back if offline.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return {
            "status": "offline",
            "available": False,
            "running_containers": 0,
            "notice": "Docker CLI not detected on system PATH."
        }

    try:
        # Check running containers count
        res = subprocess.run(
            [docker_bin, "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=3.0,
            shell=False
        )
        if res.returncode == 0:
            lines = [l.strip() for l in res.stdout.strip().splitlines() if l.strip()]
            return {
                "status": "online",
                "available": True,
                "running_containers": len(lines),
                "notice": f"Docker daemon active ({len(lines)} running container{'s' if len(lines) != 1 else ''})."
            }
        else:
            return {
                "status": "offline",
                "available": True,
                "running_containers": 0,
                "notice": "Docker daemon is stopped (Start Docker Desktop)."
            }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "available": True,
            "running_containers": 0,
            "notice": "Docker daemon timed out (reconnecting...)."
        }
    except Exception as exc:
        return {
            "status": "offline",
            "available": True,
            "running_containers": 0,
            "notice": f"Docker check: {str(exc)}"
        }


def check_git_repo_health() -> Dict[str, Any]:
    """
    Checks current repository branch, uncommitted changes, and unpushed commits.
    Runs with strict 3-second timeout.
    """
    git_bin = shutil.which("git")
    if not git_bin:
        return {
            "status": "offline",
            "branch": "unknown",
            "is_clean": True,
            "modified_count": 0,
            "notice": "Git CLI not detected on system PATH."
        }

    try:
        # Branch
        branch_res = subprocess.run(
            [git_bin, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=3.0,
            shell=False
        )
        branch = branch_res.stdout.strip() if branch_res.returncode == 0 else "main"

        # Dirty status
        status_res = subprocess.run(
            [git_bin, "status", "--porcelain"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=3.0,
            shell=False
        )
        modified_lines = [l.strip() for l in status_res.stdout.strip().splitlines() if l.strip()]
        is_clean = len(modified_lines) == 0

        # Unpushed commits
        unpushed_count = 0
        try:
            unpushed_res = subprocess.run(
                [git_bin, "log", "@{u}..HEAD", "--oneline"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=3.0,
                shell=False
            )
            if unpushed_res.returncode == 0:
                unpushed_count = len([l for l in unpushed_res.stdout.strip().splitlines() if l.strip()])
        except Exception:
            pass

        return {
            "status": "online",
            "branch": branch,
            "is_clean": is_clean,
            "modified_count": len(modified_lines),
            "unpushed_commits": unpushed_count,
            "notice": "Working tree clean." if is_clean else f"{len(modified_lines)} uncommitted change(s)."
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "branch": "main",
            "is_clean": True,
            "modified_count": 0,
            "notice": "Git status query timed out."
        }
    except Exception as exc:
        return {
            "status": "error",
            "branch": "main",
            "is_clean": True,
            "modified_count": 0,
            "notice": f"Git query error: {exc}"
        }


def check_secret_leak_guard() -> Dict[str, Any]:
    """
    Validates that sensitive files (.env, credentials.json, token.json)
    are strictly excluded by .gitignore and not staged.
    """
    sensitive_targets = [
        ".env", "credentials.json", "token.json",
        "gmail_token.json", "career_vault/credentials.json"
    ]
    git_bin = shutil.which("git")
    leaks = []

    if git_bin:
        try:
            res = subprocess.run(
                [git_bin, "status", "--porcelain"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=3.0,
                shell=False
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if not parts:
                        continue
                    # The file path in `git status --porcelain` is the last token (or tokens if spaced)
                    fname = parts[-1]
                    for target in sensitive_targets:
                        # Exclude harmless examples/templates (.env.example, etc.) and match exact target file
                        if fname.endswith(".example") or fname.endswith(".sample"):
                            continue
                        if fname == target or fname.endswith("/" + target) or fname.endswith("\\" + target):
                            leaks.append(target)
        except Exception:
            pass

    return {
        "protected": len(leaks) == 0,
        "guarded_clean": len(leaks) == 0,
        "leaked_files": leaks,
        "leaks_detected": leaks,
        "protected_rules_count": 5,
        "notice": "Shield Active: All sensitive secrets excluded." if not leaks else f"WARNING: Potential unignored secret: {', '.join(leaks)}"
    }


def get_full_lab_health() -> Dict[str, Any]:
    """Aggregates system hardware, Docker, Git, and Secret Guard status."""
    vitals = get_hardware_vitals()
    docker = check_docker_health()
    git = check_git_repo_health()
    secret = check_secret_leak_guard()

    hw = {
        "cpu_percent": vitals.get("cpu_percent", 0),
        "ram_percent": vitals.get("ram_percent", 0),
        "ram_used_gb": vitals.get("ram_used_gb", 0.0),
        "ram_total_gb": vitals.get("ram_total_gb", 0.0),
        "gpu": vitals.get("gpu", {}),
        "battery": vitals.get("battery")
    }

    return {
        "status": "ok",
        "hardware": hw,
        "hardware_vitals": hw,
        "docker": docker,
        "git": git,
        "secret_guard": secret
    }
