"""Real LLM providers over plain HTTPS (no SDK dependency). Select with EXOPS_PROVIDER."""
from __future__ import annotations

import json
import os
import time
import urllib.request

from exops.llm.base import LLMRequest, LLMResult, estimate_tokens, extract_json


def _post(url: str, payload: dict, headers: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"content-type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https endpoints
        return json.loads(resp.read())


class AnthropicProvider:
    def __init__(self, model: str):
        self.model = model
        self.key = os.environ["ANTHROPIC_API_KEY"]

    def complete(self, req: LLMRequest) -> LLMResult:
        t0 = time.perf_counter()
        body = _post(
            "https://api.anthropic.com/v1/messages",
            {"model": self.model, "max_tokens": req.max_tokens, "system": req.system,
             "messages": [{"role": "user", "content": req.prompt}]},
            {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
        )
        text = "".join(b.get("text", "") for b in body.get("content", []))
        u = body.get("usage", {})
        return LLMResult(extract_json(text), text, self.model, u.get("input_tokens", 0),
                         u.get("output_tokens", 0), (time.perf_counter() - t0) * 1000)


class OpenAIProvider:
    def __init__(self, model: str):
        self.model = model
        self.key = os.environ["OPENAI_API_KEY"]
        self.base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def complete(self, req: LLMRequest) -> LLMResult:
        t0 = time.perf_counter()
        body = _post(
            f"{self.base}/chat/completions",
            {"model": self.model, "max_tokens": req.max_tokens, "response_format": {"type": "json_object"},
             "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.prompt}]},
            {"authorization": f"Bearer {self.key}"},
        )
        text = body["choices"][0]["message"]["content"]
        u = body.get("usage", {})
        return LLMResult(extract_json(text), text, self.model, u.get("prompt_tokens", 0),
                         u.get("completion_tokens", 0), (time.perf_counter() - t0) * 1000)


class OllamaProvider:
    def __init__(self, model: str):
        self.model = model
        self.base = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    def complete(self, req: LLMRequest) -> LLMResult:
        t0 = time.perf_counter()
        body = _post(
            f"{self.base}/api/chat",
            {"model": self.model, "stream": False, "format": "json",
             "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.prompt}]},
            {}, timeout=180,
        )
        text = body["message"]["content"]
        return LLMResult(extract_json(text), text, self.model,
                         body.get("prompt_eval_count", estimate_tokens(req.system + req.prompt)),
                         body.get("eval_count", estimate_tokens(text)), (time.perf_counter() - t0) * 1000)


def make_provider(provider: str, model: str):
    from exops.llm.mock import MockProvider

    if provider == "mock":
        return MockProvider(model)
    if provider == "anthropic":
        return AnthropicProvider(model)
    if provider == "openai":
        return OpenAIProvider(model)
    if provider == "ollama":
        return OllamaProvider(model)
    raise ValueError(f"unknown provider {provider}")
