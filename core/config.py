"""
Dynamic Path Discovery and System Configuration for Vega 2.0.
Automatically detects VirtualBox, Browsers, VS Code, and system binaries.
"""

import os
import shutil
import winreg
from typing import Optional, Dict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_vboxmanage() -> Optional[str]:
    """Finds VBoxManage executable dynamically."""
    which_vbox = shutil.which("VBoxManage")
    if which_vbox and os.path.exists(which_vbox):
        return which_vbox

    standard_paths = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Oracle\VirtualBox\VBoxManage.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Oracle\VirtualBox\VBoxManage.exe"),
    ]
    for p in standard_paths:
        if os.path.isfile(p):
            return p

    # Check Windows Registry
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Oracle\VirtualBox") as key:
            install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
            candidate = os.path.join(install_dir, "VBoxManage.exe")
            if os.path.isfile(candidate):
                return candidate
    except Exception:
        pass

    return None


def find_vscode() -> Optional[str]:
    """Finds VS Code executable dynamically."""
    which_code = shutil.which("code")
    if which_code and os.path.exists(which_code):
        return which_code

    user_code = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\bin\code.cmd")
    if os.path.isfile(user_code):
        return user_code

    prog_code = os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Microsoft VS Code\bin\code.cmd")
    if os.path.isfile(prog_code):
        return prog_code

    return None


def find_browser_path(browser_name: str = "brave") -> Optional[str]:
    """Dynamically finds browser executables."""
    b = browser_name.lower().strip()
    which_b = shutil.which(b)
    if which_b and os.path.exists(which_b):
        return which_b

    paths = {
        "brave": [
            os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ],
        "chrome": [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Google\Chrome\Application\chrome.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
        "edge": [
            os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Microsoft\Edge\Application\msedge.exe"),
        ],
        "firefox": [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), r"Mozilla Firefox\firefox.exe"),
        ]
    }

    for candidate in paths.get(b, []):
        if os.path.isfile(candidate):
            return candidate

    return None


def get_system_apps_dict() -> Dict[str, str]:
    """Returns dynamic mapped applications."""
    vbox = find_vboxmanage()
    code = find_vscode() or "code"
    brave = find_browser_path("brave") or "brave"
    chrome = find_browser_path("chrome") or "chrome"
    edge = find_browser_path("edge") or "msedge"

    return {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "paint": "mspaint.exe",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "task manager": "taskmgr.exe",
        "settings": "ms-settings:",
        "command prompt": "cmd.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "terminal": "wt.exe",
        "vscode": code,
        "vs code": code,
        "brave": brave,
        "chrome": chrome,
        "edge": edge,
        "virtualbox": vbox or "VirtualBox.exe",
    }

