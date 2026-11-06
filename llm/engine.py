"""
LLM Engine and Autonomous Tool-Calling Execution Loop for Vega 2.0.
Interacts with local Ollama, Google Gemini, and OpenAI-compatible providers,
executing function calls with automatic multi-turn feedback.
"""

import json
import requests
from typing import List, Dict, Any, Optional
from tools.registry import ToolRegistry

OLLAMA_BASE = "http://127.0.0.1:11434"


class LLMEngine:
    """Orchestrates model inference, routing, and tool calling."""

    def __init__(self, ollama_host: str = OLLAMA_BASE):
        self.ollama_host = ollama_host

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
        tools = ToolRegistry.get_openai_tools()
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

            # If no tool calls requested, return assistant response directly
            if not tool_calls:
                return {
                    "role": "assistant",
                    "content": msg.get("content", ""),
                    "tool_calls": None
                }

            # Execute tool calls
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

                tool_result = ToolRegistry.execute_tool(name, args)
                executed_tools.append({"name": name, "args": args, "result": tool_result})

                # Append tool response
                updated_messages.append({
                    "role": "tool",
                    "name": name,
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })

            # Send back to LLM for final natural reply
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

