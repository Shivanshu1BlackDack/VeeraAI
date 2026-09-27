"""LLM providers with Groq as default. OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import httpx

DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SECONDS", "8"))
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class LLMError(Exception):
    pass


class BaseLLM(ABC):
    @abstractmethod
    def complete_json(
        self, system: str, user: str, seed: int = 20260426
    ) -> Dict[str, Any]:
        pass


def _load_env() -> tuple[str, str, str, str, int]:
    provider = os.environ.get("LLM_PROVIDER", "groq").lower()
    key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
    fallback = os.environ.get("LLM_FALLBACK_MODEL", "llama-3.1-8b-instant")
    seed = int(os.environ.get("LLM_SEED", "20260426"))
    return provider, key, model, fallback, seed


class OpenAICompatLLM(BaseLLM):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.extra_headers = extra_headers or {}

    def complete_json(
        self, system: str, user: str, seed: int = 20260426
    ) -> Dict[str, Any]:
        import time
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 2000,
            "response_format": {"type": "json_object"},
        }
        # Groq / some models support seed
        body["seed"] = seed
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }
        
        for attempt in range(5):
            try:
                with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                    r = client.post(self.base_url, json=body, headers=headers)
                    if r.status_code == 429 and attempt < 4:
                        print(f"\n[WARN] Bot LLM Rate limit hit. Sleeping 10s... (Attempt {attempt+1}/5)")
                        time.sleep(10)
                        continue
                        
                    if r.status_code >= 400:
                        body2 = dict(body)
                        body2.pop("seed", None)
                        r = client.post(self.base_url, json=body2, headers=headers)
                    if r.status_code >= 400:
                        body3 = dict(body)
                        body3.pop("seed", None)
                        body3.pop("response_format", None)
                        r = client.post(self.base_url, json=body3, headers=headers)
                    
                    if r.status_code == 429 and attempt < 4:
                        print(f"\n[WARN] Bot LLM Rate limit hit. Sleeping 10s... (Attempt {attempt+1}/5)")
                        time.sleep(10)
                        continue
                        
                    if r.status_code >= 400:
                        raise LLMError(f"{r.status_code}: {r.text[:500]}")
                    data = r.json()
                    text = data["choices"][0]["message"]["content"]
                    return json.loads(text)
            except Exception as e:
                if attempt < 4:
                    print(f"\n[WARN] Bot LLM Timeout/Error. Sleeping 10s... (Attempt {attempt+1}/5)")
                    time.sleep(10)
                    continue
                raise LLMError(f"Network error: {str(e)}")
        raise LLMError("Exhausted retries")


def _try_openai_compat(
    url: str, key: str, model: str, system: str, user: str, seed: int, extra: Optional[Dict] = None
) -> Dict[str, Any]:
    llm = OpenAICompatLLM(key, model, url, extra_headers=extra)
    return llm.complete_json(system, user, seed)


class GroqLLM(BaseLLM):
    def __init__(self, api_key: str, model: str) -> None:
        self._key = api_key
        self._model = model

    def complete_json(
        self, system: str, user: str, seed: int = 20260426
    ) -> Dict[str, Any]:
        return _try_openai_compat(GROQ_URL, self._key, self._model, system, user, seed)


class OllamaLLM(BaseLLM):
    def __init__(self, model: str, base: str) -> None:
        self.model = model
        self.base = base.rstrip("/")

    def complete_json(
        self, system: str, user: str, seed: int = 20260426
    ) -> Dict[str, Any]:
        # Ollama: no json_object guarantee; ask for raw JSON in prompt
        prompt = f"{system}\n\n{user}\n\nReply with ONLY a valid JSON object, no markdown."
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "seed": seed},
        }
        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{self.base}/api/generate", json=body)
            r.raise_for_status()
            text = r.json().get("response", "")
        import re

        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise LLMError("No JSON in Ollama response")
        return json.loads(m.group())


def get_llm_primary_fallback() -> tuple[BaseLLM, Optional[BaseLLM]]:
    provider, key, model, fallback_model, _ = _load_env()
    if provider == "ollama":
        base = os.environ.get("OLLAMA_URL", "http://localhost:11434")
        ol = OllamaLLM(model or "llama3", base)
        return ol, None

    if not key:
        raise LLMError("LLM_API_KEY not set")

    if provider == "groq":
        return GroqLLM(key, model), GroqLLM(key, fallback_model)

    if provider == "openai":
        primary = OpenAICompatLLM(key, model or "gpt-4o-mini", OPENAI_URL)
        fb = OpenAICompatLLM(key, fallback_model or "gpt-4o-mini", OPENAI_URL)
        return primary, fb

    if provider == "deepseek":
        return OpenAICompatLLM(key, model or "deepseek-chat", DEEPSEEK_URL), OpenAICompatLLM(
            key, fallback_model or "deepseek-chat", DEEPSEEK_URL
        )

    if provider == "openrouter":
        extra = {"HTTP-Referer": "https://magicpin.com"}
        return OpenAICompatLLM(
            key, model or "anthropic/claude-3-haiku", OPENROUTER_URL, extra_headers=extra
        ), OpenAICompatLLM(
            key, fallback_model or "anthropic/claude-3-haiku", OPENROUTER_URL, extra_headers=extra
        )

    if provider in ("anthropic", "gemini"):
        return _anthropic_or_gemini_factory(provider, key, model), None

    return GroqLLM(key, model), GroqLLM(key, fallback_model)


def _anthropic_or_gemini_factory(provider: str, key: str, model: str) -> BaseLLM:
    class _Wrap(BaseLLM):
        def complete_json(self, system: str, user: str, seed: int = 20260426) -> Dict[str, Any]:
            if provider == "anthropic":
                m = model or "claude-3-5-sonnet-20241022"
                body = {
                    "model": m,
                    "max_tokens": 2000,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
                h = {
                    "x-api-key": key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                }
                with httpx.Client(timeout=DEFAULT_TIMEOUT) as c:
                    r = c.post(ANTHROPIC_URL, json=body, headers=h)
                    r.raise_for_status()
                    text = r.json()["content"][0]["text"]
            else:
                m = model or "gemini-1.5-flash"
                full = f"{system}\n\n{user}"
                url = f"{GEMINI_BASE}/{m}:generateContent?key={key}"
                body = {
                    "contents": [{"parts": [{"text": full}]}],
                    "generationConfig": {
                        "temperature": 0,
                        "maxOutputTokens": 2000,
                    },
                }
                with httpx.Client(timeout=DEFAULT_TIMEOUT) as c:
                    r = c.post(url, json=body)
                    r.raise_for_status()
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            import re

            mm = re.search(r"\{[\s\S]*\}", text)
            if not mm:
                raise LLMError("No JSON in model response")
            return json.loads(mm.group())

    return _Wrap()


def complete_composer_json(system: str, user: str) -> Dict[str, Any]:
    """Primary then fallback on LLMError or bad parse."""
    _, _, _, _, seed = _load_env()
    primary, fallback = get_llm_primary_fallback()
    try:
        return primary.complete_json(system, user, seed)
    except Exception as e1:
        if fallback is None:
            raise LLMError(str(e1)) from e1
        try:
            return fallback.complete_json(system, user, seed)
        except Exception as e2:
            raise LLMError(f"primary: {e1}; fallback: {e2}") from e2
