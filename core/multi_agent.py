"""
vibe-local multi-agent coordination — role-based parallel agent execution

Supports:
- Coordinator agent: decomposes tasks and assigns to workers
- Worker agents: specialized roles (coder, reviewer, tester, researcher)
- Parallel execution with result aggregation
"""

import json
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


WORKER_ROLES = {
    "coder": {
        "description": "Writes and edits code. Focus on correctness and clarity.",
        "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        "system_prompt": "You are a coding agent. Write clean, correct code. Use absolute paths. Always verify your changes.",
    },
    "reviewer": {
        "description": "Reviews code for bugs, style, and best practices.",
        "tools": ["Read", "Glob", "Grep"],
        "system_prompt": "You are a code reviewer. Analyze code for bugs, style issues, and improvements. Read-only — do NOT modify files.",
    },
    "tester": {
        "description": "Writes and runs tests. Validates functionality.",
        "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
        "system_prompt": "You are a testing agent. Write tests, run them, and report results. Fix failing tests if possible.",
    },
    "researcher": {
        "description": "Researches solutions and gathers information.",
        "tools": ["Read", "Glob", "Grep", "WebFetch", "WebSearch"],
        "system_prompt": "You are a research agent. Gather information, search for solutions, and provide recommendations. Read-only.",
    },
}


class MultiAgentCoordinator:
    """Coordinates multiple worker agents for parallel task execution."""

    def __init__(self, engine_factory, memory=None, max_workers=4):
        self.engine_factory = engine_factory
        self.memory = memory
        self.max_workers = min(max_workers, len(WORKER_ROLES))
        self._results = {}
        self._lock = threading.Lock()

    def decompose_task(self, user_request):
        """Use LLM to decompose a task into subtasks for different roles."""
        prompt = f"""Decompose this task into subtasks for different agent roles.

User request: {user_request}

Available roles: {", ".join(WORKER_ROLES.keys())}

Respond with JSON array only:
[
  {{"role": "coder", "task": "implement the feature"}},
  {{"role": "tester", "task": "write tests for the feature"}}
]

Rules:
- Assign 1-4 subtasks
- Each subtask must have a role and task description
- Order matters: research before coding, testing after coding
"""
        engine, client, registry = self.engine_factory()
        result, err = client.chat_sync(
            messages=[
                {
                    "role": "system",
                    "content": "You are a task planner. Respond with JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        if err:
            return self._default_decompose(user_request)

        try:
            content = result.get("content", "")
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                subtasks = json.loads(content[start:end])
                return [
                    s
                    for s in subtasks
                    if s.get("role") in WORKER_ROLES and s.get("task")
                ]
        except Exception:
            pass

        return self._default_decompose(user_request)

    def _default_decompose(self, user_request):
        lower = user_request.lower()
        subtasks = []
        if any(
            w in lower
            for w in ["search", "research", "find", "look up", "調べ", "調査"]
        ):
            subtasks.append({"role": "researcher", "task": f"Research: {user_request}"})
        subtasks.append({"role": "coder", "task": f"Implement: {user_request}"})
        subtasks.append({"role": "tester", "task": f"Test the implementation"})
        return subtasks[: self.max_workers]

    def execute(self, subtasks, system_context=""):
        """Execute subtasks with appropriate worker agents."""
        print(f"\n{'=' * 60}")
        print(f"  MULTI-AGENT EXECUTION — {len(subtasks)} subtasks")
        print(f"{'=' * 60}\n")

        results = {}
        completed = []

        for i, subtask in enumerate(subtasks):
            role = subtask["role"]
            task = subtask["task"]
            role_config = WORKER_ROLES.get(role, WORKER_ROLES["coder"])

            print(f"  [{i + 1}/{len(subtasks)}] {role}: {task[:80]}")

            engine, client, registry = self.engine_factory()
            system_prompt = role_config["system_prompt"]
            if system_context:
                system_prompt += f"\n\nContext: {system_context}"

            prev_results = ""
            for c in completed:
                prev_results += f"\n[{c['role']} result]: {c['output'][:200]}"

            user_msg = task
            if prev_results:
                user_msg += f"\n\nPrevious results:{prev_results}"

            messages = [{"role": "system", "content": system_prompt}]

            try:
                result = engine.run(
                    system_prompt=system_prompt,
                    user_message=user_msg,
                    messages=messages,
                )

                output = ""
                for msg in result:
                    if msg.get("role") == "assistant" and msg.get("content"):
                        output = msg["content"]

                completed.append({"role": role, "task": task, "output": output})
                results[role] = output[:2000]

                if self.memory:
                    self.memory.add_lesson(
                        category="multi-agent",
                        lesson=f"{role} completed: {task[:100]}",
                        severity="info",
                    )

            except Exception as e:
                print(f"    ERROR: {e}")
                completed.append({"role": role, "task": task, "output": f"Error: {e}"})
                results[role] = f"Error: {e}"

        return self._summarize(completed)

    def execute_parallel(self, subtasks, system_context=""):
        """Execute independent subtasks in parallel threads."""
        print(f"\n{'=' * 60}")
        print(f"  PARALLEL EXECUTION — {len(subtasks)} tasks")
        print(f"{'=' * 60}\n")

        results = {}
        lock = threading.Lock()

        def run_task(idx, subtask):
            role = subtask["role"]
            task = subtask["task"]
            role_config = WORKER_ROLES.get(role, WORKER_ROLES["coder"])

            engine, client, registry = self.engine_factory()
            messages = [{"role": "system", "content": role_config["system_prompt"]}]

            try:
                result = engine.run(
                    system_prompt=role_config["system_prompt"],
                    user_message=task,
                    messages=messages,
                )
                output = ""
                for msg in result:
                    if msg.get("role") == "assistant" and msg.get("content"):
                        output = msg["content"]
                with lock:
                    results[idx] = {"role": role, "task": task, "output": output}
                    print(f"  ✓ {role}: done")
            except Exception as e:
                with lock:
                    results[idx] = {"role": role, "task": task, "output": f"Error: {e}"}
                    print(f"  ✗ {role}: {e}")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for i, st in enumerate(subtasks):
                futures.append(executor.submit(run_task, i, st))
            for f in as_completed(futures):
                f.result()

        ordered = [results[i] for i in range(len(subtasks)) if i in results]
        return self._summarize(ordered)

    def _summarize(self, completed):
        summary_parts = []
        for c in completed:
            summary_parts.append(
                f"## {c['role']}\n**Task:** {c['task']}\n\n{c['output'][:500]}"
            )
        return "\n\n---\n\n".join(summary_parts)
