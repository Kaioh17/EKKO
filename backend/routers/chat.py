"""HTTP twin of chatbot/chat_listener.py's REPL loop -- same
_handle_text_command() turn (routing -> domain match -> llm_fallback ->
execute -> memory), minus the terminal input()/print() loop. See that
module's docstring for why the underlying decision path doesn't care
whether the caller is a REPL or an HTTP request.
"""

import functools
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from backend.config import get
from backend.schemas.chat import BudgetOut, ChatIn, ChatOut, ChatStats, TokensOut
from llm_fallback.catalog import PAID_PROVIDERS
from memory import today_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])
_load_lock = threading.Lock()


@dataclass(frozen=True)
class _Engine:
    handle: Callable[..., tuple]
    router: Any
    domain_router: Any
    pending_facts: Any


@functools.cache
def _engine() -> _Engine | None:
    """Loaded on first chat, not at import: Router.load() builds a
    sentence-transformer index (seconds), which used to block backend
    startup. Returns None when chatbot/ isn't present (fresh clone)."""
    try:
        from chatbot.chat_listener import _handle_text_command
    except ImportError:
        logger.warning("chat unavailable: chatbot/chat_listener.py not found")
        return None
    from memory.scoring import PendingFactCache
    from routing.domains import DomainRouter
    from routing.route import Router
    rtr = Router.load(threshold=get("general").routing_threshold)
    return _Engine(_handle_text_command, rtr, DomainRouter.load(rtr.model), PendingFactCache())


def warm() -> None:
    """Load the chat models in the background so the first message is fast."""
    with _load_lock:
        _engine()


@router.post("")
def send_chat(body: ChatIn) -> ChatOut:
    transcript = body.message.strip()
    if not transcript:
        raise HTTPException(422, "message must not be empty")

    with _load_lock:
        engine = _engine()
    if engine is None:
        raise HTTPException(503, "Chat isn't available in this build (chatbot/ is missing).")

    logger.info("received message (%d chars)", len(transcript))
    chunks: list[str] = []
    calls: list[tuple] = []
    start = time.perf_counter()
    try:
        _outcome_key, _bundle, model_used = engine.handle(
            transcript,
            engine.router,
            execute_enabled=True,
            llm_fallback_enabled=True,
            domain_router=engine.domain_router,
            pending_facts=engine.pending_facts,
            research_tabs_enabled=True,
            ui=None,
            on_text=chunks.append,
            on_recorded=today_summary.refresh_async,
            on_outcome=lambda outcome, ctx: calls.append((outcome, ctx)),
            provider=body.provider,
            # A model picked by hand answers or errors; only "auto" fails over.
            failover=None if body.provider is None else False,
        )
    except Exception:
        logger.exception("chat turn failed")
        raise
    logger.info("replied via %s", model_used)
    stats = _stats(model_used, round(time.perf_counter() - start, 3), *(calls[0] if calls else (None, {})))
    return ChatOut(reply="\n\n".join(chunks), model=model_used, stats=stats)


def _first(usage: dict, *keys: str) -> int | None:
    return next((int(usage[k]) for k in keys if isinstance(usage.get(k), (int, float))), None)


def _tokens(usage: dict) -> TokensOut | None:
    """One shape for every provider's usage dict (OpenAI/DeepSeek, Anthropic
    and the claude CLI, Gemini, Ollama all name these differently)."""
    if not usage:
        return None
    cached = _first(usage, "prompt_cache_hit_tokens", "cached_tokens", "cache_read_input_tokens", "cachedContentTokenCount")
    inp = _first(usage, "prompt_tokens", "promptTokenCount", "prompt_eval_count")
    if inp is None and "input_tokens" in usage:  # Anthropic counts cached input separately
        inp = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    out = _first(usage, "completion_tokens", "output_tokens", "candidatesTokenCount", "eval_count")
    return TokensOut(input=inp, output=out, cached=cached)


def _budget(provider: str | None) -> BudgetOut | None:
    if provider not in PAID_PROVIDERS:
        return None
    from llm_fallback.brain.budget import budget_usd, spent_usd

    return BudgetOut(spent_usd=round(spent_usd(provider), 4), cap_usd=budget_usd(provider))


def _stats(model_used: str | None, total_s: float, outcome, ctx: dict) -> ChatStats:
    """The analytics line for one turn. `outcome` is None when the
    deterministic router handled it (or the LLM was never asked)."""
    if outcome is None:
        return ChatStats(handled_by="router" if model_used == "router" else "none", total_s=total_s)
    from llm_fallback.brain.parsing import TRUNCATED_REASON

    cost = outcome.extras.get("cost_usd")
    return ChatStats(
        handled_by="llm" if (outcome.answer or outcome.bundle) else "none",
        total_s=total_s,
        provider=outcome.provider,
        model=outcome.model,
        llm_s=outcome.latency_seconds,
        tokens=_tokens(outcome.usage),
        cost_usd=round(cost, 6) if isinstance(cost, (int, float)) else None,
        budget=_budget(outcome.provider),
        failover_from=list(outcome.extras.get("failover_from", [])),
        web_search=bool(outcome.signals.get("web_search")),
        web_research=bool(outcome.signals.get("web_research")),
        sources=len(outcome.urls),
        recall_hits=outcome.signals.get("recall_hits", 0),
        domain=outcome.domain,
        truncated=outcome.reason == TRUNCATED_REASON,
        context_turns=ctx.get("window_turns"),
        context_tokens=ctx.get("window_tokens_est"),
        error=outcome.error,
    )
