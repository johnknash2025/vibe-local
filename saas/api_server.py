"""
vibe-local SaaS — API server (stdlib only, no dependencies)
HTTP API with authentication, rate limiting, billing tracking
"""

import json
import os
import sys
import time
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saas.database import Database
from saas.services.registry import SERVICE_REGISTRY, BaseService
from core.engine import OllamaClient


class RateLimiter:
    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = {}
        self._lock = threading.Lock()

    def allow(self, key):
        with self._lock:
            now = time.time()
            if key not in self._requests:
                self._requests[key] = []
            self._requests[key] = [
                t for t in self._requests[key] if now - t < self.window
            ]
            if len(self._requests[key]) >= self.max_requests:
                return False
            self._requests[key].append(now)
            return True


class APIHandler(BaseHTTPRequestHandler):
    db: Database = None
    client: OllamaClient = None
    rate_limiter: RateLimiter = None
    services: dict = {}
    queue: list = []
    queue_lock = threading.Lock()

    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message, status=400):
        self._send_json({"error": message}, status)

    def _authenticate(self):
        api_key = self.headers.get("X-API-Key", "")
        if not api_key:
            return None
        user = self.db.get_user_by_key(api_key)
        if not user:
            self._send_error("Invalid API key", 401)
            return None
        if not self.rate_limiter.allow(user["api_key"]):
            self._send_error("Rate limit exceeded", 429)
            return None
        return user

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/services":
            self._send_json({"services": self.db.get_services()})

        elif path == "/api/stats":
            user = self._authenticate()
            if not user:
                return
            self._send_json(self.db.get_stats())

        elif path == "/api/users":
            user = self._authenticate()
            if not user:
                return
            self._send_json({"users": self.db.get_users()})

        elif path == "/api/requests":
            user = self._authenticate()
            if not user:
                return
            self._send_json({"requests": self.db.get_recent_requests()})

        elif path == "/api/me":
            user = self._authenticate()
            if not user:
                return
            self._send_json(user)

        elif path == "/" or path == "":
            self._serve_dashboard()

        else:
            self._send_error("Not found", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/users":
            body = self._read_body()
            name = body.get("name")
            if not name:
                self._send_error("name is required")
                return
            user = self.db.create_user(
                name=name,
                email=body.get("email"),
                plan=body.get("plan", "free"),
                credits=body.get("credits", 100),
            )
            self._send_json(user, 201)

        elif path == "/api/credits":
            user = self._authenticate()
            if not user:
                return
            body = self._read_body()
            amount = body.get("amount", 0)
            if amount > 0:
                self.db.add_credits(user["id"], amount, body.get("description", ""))
                self._send_json(
                    {"status": "ok", "new_credits": user["credits"] + amount}
                )
            else:
                self._send_error("amount must be positive")

        elif path.startswith("/api/v1/"):
            service_slug = path.replace("/api/v1/", "")
            if service_slug not in SERVICE_REGISTRY:
                self._send_error(f"Unknown service: {service_slug}", 404)
                return

            user = self._authenticate()
            if not user:
                return

            body = self._read_body()
            user_input = body.get("input", body.get("prompt", body.get("text", "")))
            if not user_input:
                self._send_error("input/prompt/text is required")
                return

            service_cost = 1
            for svc in self.db.get_services():
                if svc["slug"] == service_slug:
                    service_cost = svc.get("cost_per_request", 1)
                    break

            if user["credits"] < service_cost:
                self._send_error("Insufficient credits", 402)
                return

            req_id = self.db.log_request(user["id"], service_slug, user_input)

            self.queue.append(
                {
                    "req_id": req_id,
                    "user_id": user["id"],
                    "service_slug": service_slug,
                    "user_input": user_input,
                    "cost": service_cost,
                }
            )

            self._send_json(
                {
                    "request_id": req_id,
                    "status": "queued",
                    "message": "Processing... check status with GET /api/requests",
                },
                202,
            )

        else:
            self._send_error("Not found", 404)

    def _serve_dashboard(self):
        stats = self.db.get_stats()
        html = f"""<!DOCTYPE html>
<html>
<head><title>vibe-local SaaS Dashboard</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #0a0a0a; color: #e0e0e0; }}
h1 {{ color: #00ff88; }}
.card {{ background: #1a1a1a; border-radius: 8px; padding: 20px; margin: 10px 0; }}
.stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }}
.stat {{ text-align: center; }}
.stat .num {{ font-size: 2em; color: #00ff88; }}
.stat .label {{ color: #888; font-size: 0.9em; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
th {{ color: #00ff88; }}
.api-key {{ background: #222; padding: 10px; border-radius: 4px; font-family: monospace; word-break: break-all; }}
</style></head>
<body>
<h1>⚡ vibe-local SaaS</h1>
<p>Local AI Service Platform — Powered by qwen3:30b on Apple M1 Max</p>

<div class="card">
<h2>Live Stats</h2>
<div class="stats">
<div class="stat"><div class="num">{stats["total_users"]}</div><div class="label">Users</div></div>
<div class="stat"><div class="num">{stats["total_requests"]}</div><div class="label">Total Requests</div></div>
<div class="stat"><div class="num">{stats["today_requests"]}</div><div class="label">Today</div></div>
<div class="stat"><div class="num">{stats["completed_requests"]}</div><div class="label">Completed</div></div>
<div class="stat"><div class="num">{stats["error_requests"]}</div><div class="label">Errors</div></div>
<div class="stat"><div class="num">{stats["total_credits_remaining"]}</div><div class="label">Credits Left</div></div>
</div>
</div>

<div class="card">
<h2>Available Services</h2>
<table><tr><th>Service</th><th>Cost</th><th>Description</th></tr>
"""
        for svc in self.db.get_services():
            html += f"<tr><td>{svc['slug']}</td><td>{svc['cost_per_request']} credits</td><td>{svc['description']}</td></tr>\n"
        html += """</table></div>

<div class="card">
<h2>Quick Start</h2>
<div class="api-key">
# Create a user<br>
curl -X POST http://localhost:8787/api/users -H 'Content-Type: application/json' -d '{"name":"demo","credits":100}'<br><br>
# Generate content<br>
curl -X POST http://localhost:8787/api/v1/content-gen -H 'X-API-Key: YOUR_KEY' -H 'Content-Type: application/json' -d '{"input":"Write about AI"}'<br><br>
# Check status<br>
curl http://localhost:8787/api/stats -H 'X-API-Key: YOUR_KEY'
</div>
</div>

<div class="card">
<h2>Recent Requests</h2>
<table><tr><th>User</th><th>Service</th><th>Status</th><th>Time</th></tr>
"""
        for req in self.db.get_recent_requests(10):
            html += f"<tr><td>{req.get('user_name', '?')}</td><td>{req['service_slug']}</td><td>{req['status']}</td><td>{req['created_at']}</td></tr>\n"
        html += """</table></div>

<script>setInterval(() => location.reload(), 10000);</script>
</body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def run_api_server(host="0.0.0.0", port=8787, db=None, client=None):
    db = db or Database()
    client = client or OllamaClient()
    rate_limiter = RateLimiter()

    APIHandler.db = db
    APIHandler.client = client
    APIHandler.rate_limiter = rate_limiter
    APIHandler.services = SERVICE_REGISTRY
    APIHandler.queue = []

    server = HTTPServer((host, port), APIHandler)
    print(f"\n  API Server: http://{host}:{port}")
    print(f"  Dashboard: http://localhost:{port}")
    print(f"  Database: {db.db_path}\n")

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, APIHandler.queue
