"""
vibe-local memory — long-term memory, improvement history, lessons learned
"""

import json
import os
import time
from datetime import datetime


class Memory:
    """Persistent memory for agent lessons, improvements, and project context."""

    def __init__(self, state_dir=None):
        if state_dir is None:
            state_dir = os.path.join(
                os.path.expanduser("~"), ".local", "state", "vibe-local"
            )
        self.state_dir = state_dir
        self.lessons_file = os.path.join(state_dir, "lessons.json")
        self.improvements_file = os.path.join(state_dir, "improvements.json")
        self.project_file = os.path.join(state_dir, "project_context.json")
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        self.lessons = self._load(self.lessons_file, [])
        self.improvements = self._load(self.improvements_file, [])
        self.project_context = self._load(self.project_file, {})

    def _load(self, path, default):
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def _save(self, path, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add_lesson(self, category, lesson, severity="info"):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "category": category,
            "lesson": lesson,
            "severity": severity,
        }
        self.lessons.append(entry)
        if len(self.lessons) > 500:
            self.lessons = self.lessons[-500:]
        self._save(self.lessons_file, self.lessons)
        return entry

    def add_improvement(
        self,
        file_path,
        change_type,
        description,
        before_metrics=None,
        after_metrics=None,
    ):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "file": file_path,
            "change_type": change_type,
            "description": description,
            "before_metrics": before_metrics or {},
            "after_metrics": after_metrics or {},
        }
        self.improvements.append(entry)
        if len(self.improvements) > 1000:
            self.improvements = self.improvements[-1000:]
        self._save(self.improvements_file, self.improvements)
        return entry

    def set_project_context(self, key, value):
        self.project_context[key] = value
        self._save(self.project_file, self.project_context)

    def get_project_context(self, key, default=None):
        return self.project_context.get(key, default)

    def get_lessons_by_category(self, category):
        return [l for l in self.lessons if l["category"] == category]

    def get_recent_lessons(self, n=10):
        return self.lessons[-n:]

    def get_recent_improvements(self, n=10):
        return self.improvements[-n:]

    def build_context_prompt(self):
        parts = []
        parts.append("# Project Context")
        for k, v in self.project_context.items():
            parts.append(f"- {k}: {v}")

        recent = self.get_recent_lessons(5)
        if recent:
            parts.append("\n# Recent Lessons")
            for l in recent:
                parts.append(f"- [{l['severity']}] {l['lesson']}")

        recent_imp = self.get_recent_improvements(5)
        if recent_imp:
            parts.append("\n# Recent Improvements")
            for imp in recent_imp:
                parts.append(f"- {imp['change_type']}: {imp['description']}")

        return "\n".join(parts)

    def stats(self):
        return {
            "lessons": len(self.lessons),
            "improvements": len(self.improvements),
            "project_keys": list(self.project_context.keys()),
        }
