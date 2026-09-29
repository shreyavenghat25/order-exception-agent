"""Provider-agnostic LLM interface."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMRequest:
    task: str  # classify | plan | judge
    system: str
    prompt: str
    # Structured copy of the inputs. Real providers ignore it; the mock provider uses it so
    # the platform can run offline and deterministically.
    context: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 800


@dataclass
class LLMResult:
    data: dict[str, Any]
    raw: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LLMProvider(Protocol):
    model: str

    def complete(self, req: LLMRequest) -> LLMResult: ...


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response (tolerates ```json fences / prose)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        m = _JSON_BLOCK.search(text)
        if not m:
            raise ValueError(f"no JSON object in model output: {text[:200]}") from err
        return json.loads(m.group(0))
