"""
vibe-local core engine — LLM communication + tool execution loop
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    GRAY = "\033[90m"

    _enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    @classmethod
    def disable(cls):
        for attr in dir(cls):
            if (
                attr.isupper()
                and isinstance(getattr(cls, attr), str)
                and attr != "_enabled"
            ):
                setattr(cls, attr, "")
        cls._enabled = False


def _log(label, msg, color=Colors.CYAN):
    ts = time.strftime("%H:%M:%S")
    print(
        f"{color}{Colors.DIM}[{ts}]{Colors.RESET} {color}{label}:{Colors.RESET} {msg}"
    )


class OllamaClient:
    """Ollama API client (OpenAI-compatible /v1/chat/completions)."""

    def __init__(
        self,
        host="http://localhost:11434",
        model="qwen3:30b",
        temperature=0.7,
        max_tokens=8192,
        context_window=32768,
        sidecar_model=None,
        debug=False,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.sidecar_model = sidecar_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.context_window = context_window
        self.debug = debug
        self._lock = threading.Lock()

    def chat(self, messages, tools=None, stream=False, use_sidecar=False):
        model = (
            self.sidecar_model if (use_sidecar and self.sidecar_model) else self.model
        )
        body = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        if self.debug:
            _log(
                "REQ",
                f"{model} msgs={len(messages)} tools={len(tools) if tools else 0}",
                Colors.DIM,
            )

        try:
            req = urllib.request.Request(
                f"{self.host}/v1/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=300)
            data = json.loads(resp.read())
            return self._parse_response(data)
        except Exception as e:
            _log("ERR", str(e), Colors.RED)
            return None, str(e)

    def chat_sync(self, messages, tools=None, use_sidecar=False):
        return self.chat(messages, tools=tools, stream=False, use_sidecar=use_sidecar)

    def _parse_response(self, data):
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "") or ""
        reasoning = message.get("reasoning", "") or ""
        tool_calls = message.get("tool_calls", [])
        finish_reason = choice.get("finish_reason", "end_turn")
        usage = data.get("usage", {})
        return {
            "content": content,
            "reasoning": reasoning,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
        }, None


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self._cached_schemas = None

    def register(self, tool):
        self._tools[tool.name] = tool
        self._cached_schemas = None

    def get(self, name):
        return self._tools.get(name)

    def get_schemas(self):
        if self._cached_schemas is None:
            self._cached_schemas = [t.get_schema() for t in self._tools.values()]
        return self._cached_schemas

    def get_names(self):
        return list(self._tools.keys())


class AgentLoop:
    """Core agent loop: LLM → tool calls → execute → loop."""

    MAX_ITERATIONS = 50

    def __init__(
        self,
        client,
        registry,
        permissions=None,
        on_tool_result=None,
        on_iteration=None,
        max_iterations=None,
        auto_approve=False,
    ):
        self.client = client
        self.registry = registry
        self.permissions = permissions or set()
        self.on_tool_result = on_tool_result
        self.on_iteration = on_iteration
        self.max_iterations = max_iterations or self.MAX_ITERATIONS
        self.auto_approve = auto_approve
        self._stopped = threading.Event()

    def run(self, system_prompt, user_message, messages=None):
        if messages is None:
            messages = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": user_message})

        tools = self.registry.get_schemas()
        last_tool_calls = []

        for iteration in range(self.max_iterations):
            if self._stopped.is_set():
                _log("STOP", "Agent stopped by user", Colors.YELLOW)
                break

            if self.on_iteration:
                self.on_iteration(iteration, messages)

            _log("LLM", f"iteration {iteration + 1}/{self.max_iterations}", Colors.BLUE)

            result, err = self.client.chat_sync(messages, tools=tools)
            if err:
                _log("ERR", f"LLM call failed: {err}", Colors.RED)
                messages.append({"role": "assistant", "content": f"Error: {err}"})
                continue

            content = result["content"]
            reasoning = result["reasoning"]
            tool_calls = result["tool_calls"]
            finish_reason = result["finish_reason"]

            if reasoning:
                _log("REASON", reasoning[:200], Colors.DIM)
            if content:
                print(f"\n{content[:500]}{'...' if len(content) > 500 else ''}\n")

            messages.append(
                {"role": "assistant", "content": content, "tool_calls": tool_calls}
            )

            if not tool_calls:
                _log("DONE", f"finish={finish_reason}", Colors.GREEN)
                break

            last_tool_calls = tool_calls

            for tc in tool_calls:
                if self._stopped.is_set():
                    break
                self._execute_tool_call(tc, messages)

        return messages

    def _execute_tool_call(self, tc, messages):
        tc_id = tc.get("id", "")
        func = tc.get("function", {})
        name = func.get("name", "")
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError:
            args = {}

        tool = self.registry.get(name)
        if not tool:
            result_text = f"Unknown tool: {name}"
            _log("TOOL", result_text, Colors.RED)
        else:
            _log(
                "TOOL",
                f"{name}({json.dumps(args, ensure_ascii=False)[:100]})",
                Colors.MAGENTA,
            )
            try:
                result_text = tool.execute(args)
                _log("OK", f"{name} completed ({len(result_text)} bytes)", Colors.GREEN)
            except Exception as e:
                result_text = f"Error: {e}"
                _log("ERR", result_text, Colors.RED)

        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_text[:50000],
            }
        )

        if self.on_tool_result:
            self.on_tool_result(name, result_text)

    def stop(self):
        self._stopped.set()
