"""
vibe-local SaaS — worker that processes the request queue
Polls the queue, runs LLM inference, saves results, deducts credits
"""

import json
import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saas.database import Database
from saas.services.registry import SERVICE_REGISTRY
from core.engine import OllamaClient


class Worker:
    def __init__(self, db=None, client=None, poll_interval=1.0):
        self.db = db or Database()
        self.client = client or OllamaClient()
        self.poll_interval = poll_interval
        self._running = False
        self._thread = None
        self._processed = 0
        self._errors = 0

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"  Worker started (poll={self.poll_interval}s)")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        while self._running:
            try:
                pending = self.db.get_pending_requests(limit=1)
                if not pending:
                    time.sleep(self.poll_interval)
                    continue

                req = pending[0]
                self._process_request(req)
            except Exception as e:
                print(f"  Worker error: {e}")
                time.sleep(self.poll_interval)

    def _process_request(self, req):
        req_id = req["id"]
        user_id = req["user_id"]
        service_slug = req["service_slug"]
        user_input = json.loads(req["input_data"]) if req["input_data"] else ""

        if isinstance(user_input, dict):
            user_input = user_input.get(
                "input", user_input.get("prompt", user_input.get("text", ""))
            )

        print(f"  Processing #{req_id}: {service_slug} for user {user_id}")

        service_cls = SERVICE_REGISTRY.get(service_slug)
        if not service_cls:
            self.db.complete_request(req_id, error=f"Unknown service: {service_slug}")
            self._errors += 1
            return

        try:
            service = service_cls(self.client)
            result = service.process(user_input)

            if "error" in result:
                self.db.complete_request(req_id, error=result["error"])
                self._errors += 1
            else:
                tokens = result.pop("tokens", 0)
                self.db.complete_request(req_id, output_data=result, tokens_used=tokens)

                cost = 1
                for svc in self.db.get_services():
                    if svc["slug"] == service_slug:
                        cost = svc.get("cost_per_request", 1)
                        break
                self.db.deduct_credits(user_id, cost, f"{service_slug} #{req_id}")
                self._processed += 1

                print(f"  ✓ #{req_id} completed ({tokens} tokens, {cost} credits)")

        except Exception as e:
            self.db.complete_request(req_id, error=str(e))
            self._errors += 1
            print(f"  ✗ #{req_id} failed: {e}")

    def stats(self):
        return {"processed": self._processed, "errors": self._errors}
