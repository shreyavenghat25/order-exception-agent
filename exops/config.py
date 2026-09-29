"""Runtime configuration. Everything is overridable through environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class ModelTier:
    name: str
    provider: str  # mock | anthropic | openai | ollama
    model: str
    input_cost_per_1k_inr: float
    output_cost_per_1k_inr: float


@dataclass
class Settings:
    # --- Model router: try the small tier first, escalate to large on low confidence
    small: ModelTier = field(
        default_factory=lambda: ModelTier(
            name="small",
            provider=_env("EXOPS_SMALL_PROVIDER", _env("EXOPS_PROVIDER", "mock")),
            model=_env("EXOPS_SMALL_MODEL", "mock-small"),
            input_cost_per_1k_inr=float(_env("EXOPS_SMALL_IN_COST", "0.02")),
            output_cost_per_1k_inr=float(_env("EXOPS_SMALL_OUT_COST", "0.10")),
        )
    )
    large: ModelTier = field(
        default_factory=lambda: ModelTier(
            name="large",
            provider=_env("EXOPS_LARGE_PROVIDER", _env("EXOPS_PROVIDER", "mock")),
            model=_env("EXOPS_LARGE_MODEL", "mock-large"),
            input_cost_per_1k_inr=float(_env("EXOPS_LARGE_IN_COST", "0.25")),
            output_cost_per_1k_inr=float(_env("EXOPS_LARGE_OUT_COST", "1.25")),
        )
    )
    escalate_below_confidence: float = float(_env("EXOPS_ESCALATE_BELOW", "0.75"))

    # --- Per-exception budgets
    max_cost_inr_per_case: float = float(_env("EXOPS_MAX_COST_INR", "2.0"))
    max_llm_calls_per_case: int = int(_env("EXOPS_MAX_LLM_CALLS", "6"))
    max_write_actions_per_case: int = int(_env("EXOPS_MAX_WRITE_ACTIONS", "3"))

    # --- Guardrail policy
    auto_refund_limit_inr: float = float(_env("EXOPS_AUTO_REFUND_LIMIT", "5000"))
    min_auto_confidence: float = float(_env("EXOPS_MIN_AUTO_CONFIDENCE", "0.70"))

    # --- Storage
    db_path: str = _env("EXOPS_DB", "exops.db")


def get_settings() -> Settings:
    return Settings()
