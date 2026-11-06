"""
Developer Hardware and VRAM Telemetry for Vega 2.0.
Polls CPU, RAM, Battery, and NVIDIA GPU/VRAM for HUD & real-time monitoring.
"""

import subprocess
import psutil
from typing import Dict, Any
from tools.registry import tools


def get_hardware_vitals() -> Dict[str, Any]:
    """Collects real-time CPU, RAM, Battery, and NVIDIA GPU/VRAM statistics."""
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()

    battery_info = None
    try:
        batt = psutil.sensors_battery()
        if batt:
            battery_info = {
                "percent": round(batt.percent),
                "power_plugged": batt.power_plugged
            }
    except Exception:
        pass

    # Query NVIDIA GPU if available
    gpu_info = None
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2.0
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            if len(parts) >= 3:
                gpu_info = {
                    "utilization_percent": int(parts[0]),
                    "vram_used_mb": int(parts[1]),
                    "vram_total_mb": int(parts[2]),
                    "vram_free_mb": int(parts[2]) - int(parts[1])
                }
    except Exception:
        pass

    ram_used_gb = round((mem.total - mem.available) / (1024 ** 3), 1)
    ram_total_gb = round(mem.total / (1024 ** 3), 1)

    return {
        "cpu_percent": round(cpu_percent),
        "ram_percent": round(mem.percent),
        "ram_used_gb": ram_used_gb,
        "ram_total_gb": ram_total_gb,
        "battery": battery_info,
        "gpu": gpu_info
    }


@tools.register(
    name="get_hardware_telemetry",
    description="Returns real-time workstation hardware vitals: CPU %, RAM usage, free VRAM, and GPU utilization.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    }
)
def get_hardware_telemetry() -> str:
    vitals = get_hardware_vitals()
    lines = [
        f"CPU Usage: {vitals['cpu_percent']}%",
        f"RAM: {vitals['ram_used_gb']} GB / {vitals['ram_total_gb']} GB ({vitals['ram_percent']}%)"
    ]
    if vitals.get("battery"):
        plug = "Plugged in" if vitals["battery"]["power_plugged"] else "On Battery"
        lines.append(f"Battery: {vitals['battery']['percent']}% ({plug})")
    if vitals.get("gpu"):
        g = vitals["gpu"]
        lines.append(f"NVIDIA GPU: {g['utilization_percent']}% | VRAM: {g['vram_used_mb']} MB / {g['vram_total_mb']} MB ({g['vram_free_mb']} MB free)")
    return "\n".join(lines)

