"""The one list of llm_fallback providers and what the app needs to know
about each. Dependency-free on purpose: the settings schema, the keys API,
argparse and the UI's Models panel all read it without importing a
provider (or anything heavy). tests/test_catalog.py checks it against the
providers that actually register, so adding a provider means one row here
plus its provider.py.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelChoice:
    id: str
    label: str
    note: str  # one line of pros and cons, shown under the Models dropdown


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    label: str
    kind: str  # shown as a badge: "Free API", "Paid API", "Subscription", "Local"
    detail: str
    key_env: str | None = None  # the .env variable holding its API key
    paid: bool = False  # pay-as-you-go: has a spend cap (brain/budget.py)
    model_setting: str | None = None  # the "llm" settings field that picks its model, if the user can
    models: tuple[ModelChoice, ...] = ()  # the Models dropdown; the current value is always offered too


_PAID_DETAIL = "Pay-as-you-go, stops at its spend cap."

_GEMINI_MODELS = (
    ModelChoice("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", "Fastest and cheapest, free tier. Shallower reasoning on hard questions."),
    ModelChoice("gemini-2.5-flash", "Gemini 2.5 Flash", "Good balance of speed and quality. Slower than Flash-Lite; tighter free-tier limits."),
    ModelChoice("gemini-2.5-pro", "Gemini 2.5 Pro", "Best reasoning and long context. Slowest, and the free tier is very limited."),
)
_GROQ_MODELS = (
    ModelChoice("openai/gpt-oss-20b", "GPT-OSS 20B", "Fastest here and good quality. Reasons before answering, so it spends more tokens (200K/day free)."),
    ModelChoice("openai/gpt-oss-120b", "GPT-OSS 120B", "Strongest reasoning of the set. Slower than 20B; same 200K tokens/day free cap."),
    ModelChoice("qwen/qwen3.8-27b", "Qwen 3.8 27B", "Strong all-rounder with good instruction following. Slower than GPT-OSS 20B."),
)
_CLAUDE_MODELS = (
    ModelChoice("claude-haiku-4-5", "Claude Haiku 4.5", "Fast and cheap, right for voice. Less depth on complex questions."),
    ModelChoice("claude-sonnet-5", "Claude Sonnet 5", "Strong all-rounder for chat. Noticeably slower and pricier than Haiku."),
    ModelChoice("claude-opus-5", "Claude Opus 5", "Most capable. Slowest and the most expensive, burns the spend cap quickly."),
)
_OPENAI_MODELS = (
    ModelChoice("gpt-4.1-nano", "GPT-4.1 nano", "Cheapest and quickest. Weakest at multi-step answers."),
    ModelChoice("gpt-4.1-mini", "GPT-4.1 mini", "Cheap with solid quality. A good default for voice."),
    ModelChoice("gpt-4.1", "GPT-4.1", "Stronger reasoning and instruction following. About 5x the price of mini."),
    ModelChoice("gpt-4o-mini", "GPT-4o mini", "Older and cheap. GPT-4.1 mini is better for the same money."),
    ModelChoice("gpt-4o", "GPT-4o", "Older flagship. Costs more than GPT-4.1 for less."),
)

# Order is the default failover order.
CATALOG: tuple[ProviderInfo, ...] = (
    ProviderInfo("gemini", "Gemini", "Free API", "Search grounding, free tier.", "GEMINI_API_KEY", model_setting="gemini_model", models=_GEMINI_MODELS),
    ProviderInfo("groq", "Groq", "Free API", "Very fast open models, free tier with rate limits.", "GROQ_API_KEY", model_setting="groq_model", models=_GROQ_MODELS),
    ProviderInfo("deepseek", "DeepSeek", "Paid API", _PAID_DETAIL, "DEEPSEEK_API_KEY", paid=True),
    ProviderInfo("claude_api", "Claude API", "Paid API", _PAID_DETAIL, "ANTHROPIC_API_KEY", paid=True, model_setting="claude_api_model", models=_CLAUDE_MODELS),
    ProviderInfo("openai", "OpenAI", "Paid API", _PAID_DETAIL, "OPENAI_API_KEY", paid=True, model_setting="openai_model", models=_OPENAI_MODELS),
    ProviderInfo("claude_code", "Claude Code", "Subscription", "Runs the claude CLI on your subscription, no API key."),
    ProviderInfo("ollama", "Ollama", "Local", "Fully offline; needs a GPU or lots of RAM."),
)

PROVIDERS = tuple(p.name for p in CATALOG)
PAID_PROVIDERS = tuple(p.name for p in CATALOG if p.paid)
KEY_NAMES = tuple(p.key_env for p in CATALOG if p.key_env)
