"""HTTP twin of chatbot/chat_listener.py's REPL loop -- same
_handle_text_command() turn (routing -> domain match -> llm_fallback ->
execute -> memory), minus the terminal input()/print() loop. See that
module's docstring for why the underlying decision path doesn't care
whether the caller is a REPL or an HTTP request.
"""

import functools
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.config import get
from backend.schemas.chat import BudgetOut, ChatIn, ChatOut, ChatStats, ResearchOffer, TokensOut
from llm_fallback.brain.parsing import AnswerStream
from llm_fallback.brain.transport import delta_sink
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
    return _turn(body)


@router.post("/stream")
def stream_chat(body: ChatIn) -> StreamingResponse:
    """Same turn as POST /api/chat, as NDJSON: {"text": <answer so far>}
    lines while the model writes (replaced, not appended, so a failover or
    retry just overwrites), then {"done": <ChatOut>} or {"error": <message>}.
    Only providers on brain/transport.py's chat_completion stream (deepseek,
    openai, groq); the rest send "done" alone.
    """
    events: queue.Queue = queue.Queue()

    def work() -> None:
        stream = AnswerStream()
        last = ""

        def on_delta(delta: str | None) -> None:
            nonlocal stream, last
            if delta is None:  # a new model call: forget the last one's text
                stream, last = AnswerStream(), ""
                return
            stream.feed(delta)
            if stream.partial != last:
                last = stream.partial
                events.put({"text": last})

        try:
            events.put({"done": _turn(body, on_delta).model_dump()})
        except HTTPException as exc:
            events.put({"error": exc.detail})
        except Exception as exc:  # noqa: BLE001 -- _turn already logged it
            events.put({"error": str(exc) or "chat turn failed"})

    threading.Thread(target=work, daemon=True).start()

    def lines():
        while True:
            event = events.get()
            yield json.dumps(event) + "\n"
            if "done" in event or "error" in event:
                return

    return StreamingResponse(lines(), media_type="application/x-ndjson")


def _turn(body: ChatIn, on_delta: Callable[[str | None], None] | None = None) -> ChatOut:
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
    offers: list[ResearchOffer] = []
    start = time.perf_counter()
    sink_token = delta_sink.set(on_delta)
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
            on_offer=lambda query, urls: offers.append(ResearchOffer(query=query, urls=urls)),
            provider=body.provider,
            # A model picked by hand answers or errors; only "auto" fails over.
            failover=None if body.provider is None else False,
        )
    except Exception:
        logger.exception("chat turn failed")
        raise
    finally:
        delta_sink.reset(sink_token)
    logger.info("replied via %s", model_used)
    stats = _stats(model_used, round(time.perf_counter() - start, 3), *(calls[0] if calls else (None, {})))
    return ChatOut(
        reply="\n\n".join(chunks), model=model_used, stats=stats, research_offer=offers[0] if offers else None
    )


@router.post("/research", status_code=204)
def accept_research(body: ResearchOffer) -> None:
    """The user said yes to an offer (chat button or avatar card): open the
    Brave tabs. The script re-validates every url as http/https."""
    from llm_fallback.brain.research import open_research_tabs

    error = open_research_tabs(body.query, body.urls[:3])
    if error:
        raise HTTPException(502, error)


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
