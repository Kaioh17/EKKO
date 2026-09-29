"""Shared spend cap for the pay-as-you-go providers (deepseek, claude_api,
openai).

Each such provider reports every call's cost in `extras["cost_usd"]`, which
brain/logging.py sums into logs/<provider>/usage_summary.json. Before any
request, the provider calls `check_budget()`, which raises ProviderBlocked
(brain/core.py speaks its message) once that running total reaches the cap.
The cap is per provider, in USD, read from the settings DB (llm.budget_usd)
on each call, so raising it in the UI takes effect immediately. To resume
after hitting it, raise the cap or reset that provider's usage_summary.json.
"""

from llm_fallback.brain.logging import load_usage_summary, usage_summary_path_for
from llm_fallback.brain.providers import ProviderBlocked

DEFAULT_BUDGET_USD = 2.0


def budget_usd(provider: str) -> float:
    from backend.config import get  # lazy, same as brain/core.py

    return get("llm").budget_usd.get(provider, DEFAULT_BUDGET_USD)


def spent_usd(provider: str) -> float:
    summary = load_usage_summary(usage_summary_path_for(provider))
    return float(summary.get("extras_totals", {}).get("cost_usd", 0.0))


def check_budget(provider: str, label: str) -> None:
    """Raises ProviderBlocked before any request is sent once `provider`'s
    recorded spend has reached its cap. A call already in flight can't be
    interrupted, so each provider also bounds max output tokens.
    """
    spent, cap = spent_usd(provider), budget_usd(provider)
    if spent >= cap:
        raise ProviderBlocked(
            f"{label} is paused. It has used {spent:.2f} dollars, which reaches your "
            f"{cap:.2f} dollar limit. Switch provider or raise the limit to continue."
        )
