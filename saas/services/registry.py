"""
vibe-local SaaS — service implementations
Content generation, data analysis, translation, FAQ bot, summarizer, code review
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.engine import OllamaClient


class BaseService:
    def __init__(self, client: OllamaClient):
        self.client = client

    def process(self, user_input, context=None):
        raise NotImplementedError


class ContentGenerator(BaseService):
    """Generate blog posts, articles, marketing copy."""

    slug = "content-gen"
    system_prompt = """You are a professional content writer. Generate high-quality content based on the user's request.

RULES:
- Write in the same language as the request
- Include a title, introduction, body, and conclusion
- Use markdown formatting
- Be engaging and informative
- Keep it under 2000 words
- No preamble — start directly with the content"""

    def process(self, user_input, context=None):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input},
        ]
        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}
        return {
            "content": result.get("content", ""),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }


class DataAnalyzer(BaseService):
    """Analyze data and return insights."""

    slug = "data-analysis"
    system_prompt = """You are a data analyst. Analyze the provided data and return structured insights.

RULES:
- Identify key patterns and trends
- Provide actionable recommendations
- Use bullet points for clarity
- If CSV data, analyze columns, distributions, correlations
- Keep analysis under 500 words"""

    def process(self, user_input, context=None):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"Analyze this data:\n\n{user_input}"},
        ]
        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}
        return {
            "analysis": result.get("content", ""),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }


class Translator(BaseService):
    """Translate text between languages."""

    slug = "translate"
    system_prompt = """You are a professional translator. Translate the provided text accurately.

RULES:
- Preserve the original meaning and tone
- Use natural phrasing in the target language
- If the target language isn't specified, translate to Japanese
- Return only the translation — no explanations"""

    def process(self, user_input, context=None):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"Translate this:\n\n{user_input}"},
        ]
        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}
        return {
            "translation": result.get("content", ""),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }


class FAQBot(BaseService):
    """Answer questions from a knowledge base."""

    slug = "faq-bot"
    system_prompt = """You are a helpful customer support agent. Answer questions based on the provided knowledge base.

RULES:
- If you know the answer, respond clearly and concisely
- If you don't know, say "I don't have information about that. Please contact support."
- Be polite and professional
- Keep responses under 200 words"""

    def __init__(self, client, knowledge_base=None):
        super().__init__(client)
        self.knowledge_base = knowledge_base or []

    def process(self, user_input, context=None):
        kb_context = ""
        if self.knowledge_base:
            kb_context = "\n\nKnowledge Base:\n" + "\n".join(
                f"- Q: {q}\n  A: {a}" for q, a in self.knowledge_base
            )
        messages = [
            {"role": "system", "content": self.system_prompt + kb_context},
            {"role": "user", "content": user_input},
        ]
        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}
        return {
            "answer": result.get("content", ""),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }


class Summarizer(BaseService):
    """Summarize long documents."""

    slug = "summarize"
    system_prompt = """You are a summarizer. Condense the provided text into key points.

RULES:
- Extract the main ideas
- Use bullet points
- Keep it under 300 words
- Preserve important facts and numbers
- No preamble — start directly with the summary"""

    def process(self, user_input, context=None):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"Summarize this:\n\n{user_input[:10000]}"},
        ]
        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}
        return {
            "summary": result.get("content", ""),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }


class CodeReviewer(BaseService):
    """Review code for bugs and best practices."""

    slug = "code-review"
    system_prompt = """You are a senior code reviewer. Review the provided code for issues.

RULES:
- Check for bugs, security issues, and performance problems
- Suggest improvements with specific code examples
- Rate the code from 1-10
- Be constructive and specific
- Focus on the most important issues first"""

    def process(self, user_input, context=None):
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"Review this code:\n\n{user_input}"},
        ]
        result, err = self.client.chat_sync(messages)
        if err:
            return {"error": err}
        return {
            "review": result.get("content", ""),
            "tokens": result.get("usage", {}).get("total_tokens", 0),
        }


SERVICE_REGISTRY = {
    "content-gen": ContentGenerator,
    "data-analysis": DataAnalyzer,
    "translate": Translator,
    "faq-bot": FAQBot,
    "summarize": Summarizer,
    "code-review": CodeReviewer,
}
