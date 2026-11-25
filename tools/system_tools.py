"""
System automation tools for Vega 2.0.
Volume, media controls, app launching, VirtualBox, and safe system shutdown.
"""

import os
import subprocess
import webbrowser
from core.config import get_system_apps_dict, find_vboxmanage
from tools.registry import tools


@tools.register(
    name="launch_application",
    description="Opens an installed application or common utility on Windows (e.g. notepad, calculator, terminal, code, browser).",
    parameters={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "Name of the application to open, e.g. 'notepad', 'code', 'calculator'"}
        },
        "required": ["app_name"]
    }
)
def launch_application(app_name: str) -> str:
    apps = get_system_apps_dict()
    target = apps.get(app_name.lower().strip())
    if target:
        try:
            if target.startswith("http://") or target.startswith("https://"):
                webbrowser.open(target)
            elif ":" in target and not os.path.exists(target) and not target.endswith(".exe"):
                # Protocol URL like ms-settings: or spotify:
                os.startfile(target)
            else:
                import shlex
                cmd_args = shlex.split(target, posix=(os.name != "nt")) if isinstance(target, str) else [target]
                subprocess.Popen(cmd_args, shell=False)
            return f"Opened {app_name} successfully."
        except Exception as e:
            return f"Failed to open {app_name}: {e}"
    else:
        # Try direct process execution without shell
        try:
            import shlex
            cmd_args = shlex.split(app_name, posix=(os.name != "nt")) if isinstance(app_name, str) else [app_name]
            subprocess.Popen(cmd_args, shell=False)
            return f"Launched {app_name} directly."
        except Exception as e:
            return f"Could not find or launch application '{app_name}': {e}"


@tools.register(
    name="adjust_volume",
    description="Adjusts or mutes Windows system volume.",
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set", "mute", "unmute", "up", "down"],
                "description": "Volume action to perform"
            },
            "level": {
                "type": "integer",
                "description": "Volume level percentage (0 to 100) if action is 'set'"
            }
        },
        "required": ["action"]
    }
)
def adjust_volume(action: str, level: int = 50) -> str:
    try:
        from vega_control import set_volume, mute_volume, unmute_volume, volume_up, volume_down
        if action == "mute":
            mute_volume()
            return "Volume muted."
        elif action == "unmute":
            unmute_volume()
            return "Volume unmuted."
        elif action == "set":
            set_volume(level)
            return f"Volume set to {level}%."
        elif action == "up":
            volume_up()
            return "Volume increased."
        elif action == "down":
            volume_down()
            return "Volume decreased."
    except Exception as e:
        return f"Volume adjustment error: {e}"
    return "Action completed."


@tools.register(
    name="manage_virtual_machine",
    description="Starts, stops, or checks state of a VirtualBox VM.",
    parameters={
        "type": "object",
        "properties": {
            "vm_name": {"type": "string", "description": "Name of the VM in VirtualBox"},
            "action": {"type": "string", "enum": ["start", "stop", "status", "savestate"], "description": "VM operation"}
        },
        "required": ["vm_name", "action"]
    }
)
def manage_virtual_machine(vm_name: str, action: str) -> str:
    vbox = find_vboxmanage()
    if not vbox:
        return "VirtualBox VBoxManage executable not found on this system."

    try:
        if action == "start":
            subprocess.Popen([vbox, "startvm", vm_name, "--type", "headless"])
            return f"Starting VM '{vm_name}' headlessly."
        elif action == "stop":
            subprocess.run([vbox, "controlvm", vm_name, "acpipowerbutton"], check=True)
            return f"Sent ACPI shutdown to VM '{vm_name}'."
        elif action == "savestate":
            subprocess.run([vbox, "controlvm", vm_name, "savestate"], check=True)
            return f"Saved state for VM '{vm_name}'."
        elif action == "status":
            res = subprocess.run([vbox, "showvminfo", vm_name, "--machinereadable"], capture_output=True, text=True)
            for line in res.stdout.splitlines():
                if line.startswith('VMState='):
                    val = line.split('=')[1].strip('"')
                    return f"VM '{vm_name}' status: {val}"
            return f"VM '{vm_name}' found, state pending."
    except Exception as e:
        return f"VirtualBox error: {e}"


@tools.register(
    name="system_power_control",
    description="Shuts down or restarts the Windows PC. Marked DANGEROUS to require confirmation.",
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["shutdown", "restart", "lock"], "description": "Power action"},
            "confirmed": {"type": "boolean", "description": "Explicit confirmation must be true for shutdown or restart"}
        },
        "required": ["action"]
    },
    dangerous=True
)
def system_power_control(action: str, confirmed: bool = False) -> str:
    if action == "lock":
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return "Workstation locked."
        return "Workstation lock is not supported on this platform."
    elif action in ("shutdown", "restart"):
        if not confirmed:
            return f"Confirmation required: call system_power_control(action='{action}', confirmed=True) to proceed."
        import shutil
        shutdown_bin = shutil.which("shutdown") or "shutdown"
        flag = "/s" if action == "shutdown" else "/r"
        if os.name == "nt":
            subprocess.run([shutdown_bin, flag, "/t", "10"], shell=False, check=False)
        else:
            flag = "-h" if action == "shutdown" else "-r"
            subprocess.run([shutdown_bin, flag, "+1"], shell=False, check=False)
        return f"System {action} scheduled in 10 seconds."
    return "Unknown power action."
