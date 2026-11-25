"""
Unified 5-Brain Smart Auto-Router & Inference Engine for Vega v2.2.
Routes tasks deterministically across:
1. Groq (Ultra-fast cloud inference)
2. Google Gemini (Multimodal & deep reasoning flagship)
3. NVIDIA NIM (Cloud GPU acceleration)
4. OpenRouter (Multi-model aggregated router)
5. Laptop / On-device Ollama (100% private local fallback)
"""

import json
import os
import re
import time
import socket
import logging
import requests
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("vega.llm.engine")
from tools.registry import ToolRegistry
from tools.telemetry import check_vram_preflight

OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_LITE_MODEL = os.getenv("GEMINI_LITE_MODEL", "gemini-3.5-flash-lite")

AIDER_SYSTEM_INSTRUCTIONS = (
    "You are an expert software engineer. To edit local workspace files, you must use the SEARCH/REPLACE block format.\n"
    "State the file path, output a block starting with `<<<<<<< SEARCH`, include the exact original lines, output `=======`, include the new code, and end with `>>>>>>> REPLACE`."
)


def _coerce_text(value: Any) -> str:
    """Safely extracts a clean string from responses, dicts, errors, or primitives.

    Prevents 'dict' object has no attribute 'strip' when provider responses or error
    payloads are dictionaries during network or offline toggles.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return str(value)
    if isinstance(value, dict):
        for key in ("text", "content", "response", "reply", "answer", "error"):
            v = value.get(key)
            if v is not None and not isinstance(v, (dict, list, tuple)):
                s = str(v)
                if s.strip():
                    return s
            if isinstance(v, (dict, list, tuple)):
                extracted = _coerce_text(v)
                if extracted.strip():
                    return extracted
        msg = value.get("message")
        if isinstance(msg, dict):
            extracted = _coerce_text(msg)
            if extracted.strip():
                return extracted
        elif msg is not None:
            return str(msg)
        delta = value.get("delta")
        if isinstance(delta, dict):
            extracted = _coerce_text(delta)
            if extracted.strip():
                return extracted
        elif delta is not None:
            return str(delta)
        for list_key in ("choices", "candidates"):
            items = value.get(list_key)
            if isinstance(items, (list, tuple)) and items:
                extracted = _coerce_text(items[0])
                if extracted.strip():
                    return extracted
        parts = value.get("parts")
        if isinstance(parts, (list, tuple)) and parts:
            extracted = _coerce_text(parts[0])
            if extracted.strip():
                return extracted
        for container in ("data", "result"):
            nested = value.get(container)
            if nested is not None:
                extracted = _coerce_text(nested)
                if extracted.strip():
                    return extracted
        return ""
    if isinstance(value, (list, tuple)):
        parts_list = [_coerce_text(item) for item in value if item]
        return " ".join(p for p in parts_list if p)
    return str(value)


coerce_text = _coerce_text


def load_env_keys(env_path: Optional[str] = None) -> Dict[str, str]:
    """Reads all provider API keys from os.environ or .env file."""
    keys = {
        "gemini": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", ""),
        "groq": os.environ.get("GROQ_API_KEY", ""),
        "nvidia": os.environ.get("NVIDIA_API_KEY", ""),
        "openrouter": os.environ.get("OPENROUTER_API_KEY", ""),
        "cerebras": os.environ.get("CEREBRAS_API_KEY", ""),
        "mistral": os.environ.get("MISTRAL_API_KEY", ""),
    }

    if not all(keys.values()):
        if not env_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env_path = os.path.join(base_dir, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip('"').strip("'")
                            if k in ("GEMINI_API_KEY", "GOOGLE_API_KEY") and not keys["gemini"]:
                                keys["gemini"] = v
                                os.environ["GEMINI_API_KEY"] = v
                            elif k == "GROQ_API_KEY" and not keys["groq"]:
                                keys["groq"] = v
                                os.environ["GROQ_API_KEY"] = v
                            elif k == "NVIDIA_API_KEY" and not keys["nvidia"]:
                                keys["nvidia"] = v
                                os.environ["NVIDIA_API_KEY"] = v
                            elif k == "OPENROUTER_API_KEY" and not keys["openrouter"]:
                                keys["openrouter"] = v
                                os.environ["OPENROUTER_API_KEY"] = v
                            elif k == "CEREBRAS_API_KEY" and not keys["cerebras"]:
                                keys["cerebras"] = v
                                os.environ["CEREBRAS_API_KEY"] = v
                            elif k == "MISTRAL_API_KEY" and not keys["mistral"]:
                                keys["mistral"] = v
                                os.environ["MISTRAL_API_KEY"] = v
            except Exception:
                pass
    return keys


_CONNECTIVITY_CACHE = {"last_check": 0.0, "is_online": True}


def check_internet_connectivity(host: str = "1.1.1.1", port: int = 53, timeout: float = 0.8, force: bool = False) -> bool:
    """
    Lightweight socket ping to determine if an external internet route is reachable.
    Cached for 5 seconds to prevent socket exhaustion during rapid turns.
    """
    now = time.time()
    if not force and (now - _CONNECTIVITY_CACHE["last_check"] < 5.0):
        return _CONNECTIVITY_CACHE["is_online"]

    is_online = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        is_online = True
    except Exception:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(("8.8.8.8", 53))
            sock.close()
            is_online = True
        except Exception:
            is_online = False

    _CONNECTIVITY_CACHE["last_check"] = now
    _CONNECTIVITY_CACHE["is_online"] = is_online
    return is_online


def is_offline_or_low_data() -> Tuple[bool, str]:
    """
    Determines if Vega is in a restricted network mode.
    Returns:
        (is_restricted: bool, mode_name: str)
        - 'offline_only' -> (True, 'offline_only')
        - 'low_data'     -> (True, 'low_data')
        - 'auto'         -> (True, 'auto_offline') if socket check fails, else (False, 'auto_online')
    """
    if os.environ.get("VEGA_OFFLINE") == "1":
        return True, "offline_only"

    try:
        from career.db import get_network_mode
        mode = get_network_mode()
    except Exception:
        mode = "auto"

    if mode == "offline_only":
        return True, "offline_only"
    if mode == "low_data":
        return True, "low_data"

    online = check_internet_connectivity()
    if not online:
        return True, "auto_offline"

    return False, "auto_online"


def get_orchestrator_config() -> Tuple[bool, str]:
    """Returns (enabled, base_url) for AI-Agent-Orchestrator bridge."""
    raw_url = os.environ.get("VEGA_ORCHESTRATOR_URL") or os.environ.get("ORCHESTRATOR_BASE_URL") or "http://127.0.0.1:8765"
    base_url = raw_url.strip().rstrip("/")
    enabled_str = os.environ.get("ORCHESTRATOR_ENABLED", "1").strip().lower()
    enabled = enabled_str not in ("0", "false", "no", "disabled")
    return enabled, base_url


def get_orchestrator_status(timeout: float = 1.0) -> Dict[str, Any]:
    """
    Probes AI-Agent-Orchestrator proxy with strict 1.0s timeout.
    Returns:
    {
        "ok": True,
        "online": bool,
        "url": base_url,
        "active_provider": "orchestrator",
        "active_model": "auto-failover-orchestrator",
        "models": [...]
    }
    """
    enabled, base_url = get_orchestrator_config()
    if os.environ.get("VEGA_CI_MODE") == "1":
        return {
            "ok": True,
            "online": True,
            "url": base_url,
            "active_provider": "orchestrator",
            "active_model": "auto-failover-orchestrator",
            "models": ["auto-failover-orchestrator", "auto-failover", "cascade-cloud", "local-fast"]
        }
    if not enabled:
        return {
            "ok": True,
            "online": False,
            "url": base_url,
            "error": "Orchestrator disabled in settings"
        }

    root_url = base_url.replace("/v1", "")
    try:
        import urllib.request
        req = urllib.request.Request(f"{root_url}/health", headers={"User-Agent": "Vega/2.2"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            is_healthy = (resp.status == 200)
    except Exception:
        try:
            import urllib.request
            req = urllib.request.Request(f"{root_url}/v1/status", headers={"User-Agent": "Vega/2.2"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                is_healthy = (resp.status == 200)
        except Exception as exc:
            return {
                "ok": True,
                "online": False,
                "url": base_url,
                "error": str(exc)
            }

    models_list = ["auto-failover-orchestrator"]
    try:
        import urllib.request
        req_m = urllib.request.Request(f"{root_url}/v1/models", headers={"User-Agent": "Vega/2.2"})
        with urllib.request.urlopen(req_m, timeout=timeout) as m_resp:
            m_data = json.loads(m_resp.read().decode("utf-8"))
            if isinstance(m_data, dict) and "data" in m_data:
                models_list = [m["id"] for m in m_data["data"] if isinstance(m, dict) and "id" in m]
    except Exception:
        pass

    return {
        "ok": True,
        "online": True,
        "url": base_url,
        "active_provider": "orchestrator",
        "active_model": "auto-failover-orchestrator",
        "models": models_list
    }


def check_orchestrator_health(timeout: float = 1.0) -> bool:
    """Checks if AI-Agent-Orchestrator proxy server is reachable and healthy."""
    st = get_orchestrator_status(timeout=timeout)
    return bool(st.get("online"))


def estimate_tokens(text: str) -> int:
    """Fast conservative token estimation for prompt length guards."""
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    return max(int(words * 1.35), int(chars / 3.6))


def parse_aider_diff(text: Any) -> Optional[Dict[str, Any]]:
    """Parses Aider SEARCH/REPLACE block from text."""
    text = _coerce_text(text)
    if not text:
        return None

    pat_with_file = re.compile(
        r"(?:(?:file|target|path)?\s*[:`*]*\s*([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)[`*]*\s*[\r\n]+)?"
        r"<{7}\s*SEARCH\s*[\r\n]([\s\S]*?)[\r\n]={7}\s*[\r\n]([\s\S]*?)[\r\n]>{7}\s*REPLACE",
        re.I
    )
    m = pat_with_file.search(text)
    if not m:
        return None

    file_path = m.group(1) or ""
    if not file_path:
        before = text[:m.start()]
        file_candidates = re.findall(r"([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9]+)", before)
        if file_candidates:
            file_path = file_candidates[-1]

    return {
        "path": file_path.strip().replace("\\", "/"),
        "search": m.group(2),
        "replace": m.group(3)
    }


# =====================================================================
# PROVIDER HTTP CALLERS
# =====================================================================
def _call_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: float = 25.0
) -> Dict[str, Any]:
    """Helper to query OpenAI-compatible chat completion endpoints."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }

    try:
        url = f"{base_url.rstrip('/')}/chat/completions"
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                return {"status": "success", "content": content, "status_code": 200}
            return {"status": "error", "error": "Empty choices in API response", "status_code": 200}
        else:
            return {"status": "error", "error": f"HTTP {res.status_code}: {res.text[:180]}", "status_code": res.status_code}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "status_code": 500}


def chat_gemini(
    messages: Any,
    api_key: Optional[str] = None,
    model: str = GEMINI_MODEL,
    temperature: float = 0.2,
    max_retries: int = 3,
    system_instruction: Optional[str] = None,
    images: Optional[List[Any]] = None
) -> Dict[str, Any]:
    """
    Call Google Gemini REST API using central GEMINI_MODEL.
    Supports multimodal image payloads and handles HTTP 429 rate limits
    via exponential backoff (1s, 2s, 4s) and graceful fallback to GEMINI_LITE_MODEL.
    """
    if os.environ.get("VEGA_CI_MODE") == "1":
        return {
            "role": "assistant",
            "content": "Vega CI Mode: Verified offline hermetic response.",
            "model": model,
            "status": "success",
            "status_code": 200
        }

    keys = load_env_keys()
    key = api_key or keys.get("gemini")
    if not key:
        return {"error": "Missing GEMINI_API_KEY", "status_code": 401}

    url_base = "https://generativelanguage.googleapis.com/v1beta/models"

    # Support string prompt as shorthand
    if isinstance(messages, str):
        msg_list = [{"role": "user", "content": messages}]
    elif isinstance(messages, list):
        msg_list = list(messages)
    else:
        msg_list = [{"role": "user", "content": str(messages)}]

    contents = []
    # Find last user message index to attach images
    last_user_idx = -1
    for i, m in enumerate(msg_list):
        if m.get("role") == "user":
            last_user_idx = i

    for i, m in enumerate(msg_list):
        role = "user" if m.get("role") == "user" else "model"
        text = m.get("content", "")
        parts: List[Dict[str, Any]] = [{"text": text}]

        if i == last_user_idx and images:
            import base64
            for img in images:
                mime = "image/png"
                b64_str = ""
                if isinstance(img, bytes):
                    b64_str = base64.b64encode(img).decode("utf-8")
                elif isinstance(img, dict):
                    raw_data = img.get("data") or img.get("dataUrl") or img.get("b64") or img.get("image_b64") or ""
                    mime = img.get("mime_type") or img.get("mimeType") or "image/png"
                    file_path = img.get("path") or img.get("image_path")
                    if not raw_data and file_path and os.path.exists(file_path):
                        try:
                            with open(file_path, "rb") as f_img:
                                b64_str = base64.b64encode(f_img.read()).decode("utf-8")
                        except Exception:
                            pass
                    elif raw_data:
                        if "," in raw_data:
                            raw_data = raw_data.split(",", 1)[1]
                        b64_str = raw_data.strip()
                elif isinstance(img, str):
                    if os.path.exists(img):
                        try:
                            with open(img, "rb") as f_img:
                                b64_str = base64.b64encode(f_img.read()).decode("utf-8")
                        except Exception:
                            pass
                    else:
                        raw_data = img
                        if "," in raw_data:
                            raw_data = raw_data.split(",", 1)[1]
                        b64_str = raw_data.strip()

                if b64_str:
                    if "jpeg" in mime.lower() or "jpg" in mime.lower():
                        mime = "image/jpeg"
                    elif "webp" in mime.lower():
                        mime = "image/webp"
                    elif "pdf" in mime.lower():
                        mime = "application/pdf"
                    else:
                        mime = "image/png"
                    parts.append({
                        "inlineData": {
                            "mimeType": mime,
                            "data": b64_str
                        }
                    })

        contents.append({"role": role, "parts": parts})

    payload: Dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 4096
        }
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    current_model = model
    backoff = 1.0

    for attempt in range(max_retries):
        endpoint = f"{url_base}/{current_model}:generateContent?key={key}"
        try:
            res = requests.post(endpoint, json=payload, timeout=35)
            if res.status_code == 200:
                data = res.json()
                cand = data.get("candidates", [{}])[0]
                parts = cand.get("content", {}).get("parts", [{}])
                text_out = "".join(p.get("text", "") for p in parts if p.get("text"))
                return {
                    "role": "assistant",
                    "content": text_out,
                    "model": current_model,
                    "status": "success",
                    "status_code": 200
                }
            elif res.status_code == 429:
                if attempt == max_retries - 1 and current_model != GEMINI_LITE_MODEL:
                    current_model = GEMINI_LITE_MODEL
                time.sleep(backoff)
                backoff *= 2.0
                continue
            else:
                return {"error": f"Gemini error {res.status_code}: {res.text[:180]}", "status_code": res.status_code}
        except Exception as e:
            if attempt == max_retries - 1:
                return {"error": f"Gemini request failed: {e}", "status_code": 500}
            time.sleep(backoff)
            backoff *= 2.0

    return {"error": "Gemini rate limited (429) across retries", "status_code": 429}


# =====================================================================
# TASK-BASED 5-BRAIN AUTO-ROUTER
# =====================================================================
TASK_CASCADES: Dict[str, List[Dict[str, Any]]] = {
    "scam_shield": [
        {"provider": "groq", "model": "qwen/qwen3.8-27b", "fallback_models": ["openai/gpt-oss-20b", "llama-3.3-70b-versatile"]},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite", "fallback_models": ["gemini-3.8-flash"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-lightning-30b-a3b"},
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ],
    "bulk_scan": [
        {"provider": "groq", "model": "qwen/qwen3.8-27b", "fallback_models": ["openai/gpt-oss-20b", "llama-3.3-70b-versatile"]},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite", "fallback_models": ["gemini-3.8-flash"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-lightning-30b-a3b"},
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ],
    "general_chat": [
        {"provider": "groq", "model": "openai/gpt-oss-120b", "fallback_models": ["llama-3.3-70b-versatile", "qwen/qwen3.8-27b"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
        {"provider": "gemini", "model": "gemini-3.6-flash", "fallback_models": ["gemini-3.8-flash", "gemini-3.5-flash-lite"]},
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ],
    "resume_tailor": [
        {"provider": "gemini", "model": "gemini-3.8-flash", "fallback_models": ["gemini-3.5-flash-lite"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
        {"provider": "gemini", "model": "gemini-3.6-flash"},
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ],
    "long_pdf_analysis": [
        {"provider": "gemini", "model": "gemini-3.8-flash", "fallback_models": ["gemini-3.5-flash-lite"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
        {"provider": "gemini", "model": "gemini-3.6-flash"},
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ],
    "ocr_vision": [
        {"provider": "gemini", "model": "gemini-3.8-flash", "fallback_models": ["gemini-3.5-flash-lite", "gemini-2.5-flash"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
        {"provider": "openrouter", "model": "google/gemini-2.0-flash-exp:free"},
        {"provider": "gemini", "model": "gemini-3.5-flash"},
    ],
    "fast": [
        {"provider": "groq", "model": "qwen/qwen3.8-27b", "fallback_models": ["llama-3.3-70b-versatile"]},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ],
    "reasoning": [
        {"provider": "gemini", "model": "gemini-3.8-flash", "fallback_models": ["gemini-3.5-flash-lite"]},
        {"provider": "nvidia", "model": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
        {"provider": "groq", "model": "openai/gpt-oss-120b"},
        {"provider": "ollama", "model": "qwen2.5:3b"},
    ]
}


def generate_for_task(
    prompt: Any,
    task_type: str = "general_chat",
    images: Optional[List[Any]] = None,
    force_provider: Optional[str] = None,
    system_instruction: Optional[str] = None,
    max_tokens: int = 4096,
    temperature: float = 0.2
) -> Dict[str, Any]:
    """
    Task-Based 5-Brain Auto-Router.
    Automatically routes between Groq, Gemini, NVIDIA NIM, OpenRouter, and local Ollama,
    respecting token ceilings (skips Groq if prompt > 5,500 tokens or contains images)
    and performing deterministic failover on HTTP 429/502/503 or missing credentials.

    Returns:
        dict: {
            "content": str,
            "text": str,
            "provider": str,          # "groq" | "gemini" | "nvidia" | "openrouter" | "ollama"
            "model": str,             # served model identifier
            "badge": str,             # "Cloud" | "Local" | "Local / Low-Data" | "CI Mock"
            "status": "success" | "error",
            "fallback_occurred": bool, # True if first candidate failed
            "attempts": list of {"provider": str, "model": str, "status": str, "error": Optional[str]},
            "error": Optional[str],
            "task_type": str
        }
    """
    prompt = _coerce_text(prompt)
    if system_instruction:
        system_instruction = _coerce_text(system_instruction)
    # 0. Hermetic CI Mode Bypass (Zero network calls, zero external secrets)
    if os.environ.get("VEGA_CI_MODE") == "1":
        tokens_est = estimate_tokens(prompt)
        has_images = bool(images and len(images) > 0)
        prov = "groq"
        ci_model = "ci-hermetic-model"
        if has_images or tokens_est > 5500:
            prov = "gemini"
            if has_images:
                ci_model = "gemini-3.8-flash"
        if force_provider and force_provider != "auto":
            prov = force_provider.lower()
            if has_images and prov == "groq":
                prov = "gemini"

        if has_images or task_type == "ocr_vision":
            mock_content = "Vision Analysis: The attached visual content has been processed."
        elif task_type == "scam_shield":
            mock_content = json.dumps({"risk_score": 10, "flags": [], "verdict": "SAFE"})
        elif task_type == "resume_tailor":
            mock_content = "Summary: Experienced Engineer\nSkills: Python, Docker, Kubernetes\nProjects: Vega\nEducation: B.Tech"
        elif task_type == "reasoning" or "study" in prompt.lower() or "lecture" in prompt.lower():
            mock_content = json.dumps({
                "summary": [
                    "Kubernetes Pod Lifecycle transitions: Pending, Running, Succeeded, Failed, and CrashLoopBackOff.",
                    "Liveness and readiness probes ensure traffic routing and self-healing.",
                    "Resource requests and limits safeguard node stability."
                ],
                "flashcards": [
                    {"question": "What is a Pod in Kubernetes?", "answer": "The smallest deployable computing unit.", "explanation": "Core K8s abstraction.", "difficulty": "easy"},
                    {"question": "What is the purpose of a readiness probe?", "answer": "Controls traffic routing to container.", "explanation": "Prevents sending traffic to unready pods.", "difficulty": "medium"}
                ],
                "deadlines": [
                    {"text": "Complete cluster troubleshooting lab", "days_from_now": 5}
                ]
            })
        elif "Principal DevOps Engineer" in prompt:
            mock_content = json.dumps({
                "score": 90,
                "passed": True,
                "feedback": "CI Mock: Great job fixing Docker security and layer caching.",
                "fixed_bugs": ["Running as root", "Copy order"],
                "remaining_bugs": []
            })
        elif task_type == "fast" and "interview" in prompt.lower():
            if "score" in prompt.lower() or "evaluate" in prompt.lower():
                mock_content = json.dumps({
                    "score": 9,
                    "feedback": "Strong explanation of Linux container states and troubleshooting.",
                    "strengths": "Clear OOMKilled diagnosis.",
                    "improvement": "None."
                })
            else:
                mock_content = json.dumps([
                    {"id": 1, "type": "Intro", "question": "Introduce your background with Docker."},
                    {"id": 2, "type": "Linux", "question": "How do you troubleshoot OOMKilled containers?"},
                    {"id": 3, "type": "K8s", "question": "Explain Pod readiness probes."},
                    {"id": 4, "type": "Security", "question": "How do you minimize container image attack surface?"},
                    {"id": 5, "type": "Observability", "question": "What metrics do you monitor for container health?"}
                ])
        else:
            mock_content = "OK"

        return {
            "content": mock_content,
            "text": mock_content,
            "provider": prov,
            "model": ci_model,
            "badge": "CI Mock",
            "status": "success",
            "fallback_occurred": False,
            "attempts": [{"provider": prov, "model": ci_model, "status": "success"}],
            "error": None,
            "task_type": "ocr_vision" if has_images else task_type
        }

    keys = load_env_keys()
    cascade = list(TASK_CASCADES.get(task_type) or TASK_CASCADES["general_chat"])

    # 0B. Optional AI-Agent-Orchestrator HTTP Bridge with Auto-Failover
    orch_enabled, orch_base = get_orchestrator_config()
    should_try_orchestrator = (force_provider and force_provider.lower() in ("orchestrator", "orchestrator_bridge")) or (
        orch_enabled and (not force_provider or force_provider.lower() == "auto")
    )
    if should_try_orchestrator:
        try:
            v1_base = orch_base if orch_base.endswith("/v1") else f"{orch_base}/v1"
            chat_url = f"{v1_base}/chat/completions"
            orch_msgs = []
            if system_instruction:
                orch_msgs.append({"role": "system", "content": system_instruction})
            orch_msgs.append({"role": "user", "content": prompt})
            payload = {
                "model": "auto-failover-orchestrator",
                "messages": orch_msgs,
                "temperature": temperature,
                "stream": False,
            }
            import urllib.request
            req_data = json.dumps(payload).encode("utf-8")
            orch_req = urllib.request.Request(
                chat_url,
                data=req_data,
                headers={"Content-Type": "application/json", "User-Agent": "Vega/2.2"}
            )
            with urllib.request.urlopen(orch_req, timeout=12.0) as o_resp:
                if o_resp.status == 200:
                    data = json.loads(o_resp.read().decode("utf-8"))
                    choices = data.get("choices", [])
                    out_text = choices[0].get("message", {}).get("content", "") if choices else ""
                    if out_text:
                        return {
                            "content": out_text,
                            "text": out_text,
                            "provider": "orchestrator",
                            "model": "auto-failover-orchestrator",
                            "badge": "Orchestrator",
                            "status": "success",
                            "fallback_occurred": False,
                            "attempts": [{"provider": "orchestrator", "model": "auto-failover-orchestrator", "status": "success"}],
                            "error": None,
                            "task_type": task_type,
                        }
        except Exception as exc:
            logger.info(f"Orchestrator bridge check/call failed ({exc}); falling back to native router.")
            if force_provider and force_provider.lower() in ("orchestrator", "orchestrator_bridge"):
                force_provider = "auto"

    # 1. Low-Data / Metered / Offline Route Adjustment
    restricted, net_mode = is_offline_or_low_data()
    if restricted and not (force_provider and force_provider != "auto"):
        local_model = os.environ.get("OLLAMA_MODEL") or os.environ.get("LOCAL_MODEL") or "qwen2.5:3b"
        ollama_entry = {"provider": "ollama", "model": local_model, "badge": "Local / Low-Data"}
        if net_mode == "offline_only":
            cascade = [ollama_entry]
        else:
            cascade = [ollama_entry] + [step for step in cascade if step.get("provider") != "ollama"]

    # 2. Token-Ceiling & Multimodal Vision Guard
    # If images are provided OR estimated prompt tokens > 5,500, enforce vision-capable models and skip text-only models
    tokens_est = estimate_tokens(prompt)
    has_images = bool(images and len(images) > 0)
    if has_images:
        task_type = "ocr_vision"
        # Strictly force multimodal vision models: Gemini primary, followed by vision-capable fallbacks
        cascade = [
            {"provider": "gemini", "model": "gemini-3.8-flash", "fallback_models": ["gemini-3.5-flash-lite", "gemini-2.5-flash"]},
            {"provider": "nvidia", "model": "nvidia/nemotron-3.5-super-vl-120b-a12b"},
            {"provider": "openrouter", "model": "google/gemini-2.0-flash-exp:free"},
        ]
        if force_provider and force_provider.lower() == "groq":
            force_provider = "gemini"  # Groq lacks multimodal vision support
    elif tokens_est > 5500:
        cascade = [step for step in cascade if step.get("provider") != "groq"]

    # 3. Force Provider Override (if user manually pinned a provider)
    if force_provider and force_provider != "auto":
        fp = force_provider.lower()
        forced = [s for s in cascade if s.get("provider") == fp]
        others = [s for s in cascade if s.get("provider") != fp]
        if forced:
            cascade = forced + others
        else:
            cascade = [{"provider": fp, "model": "default"}] + others

    # Format messages
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    attempts = []
    fallback_occurred = False

    for idx, candidate in enumerate(cascade):
        prov = candidate.get("provider")
        model = candidate.get("model")
        fallback_models = candidate.get("fallback_models") or []
        all_models = [model] + [m for m in fallback_models if m != model]

        # Check key requirement
        key = keys.get(prov, "")
        if prov != "ollama" and not key:
            attempts.append({
                "provider": prov,
                "model": model,
                "status": "skipped",
                "error": f"API key for {prov} is missing"
            })
            fallback_occurred = True
            continue

        # Try models for this provider
        provider_success = False
        for mod in all_models:
            attempt_record = {"provider": prov, "model": mod, "status": "running"}

            if prov == "groq":
                res = _call_openai_compat(
                    base_url="https://api.groq.com/openai/v1",
                    api_key=key,
                    model=mod,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
            elif prov == "nvidia":
                res = _call_openai_compat(
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key=key,
                    model=mod,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
            elif prov == "openrouter":
                res = _call_openai_compat(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=key,
                    model=mod,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_headers={"HTTP-Referer": "http://localhost:47632", "X-Title": "Vega"}
                )
            elif prov == "gemini":
                g_res = chat_gemini(
                    messages=messages,
                    api_key=key,
                    model=mod,
                    temperature=temperature,
                    system_instruction=system_instruction,
                    images=images
                )
                if g_res.get("status") == "success":
                    res = {"status": "success", "content": g_res.get("content", ""), "status_code": 200}
                else:
                    res = {"status": "error", "error": g_res.get("error", "Gemini error"), "status_code": g_res.get("status_code", 500)}
            elif prov == "ollama":
                # Local Ollama fallback
                try:
                    payload = {"model": mod, "messages": messages, "stream": False}
                    o_res = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=35)
                    if o_res.status_code == 200:
                        res = {"status": "success", "content": o_res.json().get("message", {}).get("content", ""), "status_code": 200}
                    else:
                        res = {"status": "error", "error": f"Ollama HTTP {o_res.status_code}", "status_code": o_res.status_code}
                except Exception as exc:
                    res = {"status": "error", "error": f"Ollama connection error: {exc}", "status_code": 503}
            else:
                res = {"status": "error", "error": f"Unknown provider {prov}", "status_code": 400}

            # Check outcome
            if res.get("status") == "success" and res.get("content"):
                attempt_record["status"] = "success"
                attempts.append(attempt_record)
                badge = "Cloud"
                if prov == "ollama":
                    badge = "Local / Low-Data" if restricted else "Local"
                elif restricted:
                    badge = "Cloud Override"
                return {
                    "content": res.get("content", ""),
                    "text": res.get("content", ""),
                    "provider": prov,
                    "model": mod,
                    "badge": badge,
                    "status": "success",
                    "fallback_occurred": fallback_occurred or (idx > 0),
                    "attempts": attempts,
                    "error": None,
                    "task_type": task_type
                }
            else:
                attempt_record["status"] = "failed"
                attempt_record["error"] = res.get("error")
                attempts.append(attempt_record)
                fallback_occurred = True

    return {
        "content": "",
        "text": "",
        "provider": "none",
        "model": "none",
        "badge": "Failed",
        "status": "error",
        "fallback_occurred": True,
        "attempts": attempts,
        "error": "All 5 brains failed or lacked valid credentials",
        "task_type": task_type
    }


def chat_get(
    prompt: Any,
    task_type: str = "general_chat",
    images: Optional[List[Any]] = None,
    force_provider: Optional[str] = None,
    system_instruction: Optional[str] = None,
    **kwargs: Any
) -> Dict[str, Any]:
    """
    Direct routing helper that accepts prompt (str or list of messages),
    optional images for multimodal vision models, and forwards to generate_for_task().
    """
    if isinstance(prompt, list):
        text_prompt = ""
        for m in reversed(prompt):
            if isinstance(m, dict) and m.get("role") in ("user", "human"):
                text_prompt = _coerce_text(m.get("content", ""))
                break
        if not text_prompt and prompt:
            first = prompt[-1]
            text_prompt = _coerce_text(first.get("content", "") if isinstance(first, dict) else first)
    else:
        text_prompt = _coerce_text(prompt)

    # Inject compact GitHub portfolio summary when user asks about projects or repos
    if text_prompt and any(k in text_prompt.lower() for k in ("project", "repo", "github", "portfolio")):
        try:
            from career.github_sync import get_portfolio_summary
            summary = _coerce_text(get_portfolio_summary())
            if summary and "[PORTFOLIO CONTEXT]" not in (system_instruction or ""):
                system_instruction = f"{system_instruction or ''}\n\n[PORTFOLIO CONTEXT]\n{summary}".strip()
        except Exception:
            pass

    return generate_for_task(
        prompt=text_prompt,
        task_type=task_type,
        images=images,
        force_provider=force_provider,
        system_instruction=system_instruction,
        max_tokens=kwargs.get("max_tokens", 4096),
        temperature=kwargs.get("temperature", 0.2)
    )


class LLMEngine:
    """Orchestrates model inference, routing, and tool calling."""

    def __init__(self, ollama_host: str = OLLAMA_BASE):
        self.ollama_host = ollama_host

    def check_vram_for_ollama(self, min_gb: float = 1.2) -> Dict[str, Any]:
        """Pre-flight check: ensures free VRAM >= min_gb before executing local Ollama inference."""
        return check_vram_preflight(min_gb)

    def chat_get(
        self,
        prompt: Any,
        task_type: str = "general_chat",
        images: Optional[List[Any]] = None,
        force_provider: Optional[str] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Method wrapper for central chat_get."""
        return chat_get(
            prompt=prompt,
            task_type=task_type,
            images=images,
            force_provider=force_provider,
            system_instruction=system_instruction,
            **kwargs
        )

    def generate_for_task(
        self,
        prompt: str,
        task_type: str = "general_chat",
        images: Optional[List[Any]] = None,
        force_provider: Optional[str] = None,
        system_instruction: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2
    ) -> Dict[str, Any]:
        """Method wrapper for central 5-brain auto-router."""
        return generate_for_task(
            prompt=prompt,
            task_type=task_type,
            images=images,
            force_provider=force_provider,
            system_instruction=system_instruction,
            max_tokens=max_tokens,
            temperature=temperature
        )

    def chat_gemini(
        self,
        messages: List[Dict[str, Any]],
        api_key: Optional[str] = None,
        model: str = GEMINI_MODEL,
        temperature: float = 0.2,
        system_instruction: Optional[str] = None
    ) -> Dict[str, Any]:
        """Method wrapper for central chat_gemini with HTTP 429 exponential backoff."""
        return chat_gemini(
            messages=messages,
            api_key=api_key,
            model=model,
            temperature=temperature,
            system_instruction=system_instruction
        )

    def chat_direct_ollama(
        self,
        messages: List[Dict[str, Any]],
        model: str = "qwen2.5:3b",
        stream: bool = False
    ) -> Dict[str, Any]:
        """Direct fast Ollama chat routing bypassing tool/agent/ReAct loops."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream
        }
        try:
            res = requests.post(f"{self.ollama_host}/api/chat", json=payload, timeout=45)
            if not res.ok:
                return {"error": f"Ollama error {res.status_code}: {res.text}"}
            data = res.json()
            msg = data.get("message", {})
            return {
                "role": "assistant",
                "content": msg.get("content", ""),
                "tool_calls": None
            }
        except Exception as exc:
            return {"error": f"Ollama direct chat exception: {exc}"}

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        model: str = "qwen2.5:7b",
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Sends chat turn to Ollama with registered tool schemas.
        If the model requests tool calls, executes them and feeds results back.
        """
        tools = ToolRegistry.sanitize_tools_schema(ToolRegistry.get_openai_tools())
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False
        }

        try:
            res = requests.post(f"{self.ollama_host}/api/chat", json=payload, timeout=45)
            if not res.ok:
                return {"error": f"Ollama error {res.status_code}: {res.text}"}

            data = res.json()
            msg = data.get("message", {})
            tool_calls = msg.get("tool_calls")

            if not tool_calls:
                return {
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": None
                }

            executed_tools = []
            updated_messages = list(messages)
            updated_messages.append(msg)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                if not isinstance(name, str) or not ToolRegistry.get_tool(name):
                    print(f"[QA System] Trapped orphaned tool call: {name}")
                    tool_result = {
                        "status": "error",
                        "error": f"System error: The tool '{name}' is unavailable or deprecated in this architecture."
                    }
                else:
                    tool_result = ToolRegistry.execute_tool(name, args)

                executed_tools.append({"name": name, "args": args, "result": tool_result})

                updated_messages.append({
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })

            followup_payload = {
                "model": model,
                "messages": updated_messages,
                "stream": False
            }
            followup_res = requests.post(f"{self.ollama_host}/api/chat", json=followup_payload, timeout=45)
            if followup_res.ok:
                followup_data = followup_res.json()
                return {
                    "role": "assistant",
                    "content": followup_data.get("message", {}).get("content", ""),
                    "tool_executions": executed_tools
                }
            else:
                return {
                    "role": "assistant",
                    "content": f"Executed tools: {json.dumps(executed_tools, ensure_ascii=False)}",
                    "tool_executions": executed_tools
                }

        except Exception as exc:
            return {"error": f"Inference exception: {exc}"}


engine = LLMEngine()
