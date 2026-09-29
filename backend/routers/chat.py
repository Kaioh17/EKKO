"""HTTP twin of chatbot/chat_listener.py's REPL loop -- same
_handle_text_command() turn (routing -> domain match -> llm_fallback ->
execute -> memory), minus the terminal input()/print() loop. See that
module's docstring for why the underlying decision path doesn't care
whether the caller is a REPL or an HTTP request.
"""

import functools
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from backend.config import get
from backend.schemas.chat import ChatIn, ChatOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])
_load_lock = threading.Lock()


@dataclass(frozen=True)
class _Engine:
    handle: Callable[..., tuple]
    router: Any
    domain_router: Any
    pending_facts: Any
    session_timeout: float


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
    from ui.voice_ui import SESSION_DEFAULT_TIMEOUT_S

    rtr = Router.load(threshold=get("general").routing_threshold)
    return _Engine(_handle_text_command, rtr, DomainRouter.load(rtr.model), PendingFactCache(), SESSION_DEFAULT_TIMEOUT_S)


# One desktop app, one user, one conversation -- same session_until
# contract chat_listener.run() keeps in a local variable across turns.
_session_until: float | None = None


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

    global _session_until
    logger.info("received message (%d chars)", len(transcript))
    chunks: list[str] = []
    try:
        _outcome_key, _bundle, _session_until, model_used = engine.handle(
            transcript,
            engine.router,
            execute_enabled=True,
            llm_fallback_enabled=True,
            session_until=_session_until,
            session_timeout=engine.session_timeout,
            domain_router=engine.domain_router,
            pending_facts=engine.pending_facts,
            research_tabs_enabled=True,
            ui=None,
            on_text=chunks.append,
        )
    except Exception:
        logger.exception("chat turn failed")
        raise
    logger.info("replied via %s", model_used)
    return ChatOut(reply="\n\n".join(chunks), model=model_used)
