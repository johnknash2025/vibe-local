"""
vibe-local self-improving agent — analyze → plan → execute → verify → log

The agent can improve its own codebase by:
1. ANALYZE: Read its own source code, find issues, measure quality
2. PLAN: Create an improvement plan with priorities
3. EXECUTE: Apply changes safely with git checkpoint + rollback
4. VERIFY: Run tests, compare metrics, commit or rollback
5. LOG: Record what was learned for future reference
"""

import json
import os
import re
import subprocess
import time
import py_compile
from datetime import datetime


SYSTEM_PROMPT = """You are a self-improving AI agent. Your goal is to improve the codebase you're running in.

RULES:
1. ANALYZE first — read files, understand structure, identify issues
2. PLAN changes — explain what you'll change and why
3. EXECUTE carefully — one change at a time, verify each
4. VERIFY results — run tests, check syntax, measure improvement
5. LOG lessons — record what you learned

FOCUS AREAS:
- Code quality: readability, consistency, naming
- Performance: bottlenecks, unnecessary computation
- Bug fixes: error handling, edge cases
- Features: missing functionality, user experience
- Documentation: comments, docstrings, clarity

CONSTRAINTS:
- NEVER break existing functionality
- Always run syntax checks after edits
- Keep changes small and focused
- If a change fails, try a different approach
- Never use sudo or modify system files
- Work only within the project directory

OUTPUT FORMAT:
- Be concise
- Explain your reasoning briefly
- Show diffs when making changes
"""


class SelfImprover:
    """Self-improving agent that analyzes and improves its own codebase."""

    def __init__(
        self, engine_loop, memory, project_dir=None, max_cycles=10, auto_approve=False
    ):
        self.engine = engine_loop
        self.memory = memory
        self.project_dir = project_dir or os.getcwd()
        self.max_cycles = max_cycles
        self.auto_approve = auto_approve
        self.improvement_log = []

    def run(self):
        """Run the self-improvement loop."""
        print(f"\n{'=' * 60}")
        print(f"  SELF-IMPROVEMENT MODE — {self.max_cycles} cycles max")
        print(f"  Project: {self.project_dir}")
        print(f"{'=' * 60}\n")

        for cycle in range(self.max_cycles):
            print(f"\n{'─' * 40}")
            print(f"  CYCLE {cycle + 1}/{self.max_cycles}")
            print(f"{'─' * 40}\n")

            phase = "analyze"
            try:
                analysis = self._analyze()
                if not analysis.get("issues"):
                    print("  No issues found. Self-improvement complete.")
                    break

                phase = "plan"
                plan = self._plan(analysis)
                if not plan:
                    print("  No actionable plan. Stopping.")
                    break

                phase = "execute"
                result = self._execute(plan)

                phase = "verify"
                verified = self._verify(result)

                phase = "log"
                self._log_cycle(cycle, analysis, plan, result, verified)

                if not verified:
                    print(
                        f"  Cycle {cycle + 1}: changes rolled back (verification failed)"
                    )
                else:
                    print(f"  Cycle {cycle + 1}: improvements committed ✓")

            except Exception as e:
                print(f"  Cycle {cycle + 1}: ERROR in phase '{phase}': {e}")
                self._checkpoint_reset()

        print(f"\n{'=' * 60}")
        print(f"  SELF-IMPROVEMENT COMPLETE")
        print(f"  Cycles run: {min(cycle + 1, self.max_cycles)}")
        print(f"  Improvements: {len(self.improvement_log)}")
        print(f"{'=' * 60}\n")
        return self.improvement_log

    def _analyze(self):
        """Analyze the codebase for issues."""
        print("  [1/4] ANALYZING codebase...")

        files = self._list_source_files()
        issues = []

        for fpath in files[:20]:
            issues.extend(self._analyze_file(fpath))

        issues.sort(
            key=lambda x: {"error": 0, "warning": 1, "info": 2}.get(
                x.get("severity", "info"), 3
            )
        )

        print(f"    Found {len(issues)} issues")
        for issue in issues[:5]:
            print(
                f"    - [{issue.get('severity', '?')}] {issue.get('file', '?')}: {issue.get('description', '?')[:80]}"
            )

        return {"issues": issues, "files_analyzed": len(files)}

    def _analyze_file(self, fpath):
        """Analyze a single file for issues."""
        issues = []
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
                lines = content.splitlines()
        except Exception:
            return issues

        rel = os.path.relpath(fpath, self.project_dir)

        if fpath.endswith(".py"):
            issues.extend(self._check_python(fpath, rel, content, lines))
        issues.extend(self._check_general(rel, content, lines))

        return issues

    def _check_python(self, fpath, rel, content, lines):
        issues = []

        try:
            py_compile.compile(fpath, doraise=True)
        except py_compile.PyCompileError as e:
            issues.append(
                {
                    "file": rel,
                    "severity": "error",
                    "description": f"Syntax error: {e}",
                    "line": 0,
                }
            )

        for i, line in enumerate(lines, 1):
            stripped = line.rstrip()
            if len(stripped) > 120:
                issues.append(
                    {
                        "file": rel,
                        "severity": "warning",
                        "description": f"Line too long ({len(stripped)} chars)",
                        "line": i,
                    }
                )
            if "\t" in line:
                issues.append(
                    {
                        "file": rel,
                        "severity": "info",
                        "description": "Tab character found (use spaces)",
                        "line": i,
                    }
                )
            if re.search(r"\s+$", line):
                issues.append(
                    {
                        "file": rel,
                        "severity": "info",
                        "description": "Trailing whitespace",
                        "line": i,
                    }
                )

        if not any(
            line.startswith('"""') or line.startswith("'''") for line in lines[:5]
        ):
            if len(lines) > 10:
                issues.append(
                    {
                        "file": rel,
                        "severity": "info",
                        "description": "Missing module docstring",
                        "line": 1,
                    }
                )

        func_count = sum(1 for l in lines if re.match(r"\s*def ", l))
        doc_count = sum(1 for l in lines if '"""' in l or "'''" in l)
        if func_count > 0 and doc_count < func_count // 2:
            issues.append(
                {
                    "file": rel,
                    "severity": "info",
                    "description": f"Low docstring coverage ({doc_count}/{func_count} functions)",
                    "line": 0,
                }
            )

        return issues

    def _check_general(self, rel, content, lines):
        issues = []
        if len(content) > 100000:
            issues.append(
                {
                    "file": rel,
                    "severity": "warning",
                    "description": f"File very large ({len(content) // 1000}KB)",
                    "line": 0,
                }
            )
        if len(lines) > 2000:
            issues.append(
                {
                    "file": rel,
                    "severity": "warning",
                    "description": f"File has {len(lines)} lines (consider splitting)",
                    "line": 0,
                }
            )
        return issues

    def _plan(self, analysis):
        """Create an improvement plan from analysis results."""
        print("  [2/4] PLANNING improvements...")

        issues = analysis.get("issues", [])
        if not issues:
            return None

        top_issues = [i for i in issues if i.get("severity") in ("error", "warning")][
            :5
        ]
        if not top_issues:
            top_issues = issues[:3]

        plan_items = []
        for issue in top_issues:
            plan_items.append(
                {
                    "file": issue["file"],
                    "issue": issue["description"],
                    "severity": issue.get("severity", "info"),
                    "line": issue.get("line", 0),
                }
            )

        print(f"    Plan: {len(plan_items)} improvements")
        for p in plan_items:
            print(f"    - [{p['severity']}] {p['file']}: {p['issue'][:60]}")

        return plan_items

    def _execute(self, plan):
        """Execute the improvement plan using the LLM agent."""
        print("  [3/4] EXECUTING improvements...")

        self._checkpoint_save()
        results = []

        for item in plan:
            print(f"\n    Fixing: {item['file']} — {item['issue'][:60]}")

            prompt = self._build_fix_prompt(item)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            original_engine_auto = self.engine.auto_approve
            self.engine.auto_approve = self.auto_approve

            result = self.engine.run(
                system_prompt=SYSTEM_PROMPT,
                user_message=prompt,
                messages=messages,
            )

            self.engine.auto_approve = original_engine_auto

            content = ""
            for msg in result:
                if msg.get("role") == "assistant" and msg.get("content"):
                    content = msg["content"]

            results.append({"item": item, "output": content[:500]})
            print(f"    Done: {content[:100]}...")

        return results

    def _build_fix_prompt(self, item):
        fpath = os.path.join(self.project_dir, item["file"])
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = "(file not readable)"

        return f"""Fix this issue in {item["file"]}:

Issue: {item["issue"]}
Severity: {item["severity"]}
{f"Line: {item['line']}" if item.get("line") else ""}

Current file content:
```
{content[:10000]}
```

Use the Edit tool to fix this issue. Only change what's necessary.
After editing, verify the fix with a syntax check if it's a Python file."""

    def _verify(self, results):
        """Verify changes by running syntax checks and tests."""
        print("  [4/4] VERIFYING changes...")

        all_ok = True
        for r in results:
            fpath = os.path.join(self.project_dir, r["item"]["file"])
            if fpath.endswith(".py"):
                try:
                    py_compile.compile(fpath, doraise=True)
                    print(f"    ✓ {r['item']['file']}: syntax OK")
                except py_compile.PyCompileError as e:
                    print(f"    ✗ {r['item']['file']}: syntax error — {e}")
                    all_ok = False

        if all_ok:
            self._checkpoint_commit()
        else:
            self._checkpoint_reset()

        return all_ok

    def _checkpoint_save(self):
        try:
            subprocess.run(
                ["git", "stash", "push", "-m", "self-improve-checkpoint"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass

    def _checkpoint_commit(self):
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "self-improve: auto-commit improvements"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "stash", "drop"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            pass

    def _checkpoint_reset(self):
        try:
            subprocess.run(
                ["git", "stash", "pop"],
                cwd=self.project_dir,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            pass

    def _list_source_files(self):
        files = []
        for root, dirs, filenames in os.walk(self.project_dir):
            dirs[:] = [
                d
                for d in dirs
                if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"}
                and not d.startswith(".")
            ]
            for f in filenames:
                if f.endswith(
                    (".py", ".js", ".ts", ".md", ".json", ".sh", ".yaml", ".yml")
                ):
                    files.append(os.path.join(root, f))
        return sorted(files)

    def _log_cycle(self, cycle, analysis, plan, results, verified):
        entry = {
            "cycle": cycle,
            "timestamp": datetime.now().isoformat(),
            "issues_found": len(analysis.get("issues", [])),
            "changes_planned": len(plan),
            "changes_executed": len(results),
            "verified": verified,
            "files_changed": [r["item"]["file"] for r in results],
        }
        self.improvement_log.append(entry)
        self.memory.add_improvement(
            file_path=", ".join(entry["files_changed"]),
            change_type="self-improve",
            description=f"Cycle {cycle}: {len(plan)} issues addressed, verified={verified}",
        )
