"""
vibe-local SaaS — Web Site Manager Agent
Manages a static website hosted on Cloudflare Pages.

Capabilities:
- Generate/update blog posts (AI → HTML)
- Update landing page content (AI → optimized copy)
- SEO meta tag optimization
- 404 monitoring
- Auto git commit & push → triggers CI/CD deploy
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.engine import OllamaClient, Colors
from saas.database import Database


class WebManager:
    """Autonomous web site manager — updates content, pushes to git, triggers deploy."""

    def __init__(
        self, db: Database, client: OllamaClient, site_dir=None, repo_url=None
    ):
        self.db = db
        self.client = client
        self.site_dir = site_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "website"
        )
        self.repo_url = repo_url
        self.blog_dir = os.path.join(self.site_dir, "blog")
        self.ops_log = []
        os.makedirs(self.blog_dir, exist_ok=True)

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

    def generate_blog_post(self, topic, publish=True):
        """Generate a blog post and add it to the site."""
        self._log("blog", f"Generating post: {topic}")

        messages = [
            {
                "role": "system",
                "content": """You are a professional tech blogger writing for a Japanese AI SaaS platform.

RULES:
- Write in Japanese
- Use HTML format (not markdown)
- Include: <h1> title, <p> paragraphs, <h2> subheadings
- Keep it 800-1500 words
- Make it engaging and informative
- Include a meta description suggestion at the top as <!-- meta: ... -->
- No preamble — start directly with the HTML""",
            },
            {"role": "user", "content": f"Write a blog post about: {topic}"},
        ]

        result, err = self.client.chat_sync(messages)
        if err:
            self._log("error", f"Blog generation failed: {err}")
            return {"error": err}

        content = result.get("content", "")
        meta_desc = ""
        meta_match = re.search(r"<!--\s*meta:\s*(.*?)\s*-->", content)
        if meta_match:
            meta_desc = meta_match.group(1)
            content = re.sub(r"<!--\s*meta:.*?-->", "", content, count=1)

        slug = re.sub(
            r"[^\w\u3000-\u9fff\u3040-\u309f\u30a0-\u30ff]+", "-", topic.lower()
        ).strip("-")
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{date_str}-{slug}.html"
        filepath = os.path.join(self.blog_dir, filename)

        html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{meta_desc}">
    <title>{topic} — vibe-local</title>
    <link rel="stylesheet" href="/styles.css">
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 20px; background: #0a0a0a; color: #e0e0e0; line-height: 1.8; }}
        h1 {{ color: #00ff88; font-size: 1.8em; margin-bottom: 8px; }}
        h2 {{ color: #00ff88; font-size: 1.3em; margin-top: 32px; margin-bottom: 12px; }}
        p {{ color: #ccc; margin-bottom: 16px; }}
        a {{ color: #00ff88; }}
        .back {{ display: inline-block; margin-bottom: 24px; color: #888; }}
    </style>
</head>
<body>
    <a href="/" class="back">← ホームに戻る</a>
    {content}
    <hr style="border-color: #222; margin: 40px 0;">
    <p style="color: #666; font-size: 0.85em;">© 2026 vibe-local</p>
</body>
</html>"""

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        self._log("blog", f"Saved: {filename} ({len(content)} chars)")

        if publish:
            self._git_commit(f"blog: {topic}")

        return {
            "file": filename,
            "path": filepath,
            "meta": meta_desc,
            "length": len(content),
        }

    def update_landing_page(self):
        """Update the main landing page with optimized content."""
        self._log("landing", "Updating landing page")

        index_path = os.path.join(self.site_dir, "index.html")
        if not os.path.isfile(index_path):
            self._log("error", f"index.html not found at {index_path}")
            return {"error": "index.html not found"}

        with open(index_path, "r", encoding="utf-8") as f:
            current_html = f.read()

        messages = [
            {
                "role": "system",
                "content": """You are a conversion-focused web copywriter. Improve the landing page of an AI SaaS platform.

RULES:
- Return the COMPLETE updated HTML
- Keep the same CSS and structure
- Only improve the text content (headlines, descriptions, feature copy)
- Make it more compelling and conversion-focused
- Keep it in Japanese
- Optimize for SEO with natural keyword usage
- Return ONLY the HTML — no explanations""",
            },
            {
                "role": "user",
                "content": f"Improve this landing page:\n\n{current_html[:8000]}",
            },
        ]

        result, err = self.client.chat_sync(messages)
        if err:
            self._log("error", f"Landing page update failed: {err}")
            return {"error": err}

        new_html = result.get("content", "")
        if not new_html or len(new_html) < 1000:
            self._log("error", "Generated HTML too short, skipping")
            return {"error": "Generated content too short"}

        with open(index_path, "w", encoding="utf-8") as f:
            f.write(new_html)

        self._log("landing", f"Updated index.html ({len(new_html)} chars)")
        self._git_commit("update: landing page optimization")
        return {"length": len(new_html)}

    def optimize_seo(self):
        """Review and optimize all pages for SEO."""
        self._log("seo", "Running SEO optimization")

        pages = []
        for f in os.listdir(self.site_dir):
            if f.endswith(".html"):
                pages.append(os.path.join(self.site_dir, f))
        for f in os.listdir(self.blog_dir):
            if f.endswith(".html"):
                pages.append(os.path.join(self.blog_dir, f))

        results = []
        for page in pages[:5]:
            with open(page, "r", encoding="utf-8") as f:
                content = f.read()

            has_title = bool(re.search(r"<title>[^<]+</title>", content))
            has_meta_desc = bool(re.search(r'<meta\s+name="description"', content))
            has_h1 = bool(re.search(r"<h1[^>]*>", content))

            issues = []
            if not has_title:
                issues.append("Missing <title>")
            if not has_meta_desc:
                issues.append("Missing meta description")
            if not has_h1:
                issues.append("Missing <h1>")

            if issues:
                self._log("seo", f"{os.path.basename(page)}: {', '.join(issues)}")
                results.append({"file": os.path.basename(page), "issues": issues})
            else:
                results.append({"file": os.path.basename(page), "status": "OK"})

        return results

    def _git_commit(self, message):
        """Git commit and push to trigger CI/CD deploy."""
        try:
            repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            subprocess.run(
                ["git", "add", "website/"], cwd=repo_dir, capture_output=True, text=True
            )
            subprocess.run(
                ["git", "commit", "-m", f"[web] {message}"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            if self.repo_url:
                subprocess.run(
                    ["git", "push", self.repo_url, "main"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                )
            else:
                subprocess.run(
                    ["git", "push"], cwd=repo_dir, capture_output=True, text=True
                )
            self._log("git", f"Committed & pushed: {message}")
            return True
        except Exception as e:
            self._log("git", f"Failed: {e}")
            return False

    def run_full_cycle(self):
        """Run a complete web management cycle."""
        print(f"\n{'=' * 60}")
        print(f"  WEB MANAGER — Full Cycle")
        print(f"{'=' * 60}\n")

        self._log("start", "Starting web management cycle")

        self.optimize_seo()
        self.update_landing_page()

        topics = ["AIエージェントの最新トレンド2026", "ローカルAIのメリットと活用法"]
        for topic in topics:
            self.generate_blog_post(topic)

        self._log("complete", f"Cycle done. {len(self.ops_log)} operations logged.")
        return self.ops_log
