"""
vibe-local SaaS — Autonomous Operations Agent
Self-running ops agent that monitors, manages, and optimizes the SaaS platform.

Capabilities:
- Monitoring: server health, error rates, resource usage
- Auto-recovery: crash detection, restart, error handling
- Report generation: daily/weekly reports
- Customer support: auto-respond to inquiries
- Content ops: blog/SNS auto-posting
- SEO monitoring: rank tracking, improvement suggestions
- Billing: invoice generation, payment tracking
"""

import json
import os
import sys
import time
import subprocess
import threading
import urllib.request
import urllib.error
import socket
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.engine import OllamaClient, Colors
from saas.database import Database


# ─── Monitoring ────────────────────────────────────────────────


class Monitor:
    """Server health, error rates, resource usage monitoring."""

    def __init__(self, db: Database, api_url="http://localhost:8787"):
        self.db = db
        self.api_url = api_url
        self.alerts = []
        self._running = False

    def check_server_health(self):
        """Check if the API server is responding."""
        try:
            req = urllib.request.Request(f"{self.api_url}/api/services", method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            return {"status": "healthy", "services": len(data.get("services", []))}
        except Exception as e:
            return {"status": "down", "error": str(e)}

    def check_error_rate(self, window_minutes=60):
        """Check error rate in the last N minutes."""
        with self.db._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE created_at >= datetime('now', ?)",
                (f"-{window_minutes} minutes",),
            ).fetchone()[0]
            errors = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE status = 'error' AND created_at >= datetime('now', ?)",
                (f"-{window_minutes} minutes",),
            ).fetchone()[0]
            rate = (errors / total * 100) if total > 0 else 0
            return {"total": total, "errors": errors, "error_rate": round(rate, 1)}

    def check_resources(self):
        """Check Mac system resources."""
        try:
            mem = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True
            )
            mem_total = int(mem.stdout.strip()) / (1024**3)
            mem_used_cmd = subprocess.run(["vm_stat"], capture_output=True, text=True)
            return {
                "memory_total_gb": round(mem_total, 1),
                "disk_usage": self._get_disk_usage(),
                "ollama_running": self._check_ollama(),
            }
        except Exception as e:
            return {"error": str(e)}

    def _get_disk_usage(self):
        try:
            st = os.statvfs(os.path.expanduser("~"))
            total = st.f_blocks * st.f_frsize / (1024**3)
            free = st.f_bavail * st.f_frsize / (1024**3)
            return {
                "total_gb": round(total, 1),
                "free_gb": round(free, 1),
                "used_pct": round((1 - free / total) * 100, 1),
            }
        except Exception:
            return {}

    def _check_ollama(self):
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/tags", method="GET"
            )
            urllib.request.urlopen(req, timeout=3)
            return True
        except Exception:
            return False

    def check_queue_depth(self):
        """Check pending request queue depth."""
        pending = self.db.get_pending_requests(limit=1000)
        return {"pending": len(pending)}

    def run_all_checks(self):
        """Run all monitoring checks."""
        return {
            "timestamp": datetime.now().isoformat(),
            "server": self.check_server_health(),
            "error_rate": self.check_error_rate(),
            "resources": self.check_resources(),
            "queue": self.check_queue_depth(),
        }

    def evaluate_alerts(self, checks):
        """Evaluate if any alerts should be triggered."""
        alerts = []
        if checks["server"]["status"] == "down":
            alerts.append(
                {
                    "severity": "critical",
                    "message": "API server is DOWN",
                    "action": "restart_server",
                }
            )
        if checks["error_rate"]["error_rate"] > 20:
            alerts.append(
                {
                    "severity": "warning",
                    "message": f"High error rate: {checks['error_rate']['error_rate']}%",
                    "action": "investigate_errors",
                }
            )
        if checks["queue"]["pending"] > 50:
            alerts.append(
                {
                    "severity": "warning",
                    "message": f"Queue backlog: {checks['queue']['pending']} pending",
                    "action": "scale_worker",
                }
            )
        if checks["resources"].get("disk_usage", {}).get("used_pct", 0) > 90:
            alerts.append(
                {
                    "severity": "critical",
                    "message": "Disk usage > 90%",
                    "action": "cleanup_disk",
                }
            )
        if not checks["resources"].get("ollama_running"):
            alerts.append(
                {
                    "severity": "critical",
                    "message": "Ollama is not running",
                    "action": "restart_ollama",
                }
            )
        self.alerts.extend(alerts)
        return alerts


# ─── Auto-Recovery ─────────────────────────────────────────────


class AutoRecovery:
    """Automatic recovery from failures."""

    def __init__(self, db: Database, api_url="http://localhost:8787"):
        self.db = db
        self.api_url = api_url
        self.recovery_log = []

    def handle_alert(self, alert):
        """Handle an alert with automatic recovery."""
        action = alert.get("action", "")
        message = alert.get("message", "")

        if action == "restart_server":
            return self._restart_server()
        elif action == "restart_ollama":
            return self._restart_ollama()
        elif action == "investigate_errors":
            return self._investigate_errors()
        elif action == "cleanup_disk":
            return self._cleanup_disk()
        elif action == "scale_worker":
            return {"status": "info", "message": "Queue backlog — worker is processing"}

        return {"status": "unknown", "action": action}

    def _restart_server(self):
        """Restart the API server process."""
        self.recovery_log.append(
            {"action": "restart_server", "time": datetime.now().isoformat()}
        )
        return {
            "status": "action_needed",
            "message": "Server restart required — managed by process supervisor",
        }

    def _restart_ollama(self):
        """Restart Ollama."""
        try:
            subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
            time.sleep(2)
            subprocess.Popen(["ollama", "serve"], start_new_session=True)
            time.sleep(3)
            self.recovery_log.append(
                {"action": "restart_ollama", "time": datetime.now().isoformat()}
            )
            return {"status": "recovered", "message": "Ollama restarted"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    def _investigate_errors(self):
        """Analyze recent errors."""
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT service_slug, error, COUNT(*) as cnt FROM requests WHERE status = 'error' AND created_at >= datetime('now', '-1 hour') GROUP BY service_slug, error ORDER BY cnt DESC LIMIT 5"
            ).fetchall()
            errors = [
                {"service": r["service_slug"], "error": r["error"], "count": r["cnt"]}
                for r in rows
            ]
        return {"status": "analyzed", "errors": errors}

    def _cleanup_disk(self):
        """Clean up old data."""
        try:
            with self.db._connect() as conn:
                conn.execute(
                    "DELETE FROM system_metrics WHERE recorded_at < datetime('now', '-7 days')"
                )
                conn.execute(
                    "DELETE FROM requests WHERE created_at < datetime('now', '-30 days') AND status = 'completed'"
                )
                conn.commit()
            self.recovery_log.append(
                {"action": "cleanup_disk", "time": datetime.now().isoformat()}
            )
            return {
                "status": "cleaned",
                "message": "Old metrics and completed requests purged",
            }
        except Exception as e:
            return {"status": "failed", "error": str(e)}


# ─── Report Generator ──────────────────────────────────────────


class ReportGenerator:
    """Generate daily/weekly operational reports."""

    def __init__(self, db: Database, client: OllamaClient):
        self.db = db
        self.client = client

    def generate_daily_report(self):
        """Generate today's operational report."""
        stats = self.db.get_stats()
        recent = self.db.get_recent_requests(20)

        report = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "stats": stats,
            "top_services": self._get_top_services(),
            "recent_errors": [r for r in recent if r.get("status") == "error"][:5],
        }

        ai_summary = self._ai_summarize_report(report)
        report["ai_summary"] = ai_summary

        self._save_report(report)
        return report

    def _get_top_services(self):
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT service_slug, COUNT(*) as cnt, SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as ok FROM requests GROUP BY service_slug ORDER BY cnt DESC"
            ).fetchall()
            return [
                {"service": r["service_slug"], "requests": r["cnt"], "success": r["ok"]}
                for r in rows
            ]

    def _ai_summarize_report(self, report):
        data = json.dumps(report, ensure_ascii=False)[:3000]
        messages = [
            {
                "role": "system",
                "content": "You are an ops analyst. Summarize this SaaS daily report in 3-5 bullet points. Focus on key metrics, issues, and recommendations. Respond in Japanese.",
            },
            {"role": "user", "content": f"Summarize this report:\n{data}"},
        ]
        result, err = self.client.chat_sync(messages, use_sidecar=True)
        if err:
            return f"Stats: {report['stats']}"
        return result.get("content", "")

    def _save_report(self, report):
        report_dir = os.path.join(
            os.path.expanduser("~"), ".local", "state", "vibe-local", "reports"
        )
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, f"report-{report['date']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)


# ─── Customer Support Agent ────────────────────────────────────


class CustomerSupportAgent:
    """Auto-respond to customer inquiries."""

    def __init__(self, db: Database, client: OllamaClient):
        self.db = db
        self.client = client
        self.response_log = []

    def process_inquiry(self, inquiry, user_context=None):
        """Process a customer inquiry and generate a response."""
        faq_matches = self.db.search_faq("my-product", inquiry)

        if faq_matches:
            return {
                "response": faq_matches[0]["answer"],
                "source": "faq",
                "confidence": "high",
            }

        messages = [
            {
                "role": "system",
                "content": """You are a customer support agent for a local AI SaaS platform.

RULES:
- Be polite and helpful
- If you don't know the answer, say you'll escalate to human support
- Keep responses under 200 words
- Respond in the same language as the inquiry
- Never promise features that don't exist
- For billing issues, offer to check their account""",
            },
            {"role": "user", "content": f"Customer inquiry: {inquiry}"},
        ]

        result, err = self.client.chat_sync(messages, use_sidecar=True)
        if err:
            return {
                "response": "申し訳ありません。技術的な問題が発生しました。サポートチームにお繋ぎします。",
                "source": "fallback",
                "confidence": "low",
            }

        response = result.get("content", "")
        self.response_log.append(
            {
                "inquiry": inquiry[:100],
                "response": response[:200],
                "time": datetime.now().isoformat(),
            }
        )
        return {"response": response, "source": "ai", "confidence": "medium"}


# ─── Content Operations ────────────────────────────────────────


class ContentOps:
    """Automated content generation and posting."""

    def __init__(self, db: Database, client: OllamaClient):
        self.db = db
        self.client = client
        self.content_log = []

    def generate_blog_post(self, topic, length="medium"):
        """Generate a blog post about a topic."""
        word_limits = {"short": "500", "medium": "1000", "long": "2000"}
        limit = word_limits.get(length, "1000")

        messages = [
            {
                "role": "system",
                "content": f"""You are a professional blog writer. Write an engaging blog post.

RULES:
- Write in Japanese
- Include a catchy title, introduction, body with subheadings, and conclusion
- Use markdown formatting
- Target length: ~{limit} words
- Make it SEO-friendly with natural keyword usage
- No preamble — start directly with the title""",
            },
            {"role": "user", "content": f"Write a blog post about: {topic}"},
        ]

        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}

        content = result.get("content", "")
        self.content_log.append(
            {
                "type": "blog",
                "topic": topic,
                "length": len(content),
                "time": datetime.now().isoformat(),
            }
        )
        return {
            "content": content,
            "word_count": len(content.split()),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }

    def generate_social_posts(self, topic, count=3):
        """Generate social media posts about a topic."""
        messages = [
            {
                "role": "system",
                "content": f"""Generate {count} social media posts about the given topic.

RULES:
- Each post should be under 280 characters
- Include relevant hashtags
- Vary the tone (informative, engaging, question)
- Write in Japanese
- Return as a JSON array of strings""",
            },
            {"role": "user", "content": f"Topic: {topic}"},
        ]

        result, err = self.client.chat_sync(messages, use_sidecar=True)
        if err:
            return {"error": err}

        content = result.get("content", "")
        return {"posts": content, "count": count}


# ─── SEO Monitor ───────────────────────────────────────────────


class SEOMonitor:
    """SEO monitoring and improvement suggestions."""

    def __init__(self, db: Database, client: OllamaClient):
        self.db = db
        self.client = client

    def analyze_content_seo(self, content, target_keywords=None):
        """Analyze content for SEO optimization."""
        keywords = target_keywords or ["AI", "ローカル", "エージェント"]

        messages = [
            {
                "role": "system",
                "content": """You are an SEO expert. Analyze the provided content and give specific improvement suggestions.

Respond in JSON format:
{
  "score": 1-10,
  "keyword_density": "analysis of keyword usage",
  "readability": "readability assessment",
  "suggestions": ["specific improvement 1", "specific improvement 2"],
  "meta_description": "suggested meta description"
}""",
            },
            {
                "role": "user",
                "content": f"Keywords: {', '.join(keywords)}\n\nContent:\n{content[:3000]}",
            },
        ]

        result, err = self.client.chat_sync(messages, use_sidecar=True)
        if err:
            return {"error": err, "score": "N/A"}
        return {"analysis": result.get("content", "")}

    def suggest_content_topics(self):
        """Suggest trending content topics."""
        messages = [
            {
                "role": "system",
                "content": "You are a content strategist. Suggest 5 trending topics for an AI SaaS blog in 2026. Respond as a numbered list in Japanese.",
            },
            {
                "role": "user",
                "content": "What are 5 trending topics for AI/local agent SaaS content right now?",
            },
        ]
        result, err = self.client.chat_sync(messages, use_sidecar=True)
        if err:
            return {"error": err}
        return {"topics": result.get("content", "")}


# ─── Billing Manager ───────────────────────────────────────────


class BillingManager:
    """Billing operations — invoices, payment tracking, credit management."""

    def __init__(self, db: Database):
        self.db = db

    def get_user_billing_summary(self, user_id):
        """Get billing summary for a user."""
        with self.db._connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not user:
                return {"error": "User not found"}

            usage = conn.execute(
                "SELECT service_slug, COUNT(*) as cnt FROM requests WHERE user_id = ? GROUP BY service_slug",
                (user_id,),
            ).fetchall()

            billing = conn.execute(
                "SELECT * FROM billing WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                (user_id,),
            ).fetchall()

            return {
                "user": dict(user),
                "usage_by_service": [dict(u) for u in usage],
                "billing_history": [dict(b) for b in billing],
            }

    def generate_monthly_invoice(self, user_id):
        """Generate a monthly usage invoice."""
        with self.db._connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not user:
                return {"error": "User not found"}

            total_requests = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE user_id = ? AND created_at >= datetime('now', '-30 days')",
                (user_id,),
            ).fetchone()[0]

            return {
                "user": user["name"],
                "period": "last 30 days",
                "total_requests": total_requests,
                "current_credits": user["credits"],
                "plan": user["plan"],
            }

    def auto_topup_low_credits(self, threshold=10, amount=100):
        """Auto-topup users with low credits."""
        with self.db._connect() as conn:
            low_users = conn.execute(
                "SELECT * FROM users WHERE credits < ? AND active = 1", (threshold,)
            ).fetchall()

            topped_up = []
            for u in low_users:
                conn.execute(
                    "UPDATE users SET credits = credits + ? WHERE id = ?",
                    (amount, u["id"]),
                )
                conn.execute(
                    "INSERT INTO billing (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                    (
                        u["id"],
                        amount,
                        "auto_topup",
                        f"Auto topup: credits below {threshold}",
                    ),
                )
                topped_up.append(u["name"])
            conn.commit()

        return {"topped_up": topped_up, "amount": amount}


# ─── Main Ops Agent ────────────────────────────────────────────


class OpsAgent:
    """Autonomous operations agent — runs all ops tasks on a schedule."""

    def __init__(self, db: Database, client: OllamaClient, config=None):
        self.db = db
        self.client = client
        self.config = config or {}
        self._running = False
        self._thread = None
        self.ops_log = []

        self.monitor = Monitor(db)
        self.recovery = AutoRecovery(db)
        self.reporter = ReportGenerator(db, client)
        self.support = CustomerSupportAgent(db, client)
        self.content = ContentOps(db, client)
        self.seo = SEOMonitor(db, client)
        self.billing = BillingManager(db)

        self.schedule = {
            "monitor": 30,
            "report": 3600,
            "seo_check": 7200,
            "billing_check": 3600,
            "content_gen": 86400,
        }
        self.last_run = {}

    def start(self):
        """Start the ops agent loop."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"  {Colors.GREEN}Ops Agent started{Colors.RESET}")

        initial = self.monitor.run_all_checks()
        print(f"  Initial health check: {initial['server']['status']}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self):
        cycle = 0
        while self._running:
            try:
                cycle += 1
                now = time.time()

                checks = self.monitor.run_all_checks()
                alerts = self.monitor.evaluate_alerts(checks)

                for alert in alerts:
                    result = self.recovery.handle_alert(alert)
                    self._log("recovery", f"{alert['message']} → {result['status']}")

                if self._should_run("report", now, 3600):
                    report = self.reporter.generate_daily_report()
                    self._log("report", report.get("ai_summary", "")[:100])

                if self._should_run("seo_check", now, 7200):
                    topics = self.seo.suggest_content_topics()
                    self._log("seo", f"Suggested topics: {str(topics)[:100]}")

                if self._should_run("billing_check", now, 3600):
                    result = self.billing.auto_topup_low_credits()
                    if result["topped_up"]:
                        self._log(
                            "billing", f"Auto topup: {', '.join(result['topped_up'])}"
                        )

                if self._should_run("content_gen", now, 86400):
                    post = self.content.generate_blog_post("AIエージェントの最新動向")
                    if "content" in post:
                        self._log(
                            "content",
                            f"Generated blog post ({post['word_count']} words)",
                        )

                self._print_status(cycle, checks, alerts)
                time.sleep(30)

            except Exception as e:
                self._log("error", str(e))
                time.sleep(60)

    def _should_run(self, task, now, interval):
        last = self.last_run.get(task, 0)
        if now - last >= interval:
            self.last_run[task] = now
            return True
        return False

    def _log(self, category, message):
        entry = {
            "time": datetime.now().isoformat(),
            "category": category,
            "message": message,
        }
        self.ops_log.append(entry)
        print(
            f"  [{Colors.DIM}{entry['time'][11:19]}{Colors.RESET}] {Colors.CYAN}{category}:{Colors.RESET} {message[:120]}"
        )

    def _print_status(self, cycle, checks, alerts):
        server = checks["server"]["status"]
        color = Colors.GREEN if server == "healthy" else Colors.RED
        err_rate = checks["error_rate"]["error_rate"]
        pending = checks["queue"]["pending"]

        status = f"  {Colors.DIM}Cycle {cycle} | {color}Server: {server}{Colors.RESET} | Errors: {err_rate}% | Pending: {pending}"
        if alerts:
            status += f" | {Colors.YELLOW}Alerts: {len(alerts)}{Colors.RESET}"
        print(status)

    def get_status(self):
        return {
            "running": self._running,
            "ops_log_count": len(self.ops_log),
            "last_checks": self.monitor.run_all_checks(),
            "recent_logs": self.ops_log[-10:],
        }
