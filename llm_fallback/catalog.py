"""The one list of llm_fallback providers and what the app needs to know
about each. Dependency-free on purpose: the settings schema, the keys API,
argparse and the UI's Models panel all read it without importing a
provider (or anything heavy). tests/test_catalog.py checks it against the
providers that actually register, so adding a provider means one row here
plus its provider.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    label: str
    kind: str  # shown as a badge: "Free API", "Paid API", "Subscription", "Local"
    detail: str
    key_env: str | None = None  # the .env variable holding its API key
    paid: bool = False  # pay-as-you-go: has a spend cap (brain/budget.py)
    model_setting: str | None = None  # the "llm" settings field that picks its model, if the user can


_PAID_DETAIL = "Pay-as-you-go, stops at its spend cap."

# Order is the default failover order.
CATALOG: tuple[ProviderInfo, ...] = (
    ProviderInfo("gemini", "Gemini", "Free API", "Search grounding, free tier.", "GEMINI_API_KEY"),
    ProviderInfo("deepseek", "DeepSeek", "Paid API", _PAID_DETAIL, "DEEPSEEK_API_KEY", paid=True),
    ProviderInfo("claude_api", "Claude API", "Paid API", _PAID_DETAIL, "ANTHROPIC_API_KEY", paid=True, model_setting="claude_api_model"),
    ProviderInfo("openai", "OpenAI", "Paid API", _PAID_DETAIL, "OPENAI_API_KEY", paid=True, model_setting="openai_model"),
    ProviderInfo("claude_code", "Claude Code", "Subscription", "Runs the claude CLI on your subscription, no API key."),
    ProviderInfo("ollama", "Ollama", "Local", "Fully offline; needs a GPU or lots of RAM."),
)

PROVIDERS = tuple(p.name for p in CATALOG)
PAID_PROVIDERS = tuple(p.name for p in CATALOG if p.paid)
KEY_NAMES = tuple(p.key_env for p in CATALOG if p.key_env)
