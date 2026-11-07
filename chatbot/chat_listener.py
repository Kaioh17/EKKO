"""Text-input twin of listener/vad_listener.py: same routing/execute/
llm_fallback decision path, no microphone anywhere in it.

Skips the entire audio pipeline on purpose -- no VAD, no wake word, no
speaker verification, no Whisper, no Piper TTS. A line typed at this
terminal plays the role a verified, transcribed voice command plays in
vad_listener.py: every typed line already came from whoever is sitting
at the keyboard, so there is no wake word to gate it and no speaker
embedding to check it against. Reuses routing/, llm_fallback/, domains/,
and memory/ exactly as they are -- see routing/route.py's module
docstring for why those never depended on audio to begin with.

The terminal itself is still where you type and read replies -- what's
reused from the voice pipeline is ui/voice_ui.py's Tkinter popup, the
same status/response overlay vad_listener.py drives (see its `ui`
integration points). Every response this file prints also lands in that
popup via the same set_state()/show_response() calls _say() makes, minus
everything in _say() that's about gating a live mic. --no-ui disables it
and this becomes a plain terminal chat.

Tagged with `source=CHAT_SOURCE` ("chat") everywhere vad_listener.py
passes the implicit default ("voice") -- see routing/bundle.py's
`source` field and llm_fallback/brain/core.py's `attempt_fallback()`
`source` param. That's the only thing distinguishing a chat turn from a voice turn
anywhere downstream; every decision (matching, thresholds, execution,
fallback) is identical either way.

Usage:
    python chatbot/chat_listener.py
    python chatbot/chat_listener.py --no-execute
    python chatbot/chat_listener.py --no-llm-fallback
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

# Repo root on sys.path, same reasoning/pattern as
# listener/vad_listener.py's identical block -- chatbot/ is a sibling
# package to routing/, llm_fallback/, domains/, memory/.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from domains.loader import compose_system_instruction
from feedback.speech import render as render_response
from listener.vad_listener import _follow_up_key
from llm_fallback.brain import attempt_with_failover
from llm_fallback.brain.core import default_provider
from llm_fallback.brain.prompt import base_system_instruction
from memory.scoring import PendingFactCache, propose_and_score
from memory.context import gather as gather_context
from memory.day_log import record_turn
from memory.store import log_decision as log_memory_decision, read_memory, write_memory
from routing.bundle import RoutingStatus
from routing.config import ConfigError
from routing.domains import DomainRouter
from routing.execute import execute, response_key, spoken_override
from routing.matcher import DEFAULT_THRESHOLD as DEFAULT_ROUTING_THRESHOLD
from routing.route import Router
from ui.voice_ui import SESSION_DEFAULT_TIMEOUT_S, VoiceUI, VoiceUIState

# The tag: every routing/fallback call this file makes passes this, vs.
# the "voice" default every vad_listener.py call site leaves implicit.
CHAT_SOURCE = "chat"


def no_em_dashes(text: str | None) -> str | None:
    """Chat never shows an em dash, whatever the model or a template wrote.
    The prompt asks nicely; this is the guarantee."""
    return re.sub(r"\s*—\s*", ", ", text) if text else text


def _show(
    ui: "VoiceUI | None",
    text: str | None,
    ui_state: "VoiceUIState" = VoiceUIState.SPEAKING,
    show_in_panel: bool = True,
    panel_timeout: float = SESSION_DEFAULT_TIMEOUT_S,
) -> None:
    """Print `text` and, if a popup is running, mirror it there too -- the
    non-audio half of listener/vad_listener.py's _say(): same
    set_state()/show_response() calls, no voice/gate/vad/audio_q since
    there's no mic here to gate.

    show_in_panel defaults to True here, unlike _say()'s show_in_panel,
    which defaults to False and is only turned on for an ambiguous
    llm_fallback answer or an outcome spoken during an active follow-up
    session -- voice mode can afford that, the spoken audio already
    carries every other outcome. Text chat has no spoken half, so the
    popup's response panel is the only place an answer is visible at all
    if this defaulted the same way -- every call site here wants it on.
    """
    if not text:
        return
    print(text)
    if ui is not None:
        ui.set_state(ui_state)
        if show_in_panel:
            ui.show_response(text, timeout_s=panel_timeout)


def _handle_text_command(
    transcript: str,
    router: Router,
    execute_enabled: bool,
    llm_fallback_enabled: bool,
    domain_router: "DomainRouter | None",
    pending_facts: "PendingFactCache | None",
    research_tabs_enabled: bool,
    ui: "VoiceUI | None" = None,
    on_text: "Callable[[str], None] | None" = None,
    provider: str | None = None,
    failover: bool | None = None,
    on_recorded: "Callable[[], None] | None" = None,
    on_outcome: "Callable[[object, dict], None] | None" = None,
    on_offer: "Callable[[str, list[str]], None] | None" = None,
    panel_timeout: float = SESSION_DEFAULT_TIMEOUT_S,
) -> tuple[str | None, object, str | None]:
    """Text-mode twin of listener/vad_listener.py's _handle_command(): same
    domain routing / memory-context / llm_fallback / execute decision path,
    minus every audio-only concern (MicGate, VAD, Piper TTS).

    Conversation memory is the shared day log (memory/day_log.py): every
    turn is recorded there, and the next NO_MATCH call sees the newest
    turns whole via memory.context.gather(). No timer decides what is
    remembered; panel_timeout only controls how long the popup stays up.

    on_text, when given, is called with every piece of text this turn
    shows (same strings _show() prints/pops up) -- how backend/routers/
    chat.py recovers the reply body for an HTTP caller. on_recorded is
    called after each turn is written to the day log (the backend uses it
    to nudge memory/today_summary.py). on_outcome gets the LLM call's
    FallbackOutcome and the context stats sent with it (the backend turns
    them into the chat analytics line); never called for a router-only turn.

    Returns (outcome_key, bundle, model_used). model_used is the LLM that
    answered ("gemini-..." from FallbackOutcome.model), "router" when the
    deterministic matcher handled it with no LLM call, or None when nothing
    answered.
    """
    if provider is None:
        # Same "None means read the UI's configured provider" contract as
        # llm_fallback.brain.core.attempt_fallback -- resolved here, not
        # left to that function, since base_system_instruction() below
        # needs a concrete provider name too.
        provider = default_provider()

    if ui is not None:
        ui.set_state(VoiceUIState.PROCESSING)

    def _emit(
        text: str | None,
        ui_state: "VoiceUIState" = VoiceUIState.SPEAKING,
        show_in_panel: bool = True,
        panel_timeout: float = panel_timeout,
    ) -> None:
        text = no_em_dashes(text)
        _show(ui, text, ui_state, show_in_panel, panel_timeout)
        if on_text is not None and text:
            on_text(text)

    def _record(assistant: str | None, kind: str) -> None:
        record_turn(transcript, no_em_dashes(assistant), source=CHAT_SOURCE, kind=kind)
        if on_recorded is not None:
            on_recorded()

    bundle = router.route(transcript, source=CHAT_SOURCE)
    print(f"  {bundle.describe()}")

    model_used: str | None = "router" if bundle.status is RoutingStatus.MATCHED else None
    matched_domain: str | None = None
    if bundle.status is RoutingStatus.NO_MATCH and llm_fallback_enabled:
        system_instruction_override = None
        if domain_router is not None:
            match = domain_router.match(transcript)
            if match is not None:
                matched_domain, _score = match
                base_prompt = base_system_instruction(provider)
                system_instruction_override = compose_system_instruction(matched_domain, base_prompt)
                print(f"  [domains] matched {matched_domain!r}")
            domain_router.record(transcript, matched_domain)

        memory_context = None
        if pending_facts is not None:
            memory_context = read_memory().as_prompt_block() or None

        context = gather_context()

        fallback_outcome = attempt_with_failover(
            transcript,
            provider=provider,
            failover=failover,
            system_instruction_override=system_instruction_override,
            memory_context=memory_context,
            domain=matched_domain,
            source=CHAT_SOURCE,
            **context.as_kwargs(),
        )
        if on_outcome is not None:
            on_outcome(fallback_outcome, context.stats)
        if fallback_outcome.error:
            print(f"  [llm_fallback] {fallback_outcome.error}")
        elif fallback_outcome.bundle is not None:
            print(f"  [llm_fallback] {fallback_outcome.bundle.describe()}")
            bundle = fallback_outcome.bundle
            model_used = fallback_outcome.model
        elif fallback_outcome.answer:
            print(f"  [llm_fallback] answered: {fallback_outcome.answer!r}")
            model_used = fallback_outcome.model
            _record("\n\n".join(filter(None, (fallback_outcome.answer, fallback_outcome.follow_up))), "answer")
            _emit(fallback_outcome.answer)

            # Permission layer: never open tabs unasked, offer them instead.
            if research_tabs_enabled and fallback_outcome.urls and on_offer is not None:
                on_offer(transcript, fallback_outcome.urls)

            if pending_facts is not None and fallback_outcome.memory_candidate is not None:
                existing_memory = read_memory()
                decision = propose_and_score(
                    fallback_outcome.memory_candidate,
                    existing_memory,
                    transcript,
                    pending=pending_facts,
                    model=domain_router.model if domain_router is not None else None,
                )
                log_memory_decision(fallback_outcome.memory_candidate, decision)
                if decision.accept:
                    write_memory(decision.section, decision.text, decision.merge_into)
                    print(f"  [memory] wrote to {decision.section!r} ({decision.reason})")
                else:
                    print(f"  [memory] not written ({decision.reason})")

            # Prefer llm_fallback's own grounded next step (already plain
            # text) over the generic "anything else?" -- same preference
            # order _handle_command's caller (listen()) applies between
            # grounded_follow_up and _follow_up_key().
            if fallback_outcome.follow_up:
                _emit(fallback_outcome.follow_up)
            else:
                _emit(render_response(_follow_up_key(bundle, "ambiguous_answer")))
            return "ambiguous_answer", bundle, model_used
        else:
            print(f"  [llm_fallback] no command match, no answer (reason: {fallback_outcome.reason!r})")

    result = None
    if bundle.status is RoutingStatus.MATCHED and bundle.intent == "daily_briefing" and execute_enabled:
        # The handler narrates over TTS and opens its own windows; in chat the
        # briefing is just a message in the session.
        sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
        from daily_briefing import build_chat_briefing

        text = build_chat_briefing()
        _record(text, "command")
        _emit(text)
        return "command_confirmed", bundle, model_used
    if bundle.status is RoutingStatus.MATCHED:
        if execute_enabled:
            result = execute(bundle)
            print(f"  {result.describe()}")
        else:
            print(f"  --no-execute, not running {bundle.handler}")

    outcome_key = response_key(bundle, result)
    override_text = spoken_override(result) if result is not None else None
    outcome_text = None
    if outcome_key:
        outcome_text = override_text or render_response(
            outcome_key, intent=bundle.intent, domain=matched_domain, slots=dict(bundle.slots)
        )
    _record(outcome_text, "command" if bundle.status is RoutingStatus.MATCHED else "unrecognized")
    if outcome_key:
        # ERROR for anything but a clean MATCHED execution, same ui_state
        # choice _handle_command's own _say() call makes.
        _emit(
            outcome_text,
            ui_state=VoiceUIState.ERROR if bundle.status is not RoutingStatus.MATCHED else VoiceUIState.SPEAKING,
        )

    follow_up_key = _follow_up_key(bundle, outcome_key)
    if follow_up_key:
        _emit(render_response(follow_up_key, intent=bundle.intent))

    return outcome_key, bundle, model_used


def run(
    execute_enabled: bool = True,
    llm_fallback_enabled: bool = True,
    domain_routing_enabled: bool = True,
    memory_enabled: bool = True,
    research_tabs_enabled: bool = True,
    ui_enabled: bool = True,
    routing_threshold: float = DEFAULT_ROUTING_THRESHOLD,
    session_timeout: float = SESSION_DEFAULT_TIMEOUT_S,
    llm_provider: str | None = None,
) -> None:
    print("Loading intent router...")
    try:
        router = Router.load(threshold=routing_threshold)
    except ConfigError as exc:
        raise SystemExit(f"[routing] {exc}") from exc

    domain_router = None
    if domain_routing_enabled and llm_fallback_enabled:
        print("Loading domain router...")
        domain_router = DomainRouter.load(router.model)

    pending_facts = PendingFactCache() if memory_enabled else None

    ui = None
    if ui_enabled:
        ui = VoiceUI()
        ui.start()

    print(f"EKKO chat ({CHAT_SOURCE}) -- type a command, blank line/'exit' to quit.")
    try:
        while True:
            try:
                transcript = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not transcript or transcript.lower() in ("exit", "quit"):
                break
            _handle_text_command(
                transcript,
                router,
                execute_enabled,
                llm_fallback_enabled,
                domain_router,
                pending_facts,
                research_tabs_enabled,
                ui,
                provider=llm_provider,
                panel_timeout=session_timeout,
            )
    finally:
        if ui is not None:
            ui.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-execute", action="store_true", help="Route and log, but run nothing.")
    parser.add_argument("--no-llm-fallback", action="store_true", help="Disable the NO_MATCH Gemini fallback.")
    parser.add_argument("--no-domain-routing", action="store_true", help="Disable domain-persona detection.")
    parser.add_argument("--no-memory", action="store_true", help="Disable memory context/candidate writing.")
    parser.add_argument("--no-research-tabs", action="store_true", help="Never open browser tabs for an answer.")
    parser.add_argument("--no-ui", action="store_true", help="Don't open the ui/voice_ui.py popup, terminal only.")
    parser.add_argument("--threshold", type=float, default=DEFAULT_ROUTING_THRESHOLD)
    parser.add_argument(
        "--provider",
        choices=["gemini", "claude_code", "ollama", "groq", "deepseek", "claude_api", "openai"],
        default=None,
        help="Which llm_fallback/<provider>/provider.py answers a NO_MATCH "
        "transcript (default: the provider chosen in the app).",
    )
    args = parser.parse_args()

    run(
        execute_enabled=not args.no_execute,
        llm_fallback_enabled=not args.no_llm_fallback,
        domain_routing_enabled=not args.no_domain_routing,
        memory_enabled=not args.no_memory,
        research_tabs_enabled=not args.no_research_tabs,
        ui_enabled=not args.no_ui,
        routing_threshold=args.threshold,
        llm_provider=args.provider,
    )
