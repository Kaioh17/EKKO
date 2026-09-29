"""attempt_fallback() -- the one entry point every caller (listener/
vad_listener.py, chatbot/chat_listener.py, and each provider's own CLI)
uses, regardless of which provider answers.

Orchestrates: build_prompt() -> provider.call() -> parse_result() ->
validate_pick() -> slot completion -> research hook -> FallbackOutcome ->
log_call(). Never raises -- any failure (missing key, timeout, unreachable
provider, malformed output, a pick that doesn't validate) comes back as an
outcome with bundle=answer=None, which every caller treats exactly like a
plain NO_MATCH.
"""

import dataclasses
import re
import sys
import time
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing.bundle import IntentBundle  # noqa: E402
from routing.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from routing.host import load_dotenv  # noqa: E402

from memory.schema import ShortMemoryResponse  # noqa: E402

from llm_fallback.brain.logging import log_call  # noqa: E402
from llm_fallback.brain.outcome import FallbackOutcome  # noqa: E402
from llm_fallback.brain.parsing import ParsedResult, parse_result  # noqa: E402
from llm_fallback.brain.prompt import base_system_instruction, build_prompt  # noqa: E402
from llm_fallback.brain.providers import ProviderBlocked, get_provider  # noqa: E402
from llm_fallback.brain.research import research_enabled, run_research, wants_research  # noqa: E402
from llm_fallback.brain.validate import validate_pick  # noqa: E402

load_dotenv()  # API keys only; every other setting is in the settings DB


def default_provider() -> str:
    """The UI's chosen provider (settings DB, section "llm")."""
    from backend.config import get  # lazy: keeps provider CLIs light

    return get("llm").provider

_UNSET = object()


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _complete_transcript_slots(bundle, config, proposed: dict, transcript: str, short_memory) -> IntentBundle:
    """A transcript slot is the whole utterance, which for the reply to a
    clarifying question is only the answer ("in documents"). Rebuild the
    full request from what the user actually said this session. The model
    may propose the wording (`slots.<name>`), but it is only accepted if
    every word in it appears in something the user said -- so it can
    reorder and join their words, not add any. Otherwise fall back to the
    plain join of the pending request and this reply.
    """
    intent = config.get(bundle.intent)
    said = [t for h in short_memory.history for t in (h.get("prior_question"), h.get("follow_up_answer")) if t]
    said += [short_memory.prior_question, short_memory.follow_up_answer, transcript]
    said = [t for t in said if t]
    pool = _tokens(" ".join(said))
    slots = dict(bundle.slots)
    for slot in intent.slots:
        if slot.type != "transcript":
            continue
        proposal = proposed.get(slot.name, "").strip()
        if proposal and _tokens(proposal) <= pool:
            slots[slot.name] = proposal
        else:
            slots[slot.name] = f"{short_memory.prior_question} {transcript}"
    return dataclasses.replace(bundle, slots=slots)


_REJECTED_PICK_NOTE = (
    "\n\nNOTE: your previous reply picked the intent {intent!r}, but EKKO "
    "rejected that pick: the transcript is not a command for it. Treat the "
    "transcript as an open-ended question or request and answer it "
    "directly: set `intent` to null and put a spoken answer in `answer`. "
    "Only leave `answer` null if you genuinely cannot understand it."
)


def _merge_usage(a: dict, b: dict) -> dict:
    """Sum numeric usage counters across the first call and its retry."""
    merged = dict(a)
    for key, value in (b or {}).items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] = merged[key] + value
        else:
            merged.setdefault(key, value)
    return merged


def _ask_again(provider_impl, prompt: str, system_instruction: str, model, rejected_intent: str, **call_kwargs):
    """The one retry after a rejected pick: same prompt plus a note that
    the pick was refused, so the model answers the question directly.
    Returns (raw response, parsed reply)."""
    raw = provider_impl.call(prompt + _REJECTED_PICK_NOTE.format(intent=rejected_intent), system_instruction, model=model, **call_kwargs)
    return raw, parse_result(raw.text)


def attempt_fallback(
    transcript: str,
    provider: str | None = None,
    model: str | object = _UNSET,
    config_path=DEFAULT_CONFIG_PATH,
    timeout: float | object = _UNSET,
    log_path: str | Path | None | bool = None,
    system_instruction_override: str | None = None,
    memory_context: str | None = None,
    domain: str | None = None,
    short_memory: ShortMemoryResponse | None = None,
    source: str = "voice",
    enable_research: bool | None = None,
    **provider_kwargs,
) -> FallbackOutcome:
    """Live entry point, called from listener/vad_listener.py's
    _handle_command() and chatbot/chat_listener.py's _handle_text_command()
    whenever the deterministic matcher returns NO_MATCH.

    provider selects which llm_fallback/<name>/provider.py answers this
    call (see llm_fallback/brain/providers.py's registry) -- everything
    else about this function's contract is identical regardless of which
    one is picked. provider_kwargs are passed straight through to that
    provider's capability_note()/call() (e.g. Gemini's enable_search).

    system_instruction_override / memory_context / domain / short_memory
    are all purely additive, optional hooks -- omitting them reproduces
    the flat-prompt, no-domain, no-memory behavior. `domain` is stamped
    onto the outcome for logging and, when it names a
    domains/<key>/research.yaml, also selects that domain's research
    pipeline -- see brain/research.py's run_research().
    """
    provider = provider or default_provider()
    if enable_research is None:
        enable_research = research_enabled()
    provider_impl = get_provider(provider)
    call_model = provider_impl.default_model if model is _UNSET else model
    call_timeout = None if timeout is _UNSET else timeout

    config = load_config(config_path)  # fresh load, see validate_pick's docstring
    research_result, research_chars = None, 9000
    if enable_research and not provider_kwargs.get("enable_search", False) and (domain or wants_research(transcript)):
        found = run_research(transcript, domain)
        if found:
            research_result, research_chars = found

    capability_note = provider_impl.capability_note(**provider_kwargs)
    prompt = build_prompt(
        transcript,
        config,
        capability_note=capability_note,
        memory_context=memory_context,
        short_memory=short_memory,
        source=source,
        research_context=(research_result.as_prompt_block(research_chars)
                          if research_result and (research_result.sources or research_result.facts) else None),
    )
    system_instruction = system_instruction_override or base_system_instruction(provider)

    parsed = ParsedResult.empty()
    usage: dict = {}
    extras: dict = {}
    latency_seconds = None
    error = None
    bundle = None
    urls: list[str] = []
    used_model = call_model

    start = time.perf_counter()
    try:
        call_kwargs = dict(provider_kwargs)
        if call_timeout is not None:
            call_kwargs["timeout"] = call_timeout
        print(f"  [model_used] {provider}/{call_model}")
        raw = provider_impl.call(prompt, system_instruction, model=call_model, **call_kwargs)
        latency_seconds = raw.latency_seconds if raw.latency_seconds is not None else round(time.perf_counter() - start, 3)
        usage = raw.usage
        extras = raw.extras
        used_model = raw.model
        parsed = parse_result(raw.text)
        urls = raw.grounded_urls or parsed.urls
        bundle = validate_pick(transcript, parsed.intent, parsed.slots, config)
        if parsed.intent is not None and bundle is None:
            # The model picked a command that failed re-validation (e.g.
            # web_search for "how do I make butter": no trigger phrase to
            # build a query from). parse_result() drops `answer` whenever
            # an intent is set, so without this the person gets silence.
            # Ask once more, telling the model its pick was rejected so it
            # answers the question directly instead.
            print(f"  [model_used] {provider}/{call_model} (retry after rejected pick)")
            retry_raw, retry_parsed = _ask_again(provider_impl, prompt, system_instruction, call_model, parsed.intent, **call_kwargs)
            latency_seconds = round(time.perf_counter() - start, 3)
            usage = _merge_usage(usage, retry_raw.usage)
            extras = retry_raw.extras or extras
            used_model = retry_raw.model
            if retry_parsed.intent is None:
                parsed = retry_parsed
                urls = retry_raw.grounded_urls or parsed.urls
        if bundle is not None and source != "voice":
            bundle = dataclasses.replace(bundle, source=source)
        if bundle is not None and short_memory is not None:
            bundle = _complete_transcript_slots(bundle, config, parsed.slots, transcript, short_memory)
        # bundle is None either because the model proposed intent=null
        # (nothing to validate), or it proposed an intent that failed
        # re-validation -- in both cases there's no command to run, so
        # whatever answer it also gave (if any) is still fair to speak.
    except ProviderBlocked as blocked:
        # Deliberate refusal before any request was sent (spend cap).
        # Surfaced as a spoken answer so EKKO tells the user why, rather
        # than the silent "didn't catch that" a plain error produces.
        latency_seconds = round(time.perf_counter() - start, 3)
        parsed = ParsedResult.empty("provider_blocked", answer=blocked.spoken)
        extras = {"blocked": 1}
    except Exception as exc:  # noqa: BLE001 -- a provider's own transport error, never propagated
        latency_seconds = round(time.perf_counter() - start, 3)
        error = str(exc)

    outcome = FallbackOutcome(
        bundle=bundle,
        answer=parsed.answer,
        urls=urls or ((research_result.urls[:3] if research_result and parsed.answer else [])),
        raw_intent=parsed.intent,
        raw_slots=parsed.slots,
        reason=parsed.reason,
        provider=provider,
        model=used_model,
        usage=usage,
        latency_seconds=latency_seconds,
        error=error,
        follow_up=parsed.follow_up,
        memory_candidate=parsed.memory_candidate,
        domain=domain,
        source=source,
        extras=extras,
    )
    if log_path is not False:
        log_call(outcome, transcript, None if log_path is None else log_path)
    return outcome


if __name__ == "__main__":
    import argparse

    from llm_fallback.brain.logging import load_usage_summary, usage_summary_path_for
    from llm_fallback.brain.providers import available_providers, load_providers

    # Not `import llm_fallback.brain`: that would re-import this very
    # module under `python -m`.
    load_providers()

    parser = argparse.ArgumentParser(
        description="Manually test any llm_fallback provider through the shared brain, "
        "for parity-checking across providers (see llm_fallback/README.md)."
    )
    parser.add_argument("transcript", nargs="?", help="A transcript that already failed the deterministic matcher.")
    parser.add_argument("--provider", default=None, choices=available_providers(), help="Default: the UI setting.")
    parser.add_argument("--model", default=None, help="Override the provider's default model.")
    parser.add_argument("--domain", default=None, help="Force a domains/registry.yaml key for this call's system instruction.")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--source", default="voice", choices=["voice", "chat"])
    parser.add_argument("--dry-run", action="store_true", help="Print the prompt/system instruction without calling the provider.")
    parser.add_argument("--no-log", action="store_true")
    parser.add_argument("--usage", action="store_true", help="Print --provider's running usage_summary.json and exit.")
    args = parser.parse_args()
    args.provider = args.provider or default_provider()

    if args.usage:
        summary = load_usage_summary(usage_summary_path_for(args.provider))
        print(f"{usage_summary_path_for(args.provider)}")
        print(f"  calls:           {summary['total_calls']}")
        print(f"  total latency:   {summary['total_latency_seconds']:.2f}s")
        print(f"  usage totals:    {summary['usage_totals']}")
        print(f"  extras totals:   {summary['extras_totals']}")
        print(f"  first call:      {summary['first_call']}")
        print(f"  last call:       {summary['last_call']}")
        raise SystemExit(0)

    if not args.transcript:
        parser.error("a transcript is required unless --usage is given")

    domain_override = None
    if args.domain:
        from domains.loader import compose_system_instruction
        domain_override = compose_system_instruction(args.domain, base_system_instruction(args.provider))

    if args.dry_run:
        cfg = load_config(DEFAULT_CONFIG_PATH)
        provider_impl = get_provider(args.provider)
        note = provider_impl.capability_note()
        prompt = build_prompt(args.transcript, cfg, capability_note=note, source=args.source)
        print(f"provider: {args.provider}")
        print(f"model:    {args.model or provider_impl.default_model}")
        print(f"domain:   {args.domain or '(none -- BASE_CONTRACT.md + provider delta)'}")
        print(f"\n--- system_instruction ---\n{domain_override or base_system_instruction(args.provider)}\n--- end system_instruction ---\n")
        print(f"prompt:\n{prompt}")
        raise SystemExit(0)

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.timeout:
        kwargs["timeout"] = args.timeout

    result = attempt_fallback(
        args.transcript,
        provider=args.provider,
        system_instruction_override=domain_override,
        source=args.source,
        log_path=False if args.no_log else None,
        **kwargs,
    )
    print(f"Transcript: {args.transcript!r}")
    print(f"Provider:   {result.provider}  Model: {result.model}")
    print(f"Domain:     {result.domain or '(none)'}")
    if result.error:
        print(f"Error:      {result.error}")
    print(f"Raw pick:   intent={result.raw_intent!r} slots={result.raw_slots} reason={result.reason!r}")
    if result.bundle is not None:
        print(f"Validated:  {result.bundle.describe()}")
    elif result.answer is not None:
        print(f"Answer:     {result.answer!r}")
        print(f"Follow-up:  {result.follow_up!r}")
        if result.memory_candidate:
            print(f"Memory:     {result.memory_candidate}")
        print(f"URLs:       {result.urls}")
    else:
        print("Validated:  no match, no answer (nothing to run or say)")
    print(f"Latency:    {result.latency_seconds}s")
    print(f"Usage:      {result.usage}")
    print(f"Extras:     {result.extras}")
    raise SystemExit(0)
