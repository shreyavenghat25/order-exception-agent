"""Cost-aware model router: small model first, escalate to large model on low confidence.

Also enforces the per-exception budget (LLM calls and INR cost).
"""
from __future__ import annotations

from dataclasses import dataclass

from exops.config import ModelTier, Settings
from exops.llm.base import LLMRequest, LLMResult
from exops.llm.providers import make_provider
from exops.models import Usage


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class RoutedResult:
    result: LLMResult
    tier: str
    escalated: bool


class ModelRouter:
    def __init__(self, settings: Settings):
        self.s = settings
        self.tiers = {t.name: (t, make_provider(t.provider, t.model)) for t in (settings.small, settings.large)}

    def _cost(self, tier: ModelTier, r: LLMResult) -> float:
        return r.input_tokens / 1000 * tier.input_cost_per_1k_inr + r.output_tokens / 1000 * tier.output_cost_per_1k_inr

    def _call(self, tier_name: str, req: LLMRequest, usage: Usage) -> LLMResult:
        if usage.calls >= self.s.max_llm_calls_per_case:
            raise BudgetExceeded(f"LLM call budget ({self.s.max_llm_calls_per_case}) exhausted")
        if usage.cost_inr >= self.s.max_cost_inr_per_case:
            raise BudgetExceeded(f"cost budget Rs {self.s.max_cost_inr_per_case} exhausted")
        tier, provider = self.tiers[tier_name]
        r = provider.complete(req)
        usage.add(Usage(input_tokens=r.input_tokens, output_tokens=r.output_tokens,
                        cost_inr=self._cost(tier, r), latency_ms=r.latency_ms, calls=1))
        return r

    def run(self, req: LLMRequest, usage: Usage, force_large: bool = False) -> RoutedResult:
        if force_large:
            return RoutedResult(self._call("large", req, usage), "large", False)
        r = self._call("small", req, usage)
        conf = float(r.data.get("confidence", 0))
        if conf >= self.s.escalate_below_confidence:
            return RoutedResult(r, "small", False)
        try:
            return RoutedResult(self._call("large", req, usage), "large", True)
        except BudgetExceeded:
            return RoutedResult(r, "small", False)  # keep small answer; guardrails will gate low confidence
