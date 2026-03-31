#!/usr/bin/env python3
"""
vibe-local — Self-improving local AI agent system
Optimized for Apple M1 Max / 64GB RAM

Usage:
    python3 main.py                          # interactive mode
    python3 main.py -p "task description"    # one-shot
    python3 main.py --self-improve           # self-improvement mode
    python3 main.py --multi-agent "task"     # multi-agent mode
    python3 main.py --model qwen3:30b        # specify model
    python3 main.py -y                       # auto-approve
"""

import argparse
import json
import os
import sys
import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import OllamaClient, AgentLoop, ToolRegistry, Colors
from core.tools import register_default_tools
from core.memory import Memory
from core.self_improve import SelfImprover
from core.multi_agent import MultiAgentCoordinator


def parse_config(config_path):
    config = {}
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        config[k.strip()] = v.strip().strip("\"'")
        except Exception:
            pass
    return config


def create_engine(
    model,
    sidecar_model,
    host,
    temperature,
    max_tokens,
    context_window,
    auto_approve=False,
):
    client = OllamaClient(
        host=host,
        model=model,
        sidecar_model=sidecar_model,
        temperature=temperature,
        max_tokens=max_tokens,
        context_window=context_window,
    )
    registry = ToolRegistry()
    undo_stack = register_default_tools(registry)
    loop = AgentLoop(
        client=client,
        registry=registry,
        auto_approve=auto_approve,
    )
    return loop, client, registry


def engine_factory(
    model,
    sidecar_model,
    host,
    temperature,
    max_tokens,
    context_window,
    auto_approve=False,
):
    def _factory():
        return create_engine(
            model,
            sidecar_model,
            host,
            temperature,
            max_tokens,
            context_window,
            auto_approve,
        )

    return _factory


SYSTEM_PROMPT = """You are a helpful coding assistant running locally on a Mac.

ENVIRONMENT:
- OS: macOS (Apple Silicon M1 Max)
- Home directory: /Users/keigofukumoto (NEVER /home/user)
- Shell: zsh
- Package manager: brew (NEVER apt/yum)
- Working directory: /Users/keigofukumoto/dev/vibe-local

CORE RULES:
1. Use tools immediately — no explanation before tool calls
2. After tool results: give a clear, concise summary (2-3 sentences)
3. NEVER end with a question — just finish and wait
4. ALWAYS use absolute paths starting with /Users/ (NEVER /home/)
5. Install dependencies BEFORE running code
6. Reply in the same language as the user
7. Quote URLs with single quotes in Bash commands
8. NEVER use sudo
9. NEVER fabricate results — if something fails, say so honestly
10. For file operations, use the current working directory or /tmp/

Available tools: Bash, Read, Write, Edit, Glob, Grep, WebFetch, WebSearch
"""


def main():
    parser = argparse.ArgumentParser(description="vibe-local — self-improving AI agent")
    parser.add_argument("-p", "--prompt", help="One-shot prompt")
    parser.add_argument("-m", "--model", help="Ollama model name")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Auto-approve all tool calls"
    )
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    parser.add_argument(
        "--self-improve", action="store_true", help="Self-improvement mode"
    )
    parser.add_argument(
        "--self-improve-cycles", type=int, default=5, help="Max self-improve cycles"
    )
    parser.add_argument("--multi-agent", help="Multi-agent mode with task description")
    parser.add_argument(
        "--parallel", action="store_true", help="Run multi-agent tasks in parallel"
    )
    parser.add_argument("--ollama-host", help="Ollama host URL")
    parser.add_argument("--max-tokens", type=int, help="Max output tokens")
    parser.add_argument("--temperature", type=float, help="Sampling temperature")
    parser.add_argument("--context-window", type=int, help="Context window size")
    args = parser.parse_args()

    config_path = os.path.join(
        os.path.expanduser("~"), ".config", "vibe-local", "config"
    )
    config = parse_config(config_path)

    model = args.model or config.get("MODEL", "qwen3:30b")
    sidecar_model = config.get("SIDECAR_MODEL", None)
    host = args.ollama_host or config.get("OLLAMA_HOST", "http://localhost:11434")
    temperature = args.temperature or float(config.get("TEMPERATURE", "0.7"))
    max_tokens = args.max_tokens or int(config.get("MAX_TOKENS", "8192"))
    context_window = args.context_window or int(config.get("CONTEXT_WINDOW", "32768"))
    auto_approve = args.yes

    memory = Memory()

    print(f"\n{Colors.BOLD}vibe-local{Colors.RESET} — self-improving AI agent")
    print(
        f"  Model: {model}" + (f" (sidecar: {sidecar_model})" if sidecar_model else "")
    )
    print(f"  Host: {host}")
    print(f"  Auto-approve: {auto_approve}")
    print(
        f"  Memory: {memory.stats()['lessons']} lessons, {memory.stats()['improvements']} improvements\n"
    )

    if args.self_improve:
        engine, client, registry = create_engine(
            model,
            sidecar_model,
            host,
            temperature,
            max_tokens,
            context_window,
            auto_approve=True,
        )
        improver = SelfImprover(
            engine_loop=engine,
            memory=memory,
            project_dir=os.path.dirname(os.path.abspath(__file__)),
            max_cycles=args.self_improve_cycles,
            auto_approve=True,
        )
        improver.run()
        return

    if args.multi_agent:
        engine, client, registry = create_engine(
            model,
            sidecar_model,
            host,
            temperature,
            max_tokens,
            context_window,
            auto_approve,
        )
        factory = engine_factory(
            model,
            sidecar_model,
            host,
            temperature,
            max_tokens,
            context_window,
            auto_approve,
        )
        coordinator = MultiAgentCoordinator(
            engine_factory=factory,
            memory=memory,
            max_workers=4,
        )
        subtasks = coordinator.decompose_task(args.multi_agent)
        print(f"\n  Decomposed into {len(subtasks)} subtasks:")
        for st in subtasks:
            print(f"    - {st['role']}: {st['task'][:80]}")

        if args.parallel:
            result = coordinator.execute_parallel(subtasks)
        else:
            result = coordinator.execute(
                subtasks, system_context=memory.build_context_prompt()
            )

        print(f"\n{'=' * 60}")
        print(f"  RESULTS")
        print(f"{'=' * 60}\n")
        print(result)
        return

    if args.prompt:
        engine, client, registry = create_engine(
            model,
            sidecar_model,
            host,
            temperature,
            max_tokens,
            context_window,
            auto_approve,
        )
        ctx = memory.build_context_prompt()
        prompt = SYSTEM_PROMPT
        if ctx:
            prompt += f"\n\n{ctx}"
        engine.run(system_prompt=prompt, user_message=args.prompt)
        return

    print(
        f"  {Colors.DIM}Interactive mode — type /help for commands, Ctrl+C to exit{Colors.RESET}\n"
    )
    engine, client, registry = create_engine(
        model,
        sidecar_model,
        host,
        temperature,
        max_tokens,
        context_window,
        auto_approve,
    )

    history = []

    def handle_sigint(sig, frame):
        print(f"\n{Colors.YELLOW}Interrupted. Type 'exit' to quit.{Colors.RESET}")
        engine.stop()

    signal.signal(signal.SIGINT, handle_sigint)

    while True:
        try:
            user_input = input(f"\n{Colors.GREEN}▸ {Colors.RESET}")
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.YELLOW}Bye!{Colors.RESET}")
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print(f"\n{Colors.YELLOW}Bye!{Colors.RESET}")
            break

        if user_input == "/help":
            print(f"""
Commands:
  /help          — Show this help
  /exit, /quit   — Exit
  /status        — Show current status
  /memory        — Show memory stats
  /self-improve  — Run self-improvement ({args.self_improve_cycles} cycles)
  /multi-agent   — Run multi-agent mode (next input is the task)
  /clear         — Clear history
  /model         — Show current model
""")
            continue

        if user_input == "/status":
            print(f"  Model: {model}")
            print(f"  Host: {host}")
            print(f"  History: {len(history)} messages")
            continue

        if user_input == "/memory":
            stats = memory.stats()
            print(f"  Lessons: {stats['lessons']}")
            print(f"  Improvements: {stats['improvements']}")
            print(f"  Context keys: {stats['project_keys']}")
            continue

        if user_input == "/model":
            print(
                f"  Model: {model}"
                + (f" (sidecar: {sidecar_model})" if sidecar_model else "")
            )
            continue

        if user_input == "/self-improve":
            improver = SelfImprover(
                engine_loop=engine,
                memory=memory,
                project_dir=os.path.dirname(os.path.abspath(__file__)),
                max_cycles=args.self_improve_cycles,
                auto_approve=True,
            )
            improver.run()
            continue

        if user_input == "/multi-agent":
            print(f"  {Colors.DIM}Enter task for multi-agent mode:{Colors.RESET}")
            try:
                task = input(f"\n{Colors.GREEN}▸ {Colors.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not task:
                continue
            factory = engine_factory(
                model,
                sidecar_model,
                host,
                temperature,
                max_tokens,
                context_window,
                auto_approve,
            )
            coordinator = MultiAgentCoordinator(
                engine_factory=factory, memory=memory, max_workers=4
            )
            subtasks = coordinator.decompose_task(task)
            print(f"\n  Decomposed into {len(subtasks)} subtasks:")
            for st in subtasks:
                print(f"    - {st['role']}: {st['task'][:80]}")
            result = coordinator.execute(
                subtasks, system_context=memory.build_context_prompt()
            )
            print(f"\n{'=' * 60}\n{result}\n{'=' * 60}\n")
            continue

        if user_input == "/clear":
            history = []
            print(f"  {Colors.DIM}History cleared{Colors.RESET}")
            continue

        ctx = memory.build_context_prompt()
        prompt = SYSTEM_PROMPT
        if ctx:
            prompt += f"\n\n{ctx}"

        messages = [{"role": "system", "content": prompt}] + history
        result = engine.run(
            system_prompt=prompt, user_message=user_input, messages=messages
        )

        history = result[-10:]

        last_content = ""
        for msg in reversed(result):
            if msg.get("role") == "assistant" and msg.get("content"):
                last_content = msg["content"]
                break

        if last_content:
            memory.set_project_context("last_task", user_input[:100])
            memory.set_project_context("last_response_summary", last_content[:200])


if __name__ == "__main__":
    main()
