"""
Unified Tool Registry and Safety Confirmation Gate for Vega 2.0.
Exports OpenAI / Ollama and Gemini compatible schemas.
Intercepts dangerous tools with mandatory user confirmation.
"""

import json
import uuid
from typing import Callable, Dict, Any, List, Optional

_REGISTRY: Dict[str, Dict[str, Any]] = {}
_PENDING_CONFIRMATIONS: Dict[str, Dict[str, Any]] = {}


class ToolRegistry:
    """Manages system tool definitions and execution."""

    @staticmethod
    def sanitize_tools_schema(tools):
        """Remove malformed or orphaned tool schemas without mutating live state."""
        if not isinstance(tools, list):
            return []

        valid_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                print("[SYSTEM-SEC] Stripped malformed tool schema: non-object entry")
                continue

            name = ""
            function = tool.get("function")
            if isinstance(function, dict):
                name = function.get("name", "")
            elif isinstance(tool, dict) and isinstance(tool.get("name"), str):
                name = tool.get("name", "")

            if not isinstance(name, str) or not name.strip():
                print(f"[SYSTEM-SEC] Stripped malformed tool from LLM context: {name!r}")
                continue

            name = name.strip()
            registered = _REGISTRY.get(name)
            if not registered or not callable(registered.get("func")):
                print(f"[SYSTEM-SEC] Stripped orphaned tool from LLM context: {name}")
                continue
            valid_tools.append(tool)
        return valid_tools

    @staticmethod
    def register(
        name: str,
        description: str,
        parameters: Dict[str, Any],
        dangerous: bool = False
    ):
        """Decorator to register a tool function."""
        def decorator(func: Callable):
            _REGISTRY[name] = {
                "name": name,
                "description": description,
                "parameters": parameters,
                "dangerous": dangerous,
                "func": func
            }
            return func
        return decorator

    @staticmethod
    def get_tool(name: str) -> Optional[Dict[str, Any]]:
        return _REGISTRY.get(name)

    @staticmethod
    def list_tools() -> List[Dict[str, Any]]:
        return list(_REGISTRY.values())

    @staticmethod
    def get_openai_tools() -> List[Dict[str, Any]]:
        """Returns schemas formatted for Ollama / OpenAI / Groq tool calling."""
        tools = []
        for t in _REGISTRY.values():
            if not callable(t.get("func")):
                print(f"[SYSTEM-SEC] Stripped orphaned tool from LLM context: {t.get('name', '')}")
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"]
                }
            })
        return tools

    @staticmethod
    def get_gemini_tools() -> List[Dict[str, Any]]:
        """Returns schemas formatted for Gemini API Function Calling."""
        declarations = []
        for t in _REGISTRY.values():
            declarations.append({
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"]
            })
        return [{"function_declarations": declarations}] if declarations else []

    @staticmethod
    def execute_tool(name: str, args: Dict[str, Any], confirmed: bool = False) -> Dict[str, Any]:
        """
        Executes a registered tool. If dangerous and not yet confirmed,
        returns a pending confirmation object.
        """
        if not isinstance(name, str) or name not in _REGISTRY:
            print(f"[QA System] Trapped orphaned tool call: {name}")
            return {
                "status": "error",
                "error": f"System error: The tool '{name}' is unavailable or deprecated in this architecture."
            }
        tool = _REGISTRY[name]
        func = tool.get("func")
        if not callable(func):
            print(f"[QA System] Trapped orphaned tool call: {name}")
            return {
                "status": "error",
                "error": f"System error: The tool '{name}' is unavailable or deprecated in this architecture."
            }

        if tool["dangerous"] and not confirmed:
            conf_id = str(uuid.uuid4())[:8]
            _PENDING_CONFIRMATIONS[conf_id] = {
                "id": conf_id,
                "name": name,
                "args": args,
                "description": tool["description"]
            }
            return {
                "status": "confirmation_required",
                "confirmation_id": conf_id,
                "message": f"Safety Confirmation Required: Are you sure you want to run '{name}' with {json.dumps(args)}? Reply 'yes' to proceed."
            }

        result = func(**args)
        return {"status": "success", "result": result}

    @staticmethod
    def confirm_action(confirmation_id: str, approved: bool = True) -> Dict[str, Any]:
        """Executes or cancels a paused dangerous tool call."""
        if confirmation_id not in _PENDING_CONFIRMATIONS:
            return {"status": "error", "error": "Invalid or expired confirmation ID."}

        action = _PENDING_CONFIRMATIONS.pop(confirmation_id)
        if not approved:
            return {"status": "cancelled", "message": f"Execution of '{action['name']}' was cancelled."}

        return ToolRegistry.execute_tool(action["name"], action["args"], confirmed=True)


tools = ToolRegistry()

