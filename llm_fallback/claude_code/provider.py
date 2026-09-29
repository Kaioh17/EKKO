"""ClaudeCodeProvider -- the thin transport wrapper brain/core.py calls.

Spawns `claude -p ... --output-format json` and returns raw text plus
usage/cost. Everything else (schema, validation, domain/memory/research
wiring, logging) lives in llm_fallback/brain/ now.

Previously the system prompt came from CLAUDE.md, auto-loaded by the
`claude` CLI itself from its cwd -- that only ever carried one static
file and had no way to layer a domain's guardrails/persona/knowledge in
per call. `--append-system-prompt` replaces that: the composed
system_instruction brain/core.py builds (BASE_CONTRACT.md + this
provider's delta + an optional domain override) is passed explicitly on
every call, same as every other provider, so Claude Code gets the same
domain-aware/memory-aware prompt Gemini already does.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from llm_fallback.brain.providers import NO_SEARCH_NOTE, ProviderError, RawResponse, register_provider  # noqa: E402

CLAUDE_CODE_DIR = _PACKAGE_DIR
DEFAULT_MODEL = "haiku"
# Uncalibrated guess, same caveat every provider's own default timeout
# carries -- `claude -p` latency depends on network/API conditions this
# project doesn't control.
DEFAULT_TIMEOUT = 20.0

CLAUDE_COMMAND = ["claude", "-p"]


class ClaudeCodeProvider:
    name = "claude_code"
    default_model = DEFAULT_MODEL

    def capability_note(self, enable_search: bool = True, **kwargs) -> str:
        if enable_search:
            return (
                "It's fine to use WebSearch for a factual or current-events "
                "question. `urls` is 0-3 real WebSearch result URLs worth "
                "reading further, only when answer is non-null and you "
                "actually searched for it -- never invented, [] otherwise."
            )
        return NO_SEARCH_NOTE

    def call(
        self,
        prompt: str,
        system_instruction: str,
        timeout: float = DEFAULT_TIMEOUT,
        model: str = DEFAULT_MODEL,
        enable_search: bool = True,
        **kwargs,
    ) -> RawResponse:
        command = [
            *CLAUDE_COMMAND,
            prompt,
            "--model", model,
            "--append-system-prompt", system_instruction,
            "--permission-mode", "dontAsk",
            "--output-format", "json",
        ]
        if enable_search:
            command += ["--allowedTools", "WebSearch"]

        start = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=CLAUDE_CODE_DIR,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ProviderError(f"claude did not finish within {timeout}s") from None
        except FileNotFoundError:
            raise ProviderError("claude CLI not found on PATH") from None
        latency = round(time.perf_counter() - start, 3)

        if completed.returncode != 0:
            raise ProviderError(
                f"claude exited {completed.returncode}: {completed.stderr.strip()[:300]}"
            )
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"claude output wasn't valid JSON: {exc}") from None

        # is_error: true is NOT raised here, deliberately: the envelope
        # still carries real usage/total_cost_usd (tokens were spent
        # producing it) and this project tracks spend precisely, so it
        # gets logged either way -- result's `result` field on an error
        # response isn't the expected JSON, so brain/parsing.py's
        # parse_result() fails to parse it and reports that in `reason`,
        # same bug-tolerant outcome as any other malformed response, just
        # without discarding the cost data.
        return RawResponse(
            text=envelope.get("result", ""),
            model=model,
            usage=envelope.get("usage", {}),
            latency_seconds=latency,
            extras={
                "session_id": envelope.get("session_id"),
                "cost_usd": envelope.get("total_cost_usd"),
            },
        )


register_provider(ClaudeCodeProvider())
