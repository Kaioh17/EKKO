"""The one list of live-search providers and what the app needs to know
about each. Dependency-free on purpose (same role as llm_fallback/catalog.py):
the settings schema, the keys API and the UI's Search panel all read it
without importing a provider. Adding a provider = one row here, a <name>.py
exposing search() and live_usage(), and one entry in __init__.PROVIDER_MODULES.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchProviderInfo:
    name: str
    label: str
    kind: str  # badge text
    detail: str  # one line: what it is
    key_env: str  # the .env variable holding its API key
    key_url: str  # where the user creates a key
    dashboard_url: str  # where the user sees real usage and billing
    cost: str  # what it costs, in plain words
    pros: str
    cons: str
    steps: tuple[str, ...]  # how to get a key
    live_usage: bool  # True: live_usage() reports real numbers; False: EKKO's own count only


CATALOG: tuple[SearchProviderInfo, ...] = (
    SearchProviderInfo(
        "brave", "Brave Search", "Search index",
        "Independent web index with its own crawler. Returns result titles, links and snippets.",
        "BRAVE_API_KEY",
        "https://api-dashboard.search.brave.com/app/keys",
        "https://api-dashboard.search.brave.com/app/usage",
        "$5 of free credit every month, then pay per search. Each search costs a fraction of a cent, so check the dashboard for exact spend.",
        "Fast. Fresh results with page age. No dependence on Google or Bing.",
        "Snippets are short. Needs a card on file, and paid use is billed per request.",
        (
            "Create an account at api-dashboard.search.brave.com.",
            "Pick the Search plan (the free credit applies automatically).",
            "Open API Keys, create a key and paste it here.",
        ),
        live_usage=False,  # per-call quota headers are recorded; no usage endpoint
    ),
    SearchProviderInfo(
        "tavily", "Tavily", "Search for AI",
        "Search built for LLMs. Returns longer, cleaned page excerpts instead of bare snippets.",
        "TAVILY_API_KEY",
        "https://app.tavily.com/home",
        "https://app.tavily.com/home",
        "About 1,000 free credits every month, no card needed. A basic search uses 1 credit.",
        "Bigger free tier. Content is already trimmed for a model, so answers need fewer tokens.",
        "No result age. Usually a little slower than Brave.",
        (
            "Sign up at app.tavily.com.",
            "Your first API key is on the home page; copy it (it starts with tvly-).",
            "Paste it here.",
        ),
        live_usage=True,
    ),
)

PROVIDERS = tuple(p.name for p in CATALOG)
KEY_NAMES = tuple(p.key_env for p in CATALOG)
