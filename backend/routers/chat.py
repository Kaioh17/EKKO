"""HTTP twin of chatbot/chat_listener.py's REPL loop -- same
_handle_text_command() turn (routing -> domain match -> llm_fallback ->
execute -> memory), minus the terminal input()/print() loop. See that
module's docstring for why the underlying decision path doesn't care
whether the caller is a REPL or an HTTP request.
"""

import logging

from fastapi import APIRouter, HTTPException

from backend.schemas.chat import ChatIn, ChatOut
from chatbot.chat_listener import _handle_text_command
from memory.scoring import PendingFactCache
from routing.domains import DomainRouter
from routing.matcher import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD
from routing.route import Router
from ui.voice_ui import SESSION_DEFAULT_TIMEOUT_S

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Loaded once at process start, not per request -- Router.load() builds a
# sentence-transformer embedding index, same reason chat_listener.run()
# loads these before its input() loop rather than inside it.
_router = Router.load(threshold=DEFAULT_ROUTING_THRESHOLD)
_domain_router = DomainRouter.load(_router.model)
_pending_facts = PendingFactCache()

# One desktop app, one user, one conversation -- same session_until
# contract chat_listener.run() keeps in a local variable across turns.
_session_until: float | None = None


@router.post("")
def send_chat(body: ChatIn) -> ChatOut:
    transcript = body.message.strip()
    if not transcript:
        raise HTTPException(422, "message must not be empty")

    global _session_until
    logger.info("received message: %r", transcript)
    chunks: list[str] = []
    try:
        _outcome_key, _bundle, _session_until, model_used = _handle_text_command(
            transcript,
            _router,
            execute_enabled=True,
            llm_fallback_enabled=True,
            session_until=_session_until,
            session_timeout=SESSION_DEFAULT_TIMEOUT_S,
            domain_router=_domain_router,
            pending_facts=_pending_facts,
            research_tabs_enabled=True,
            ui=None,
            on_text=chunks.append,
        )
    except Exception:
        logger.exception("turn failed for message: %r", transcript)
        raise
    logger.info("replied via %s: %r", model_used, "\n\n".join(chunks))
    return ChatOut(reply="\n\n".join(chunks), model=model_used)
