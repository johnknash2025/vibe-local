"""
vibe-local core tools — Bash, Read, Write, Edit, Glob, Grep, WebFetch, WebSearch
"""

import json
import os
import re
import subprocess
import tempfile
import shutil
import fnmatch
import urllib.request
import urllib.error
import urllib.parse
import threading
import difflib
import base64
from pathlib import Path
from abc import ABC, abstractmethod

MAX_OUTPUT = 30000
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_WRITE_SIZE = 10 * 1024 * 1024

DANGEROUS_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"mkfs", re.IGNORECASE),
    re.compile(r"\bdd\b", re.IGNORECASE),
    re.compile(r"curl.*\|\s*(ba)?sh", re.IGNORECASE),
    re.compile(r"wget.*\|\s*(ba)?sh", re.IGNORECASE),
    re.compile(r"eval\s+.*base64", re.IGNORECASE),
]

PROTECTED_PATHS = {
    "/etc",
    "/usr",
    "/System",
    "/Library",
    os.path.expanduser("~/.config"),
    os.path.expanduser("~/.local"),
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    "dist",
    "build",
    ".DS_Store",
}


class Tool(ABC):
    name = ""
    description = ""
    parameters = {}

    def get_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    @abstractmethod
    def execute(self, params):
        pass


class BashTool(Tool):
    name = "Bash"
    description = (
        "Execute a shell command. Quote URLs with single quotes. Never use sudo."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to execute"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in milliseconds (default: 120000)",
            },
        },
        "required": ["command"],
    }

    def execute(self, params):
        cmd = params.get("command", "")
        timeout_ms = params.get("timeout", 120000)

        for pat in DANGEROUS_PATTERNS:
            if pat.search(cmd):
                return f"DANGEROUS command blocked: {cmd}"

        env = os.environ.copy()
        for k in list(env.keys()):
            if any(
                s in k.upper()
                for s in ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]
            ):
                env.pop(k, None)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000,
                env=env,
                cwd=os.getcwd(),
            )
            out = result.stdout
            if result.stderr:
                out += result.stderr
            if not out:
                out = "(empty output)"
            return out[:MAX_OUTPUT]
        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout_ms}ms"
        except Exception as e:
            return f"Error: {e}"


class ReadTool(Tool):
    name = "Read"
    description = "Read a file. Returns line-numbered content. Supports text, images (base64), PDFs."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "offset": {
                "type": "integer",
                "description": "Start line (1-based, default: 1)",
            },
            "limit": {
                "type": "integer",
                "description": "Max lines to read (default: 2000)",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, params):
        path = params.get("file_path", "")
        offset = params.get("offset", 1)
        limit = params.get("limit", 2000)

        if os.path.islink(path):
            return f"Error: symlinks not allowed: {path}"
        if not os.path.isfile(path):
            return f"Error: file not found: {path}"

        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_SIZE:
                return f"Error: file too large ({size} bytes)"
        except OSError:
            pass

        ext = os.path.splitext(path)[1].lower()

        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"):
            try:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                media = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                    "gif": "image/gif",
                    "webp": "image/webp",
                    "bmp": "image/bmp",
                    "svg": "image/svg+xml",
                }.get(ext.lstrip("."), "application/octet-stream")
                return json.dumps(
                    {"type": "image", "media_type": media, "data": data[:10000]}
                )
            except Exception as e:
                return f"Error reading image: {e}"

        if ext == ".pdf":
            return self._read_pdf(path)

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = []
                for i, line in enumerate(f, 1):
                    if i < offset:
                        continue
                    if len(lines) >= limit:
                        lines.append(f"... ({limit} lines shown)")
                        break
                    lines.append(f"{i}: {line.rstrip()}")
                if not lines:
                    return f"(empty file, {size} bytes)"
                return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    def _read_pdf(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read(500000)
            text = ""
            for m in re.finditer(b"/([A-Za-z0-9]+) Tj", data):
                t = m.group(1).decode("ascii", errors="replace")
                text += (
                    t.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
                    + " "
                )
            for m in re.finditer(rb"stream\s*\n(.*?)\nendstream", data, re.DOTALL):
                chunk = m.group(1)
                for m2 in re.finditer(rb"\(([^\)]+)\)", chunk):
                    text += m2.group(1).decode("ascii", errors="replace") + " "
            return text[:MAX_OUTPUT] or "(could not extract PDF text)"
        except Exception as e:
            return f"Error reading PDF: {e}"


class WriteTool(Tool):
    name = "Write"
    description = "Create or overwrite a file. ALWAYS use absolute paths."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to write to"},
            "content": {"type": "string", "description": "File content"},
        },
        "required": ["file_path", "content"],
    }

    def __init__(self, undo_stack=None):
        self.undo_stack = undo_stack or []

    def execute(self, params):
        path = params.get("file_path", "")
        content = params.get("content", "")

        if len(content) > MAX_WRITE_SIZE:
            return (
                f"Error: content too large ({len(content)} bytes, max {MAX_WRITE_SIZE})"
            )

        if os.path.islink(path):
            return f"Error: symlinks not allowed: {path}"

        abs_path = os.path.abspath(path)
        for pp in PROTECTED_PATHS:
            if abs_path.startswith(pp):
                return f"Error: protected path: {abs_path}"

        try:
            parent = os.path.dirname(abs_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            if os.path.exists(abs_path):
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        old = f.read()
                    self.undo_stack.append({"path": abs_path, "content": old})
                    if len(self.undo_stack) > 20:
                        self.undo_stack.pop(0)
                except Exception:
                    pass

            fd, tmp = tempfile.mkstemp(dir=parent or ".")
            try:
                os.write(fd, content.encode("utf-8"))
                os.close(fd)
                os.replace(tmp, abs_path)
            except Exception:
                os.close(fd) if not os.get_inheritable(fd) else None
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

            lines = content.count("\n") + 1
            return f"Wrote {len(content)} bytes ({lines} lines) to {abs_path}"
        except Exception as e:
            return f"Error writing file: {e}"


class EditTool(Tool):
    name = "Edit"
    description = "Edit an existing file by replacing old_string with new_string."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Absolute path to the file"},
            "old_string": {"type": "string", "description": "Text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default: false)",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def __init__(self, undo_stack=None):
        self.undo_stack = undo_stack or []

    def execute(self, params):
        path = params.get("file_path", "")
        old = params.get("old_string", "")
        new = params.get("new_string", "")
        replace_all = params.get("replace_all", False)

        if os.path.islink(path):
            return f"Error: symlinks not allowed: {path}"
        if not os.path.isfile(path):
            return f"Error: file not found: {path}"

        try:
            size = os.path.getsize(path)
            if size > MAX_FILE_SIZE:
                return f"Error: file too large ({size} bytes)"
        except OSError:
            pass

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            self.undo_stack.append({"path": path, "content": content})
            if len(self.undo_stack) > 20:
                self.undo_stack.pop(0)

            if old not in content:
                norm_old = old.encode("utf-8").decode("utf-8", errors="replace")
                import unicodedata

                norm_content_text = unicodedata.normalize("NFC", content)
                norm_old_nfc = unicodedata.normalize("NFC", old)
                if norm_old_nfc in norm_content_text:
                    content = norm_content_text
                    old = norm_old_nfc
                else:
                    return f"Error: old_string not found in {path}"

            if replace_all:
                new_content = content.replace(old, new)
            else:
                new_content = content.replace(old, new, 1)

            diff = self._make_diff(content, new_content)

            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".")
            try:
                os.write(fd, new_content.encode("utf-8"))
                os.close(fd)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.close(fd)
                except Exception:
                    pass
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

            return f"Edited {path}\n{diff}"
        except Exception as e:
            return f"Error editing file: {e}"

    def _make_diff(self, old, new):
        old_lines = old.splitlines()
        new_lines = new.splitlines()
        diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
        return "\n".join(diff[:30])


class GlobTool(Tool):
    name = "Glob"
    description = "Find files matching a glob pattern. Use ** for recursive search."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py)"},
            "path": {
                "type": "string",
                "description": "Base directory (default: current)",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, params):
        pattern = params.get("pattern", "")
        base = params.get("path", os.getcwd())

        if not os.path.isdir(base):
            return f"Error: directory not found: {base}"

        results = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for f in files:
                if fnmatch.fnmatch(f, pattern) or fnmatch.fnmatch(
                    os.path.join(root, f), f"**/{pattern}"
                ):
                    full = os.path.join(root, f)
                    try:
                        mt = os.path.getmtime(full)
                        results.append((full, mt))
                    except OSError:
                        pass
                elif "**" in pattern:
                    if fnmatch.fnmatch(f, pattern.split("/")[-1]):
                        full = os.path.join(root, f)
                        try:
                            mt = os.path.getmtime(full)
                            results.append((full, mt))
                        except OSError:
                            pass

            if len(results) >= 200:
                break

        results.sort(key=lambda x: x[1], reverse=True)
        if not results:
            return f"No files matching '{pattern}' in {base}"
        lines = [f"{path}" for path, _ in results[:200]]
        if len(results) > 200:
            lines.append(f"... and {len(results) - 200} more")
        return "\n".join(lines)


class GrepTool(Tool):
    name = "Grep"
    description = "Search file contents with regex. Use -i for case insensitive."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {
                "type": "string",
                "description": "Directory to search (default: current)",
            },
            "glob": {
                "type": "string",
                "description": "File pattern filter (e.g. *.py)",
            },
            "-i": {"type": "boolean", "description": "Case insensitive"},
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": "Output mode",
            },
            "head_limit": {
                "type": "integer",
                "description": "Max results (default: 50)",
            },
        },
        "required": ["pattern"],
    }

    def execute(self, params):
        pattern = params.get("pattern", "")
        search_path = params.get("path", os.getcwd())
        file_glob = params.get("glob", "*")
        case_insensitive = params.get("-i", False)
        output_mode = params.get("output_mode", "content")
        head_limit = params.get("head_limit", 50)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            re.compile(pattern, flags)
        except re.error as e:
            return f"Invalid regex: {e}"

        results = []
        count = 0

        for root, dirs, files in os.walk(search_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                if not fnmatch.fnmatch(fname, file_glob):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getsize(fpath) > MAX_FILE_SIZE:
                        continue
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if re.search(pattern, line, flags):
                                count += 1
                                if output_mode == "files_with_matches":
                                    results.append(fpath)
                                    break
                                elif output_mode == "count":
                                    pass
                                else:
                                    results.append(f"{fpath}:{i}: {line.rstrip()}")
                                if (
                                    len(results) >= head_limit
                                    and output_mode != "count"
                                ):
                                    break
                except Exception:
                    pass
                if len(results) >= head_limit and output_mode != "count":
                    break

        if output_mode == "count":
            return f"{count} matches"
        if output_mode == "files_with_matches":
            return "\n".join(results[:head_limit]) or "No matches"
        return "\n".join(results[:head_limit]) or "No matches"


class WebFetchTool(Tool):
    name = "WebFetch"
    description = "Fetch content from a URL. Only http/https allowed."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "prompt": {"type": "string", "description": "What to look for (optional)"},
        },
        "required": ["url"],
    }

    def execute(self, params):
        url = params.get("url", "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Error: only http/https allowed, got: {parsed.scheme}"

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                },
            )
            resp = urllib.request.urlopen(req, timeout=30)
            html = resp.read(5 * 1024 * 1024).decode("utf-8", errors="replace")
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:50000] or "(empty page)"
        except Exception as e:
            return f"Error fetching URL: {e}"


class WebSearchTool(Tool):
    name = "WebSearch"
    description = "Search the web using DuckDuckGo."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    }

    _lock = threading.Lock()
    _last_search = 0
    _search_count = 0

    def execute(self, params):
        query = params.get("query", "")
        with self._lock:
            now = time.time()
            if now - self._last_search < 2:
                time.sleep(2 - (now - self._last_search))
            self._last_search = time.time()
            self._search_count += 1
            if self._search_count > 50:
                return "Error: search rate limit exceeded (50/session)"

        search_url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        try:
            req = urllib.request.Request(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                },
            )
            resp = urllib.request.urlopen(req, timeout=15)
            html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return f"Search failed: {e}"

        results = []
        link_pat = re.compile(
            r'<a\s+[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL
        )
        snippet_pat = re.compile(
            r'<a\s+[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL
        )
        links = link_pat.findall(html)
        snippets = snippet_pat.findall(html)

        for i, (raw_url, raw_title) in enumerate(links[:10]):
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            if not title:
                continue
            url = raw_url
            if "uddg=" in url:
                m = re.search(r"uddg=([^&]+)", url)
                if m:
                    url = urllib.parse.unquote(m.group(1))
            elif url.startswith("//"):
                url = "https:" + url
            if "/y.js?" in url or "ad_provider" in url:
                continue
            snippet = (
                re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
            )
            results.append(f"- {title}\n  URL: {url}\n  {snippet}")

        return "\n\n".join(results) or f"No results for: {query}"


def register_default_tools(registry, undo_stack=None):
    """Register all default tools to the given registry."""
    undo = undo_stack or []
    registry.register(BashTool())
    registry.register(ReadTool())
    registry.register(WriteTool(undo_stack=undo))
    registry.register(EditTool(undo_stack=undo))
    registry.register(GlobTool())
    registry.register(GrepTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    return undo
