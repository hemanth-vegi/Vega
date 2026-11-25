"""
Developer Hardware, VRAM, and Model Telemetry for Vega 2.0.
Polls CPU, RAM, Battery, NVIDIA GPU/VRAM, and Ollama status via a 1500ms non-blocking background loop.
"""

import os
import time
import json
import threading
import subprocess
import urllib.request
import psutil
from typing import Dict, Any
from tools.registry import tools

# Thread safety lock and global cached state
_CACHE_LOCK = threading.Lock()
_CACHED_VITALS: Dict[str, Any] = {
    "cpu_percent": 0,
    "ram_percent": 0,
    "ram_used_gb": 0.0,
    "ram_total_gb": 0.0,
    "battery": None,
    "gpu": {
        "utilization_percent": 0,
        "vram_used_mb": 0,
        "vram_total_mb": 4096,
        "vram_free_mb": 4096,
    },
    "model": {
        "name": "qwen2.5:3b",
        "status": "ONLINE",
        "vram_mb": 0,
        "tok_per_sec": 0.0,
        "available_models": [],
    },
    "last_updated": 0.0,
}

_IS_GENERATING = False
_LAST_TOK_SPEED = 0.0
_TELEMETRY_THREAD_STARTED = False


def get_free_vram_gb() -> float:
    """
    Returns free VRAM in Gigabytes using pynvml if installed, falling back to nvidia-smi.
    Guarantees a numeric return value.
    """
    try:
        import pynvml
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()
        return round(float(info.free) / (1024.0 ** 3), 2)
    except Exception:
        pass

    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1.5
        )
        if res.returncode == 0 and res.stdout.strip():
            free_mb = float(res.stdout.strip().split("\n")[0].strip())
            return round(free_mb / 1024.0, 2)
    except Exception:
        pass

    with _CACHE_LOCK:
        gpu = _CACHED_VITALS.get("gpu", {})
        free_mb = gpu.get("vram_free_mb", 4096)
        return round(float(free_mb) / 1024.0, 2)


def check_vram_preflight(min_gb: float = 1.2) -> Dict[str, Any]:
    """
    VRAM Pre-Flight Check:
    Verifies that free VRAM is >= min_gb (default 1.2 GB) before executing local Ollama inference.
    Returns status dict with warning message if below threshold.
    """
    free_gb = get_free_vram_gb()
    if free_gb < min_gb:
        return {
            "ok": False,
            "free_gb": free_gb,
            "min_gb": min_gb,
            "warning": f"Warning: Low VRAM detected ({free_gb} GB free, requires {min_gb} GB). Routing to Cloud or please close background apps."
        }
    return {
        "ok": True,
        "free_gb": free_gb,
        "min_gb": min_gb,
        "warning": None
    }


def set_generating_status(generating: bool):
    global _IS_GENERATING
    _IS_GENERATING = bool(generating)


def record_streaming_metric(tok_per_sec: float, is_generating: bool = True):
    global _IS_GENERATING, _LAST_TOK_SPEED
    _IS_GENERATING = is_generating
    if tok_per_sec > 0:
        _LAST_TOK_SPEED = round(tok_per_sec, 1)


def _poll_hardware_and_model():
    """Single synchronous telemetry collection step run exclusively in the background thread."""
    global _LAST_TOK_SPEED

    # 1. CPU & RAM
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    ram_used_gb = round((mem.total - mem.available) / (1024 ** 3), 1)
    ram_total_gb = round(mem.total / (1024 ** 3), 1)

    # 2. Battery
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

    # 3. NVIDIA GPU/VRAM
    gpu_info = None
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=1.0
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            if len(parts) >= 3:
                gpu_info = {
                    "utilization_percent": int(parts[0]),
                    "vram_used_mb": int(parts[1]),
                    "vram_total_mb": int(parts[2]),
                    "vram_free_mb": max(0, int(parts[2]) - int(parts[1]))
                }
    except Exception:
        pass

    # Fallback GPU if nvidia-smi failed or unavailable
    if gpu_info is None:
        with _CACHE_LOCK:
            gpu_info = _CACHED_VITALS.get("gpu", {
                "utilization_percent": 0,
                "vram_used_mb": 0,
                "vram_total_mb": 4096,
                "vram_free_mb": 4096
            })

    # 4. Ollama Model & Health
    model_name = "qwen2.5:3b"
    model_status = "ONLINE"
    model_vram_mb = 0
    available_models = []

    try:
        # Check active models in VRAM via /api/ps
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps", headers={"User-Agent": "Vega/2.1"})
        with urllib.request.urlopen(req, timeout=0.8) as resp:
            ps_data = json.loads(resp.read().decode())
            active_list = ps_data.get("models", [])
            if active_list:
                first = active_list[0]
                model_name = first.get("name") or first.get("model") or model_name
                size_vram = first.get("size_vram", 0)
                if size_vram > 0:
                    model_vram_mb = round(size_vram / (1024 * 1024))

        # Check tags list via /api/tags
        req_tags = urllib.request.Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "Vega/2.1"})
        with urllib.request.urlopen(req_tags, timeout=0.8) as resp_tags:
            tags_data = json.loads(resp_tags.read().decode())
            available_models = [m["name"] for m in tags_data.get("models", []) if "name" in m]

    except Exception:
        # Check if Gemini key is present if Ollama is unreachable
        if os.environ.get("GEMINI_API_KEY"):
            model_name = "Gemini Flash"
            model_status = "ONLINE"
        else:
            model_status = "OFFLINE"

    if _IS_GENERATING:
        model_status = "GENERATING"

    tok_per_sec = _LAST_TOK_SPEED if _IS_GENERATING else 0.0

    # Commit snapshot to cache
    with _CACHE_LOCK:
        _CACHED_VITALS["cpu_percent"] = round(cpu_percent)
        _CACHED_VITALS["ram_percent"] = round(mem.percent)
        _CACHED_VITALS["ram_used_gb"] = ram_used_gb
        _CACHED_VITALS["ram_total_gb"] = ram_total_gb
        _CACHED_VITALS["battery"] = battery_info
        _CACHED_VITALS["gpu"] = gpu_info
        _CACHED_VITALS["model"] = {
            "name": model_name,
            "status": model_status,
            "vram_mb": model_vram_mb,
            "tok_per_sec": tok_per_sec,
            "available_models": available_models,
        }
        _CACHED_VITALS["last_updated"] = time.time()


def _telemetry_background_worker():
    """Background telemetry poller running every 1500ms (1.5 seconds)."""
    while True:
        try:
            _poll_hardware_and_model()
        except Exception:
            pass
        time.sleep(1.5)


def start_telemetry_loop():
    """Start the telemetry background loop thread if not already active."""
    global _TELEMETRY_THREAD_STARTED
    if not _TELEMETRY_THREAD_STARTED:
        _TELEMETRY_THREAD_STARTED = True
        # Do initial synchronous fast poll so cache is immediately warm
        try:
            _poll_hardware_and_model()
        except Exception:
            pass
        t = threading.Thread(target=_telemetry_background_worker, daemon=True, name="VegaTelemetryWorker")
        t.start()


# Ensure telemetry background thread starts when module is imported
start_telemetry_loop()


def get_hardware_vitals() -> Dict[str, Any]:
    """Returns cached real-time telemetry instantly without blocking caller."""
    with _CACHE_LOCK:
        import copy
        if not _CACHED_VITALS:
            try:
                _poll_hardware_and_model()
            except Exception:
                pass
        return copy.deepcopy(_CACHED_VITALS)


@tools.register(
    name="get_hardware_telemetry",
    description="Returns real-time workstation hardware vitals: CPU %, RAM usage, free VRAM, GPU utilization, and model status.",
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
    if vitals.get("model"):
        m = vitals["model"]
        lines.append(f"AI Model: {m['name']} ({m['status']}) - VRAM: {m['vram_mb']} MB")
    return "\n".join(lines)
