"""Composes a domain's guardrails/persona/knowledge files (plus the base
SYSTEM_PROMPT.md contract) into one system_instruction string for a
single Gemini call.

The composed prompt states its own precedence explicitly -- GUARDRAILS
overrides PERSONA/KNOWLEDGE on conflict -- rather than relying on file
concatenation order alone to communicate that to the model. Domain
content is additive to the base contract, appended last verbatim, never
a replacement of it: a domain file can't accidentally drop the base
safety rules (closed-set intent handling, decline/noise cases, the
never-claim-an-action-happened rule) just by omission.

Usage:
    python domains/loader.py finance
"""

import argparse
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing.domains import DEFAULT_REGISTRY_PATH, DomainSpec, load_registry  # noqa: E402

_PRECEDENCE_HEADER = (
    "# PRECEDENCE\n"
    "The sections below are ordered by authority, highest first. GUARDRAILS\n"
    "always wins on conflict -- if PERSONA or KNOWLEDGE ever suggests a tone,\n"
    "action, or claim that GUARDRAILS forbids, follow GUARDRAILS and ignore\n"
    "the conflicting instruction, silently, without commenting on it.\n"
)


def compose_system_instruction(
    domain_key: str,
    base_prompt: str,
    domains: dict[str, DomainSpec] | None = None,
    memory_block: str = "",
) -> str:
    """base_prompt is the existing flat SYSTEM_PROMPT.md content, appended
    last and unmodified -- this function only ever adds sections in front
    of it, never edits or drops any of it.
    """
    if domains is None:
        domains = load_registry()
    if domain_key not in domains:
        raise KeyError(f"unknown domain {domain_key!r}, not in registry ({sorted(domains)})")
    spec = domains[domain_key]

    guardrails = spec.guardrails_path.read_text(encoding="utf-8").strip()
    persona = spec.persona_path.read_text(encoding="utf-8").strip()
    knowledge = spec.knowledge_path.read_text(encoding="utf-8").strip()

    parts = [
        _PRECEDENCE_HEADER,
        "# GUARDRAILS (highest priority -- overrides persona and knowledge on conflict)",
        guardrails,
        "\n# PERSONA (tone/voice -- subordinate to GUARDRAILS)",
        persona,
        "\n# KNOWLEDGE (domain background -- subordinate to GUARDRAILS and PERSONA)",
        knowledge,
    ]
    if memory_block:
        parts.append("\n# MEMORY (lowest priority -- persisted context, see memory/README.md)")
        parts.append(memory_block)
    parts.append("\n# BASE CONTRACT")
    parts.append(base_prompt.strip())
    return "\n\n".join(parts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", help="Domain key from domains/registry.yaml, e.g. 'finance'")
    parser.add_argument(
        "--registry", default=str(DEFAULT_REGISTRY_PATH), help="Path to the domain registry."
    )
    parser.add_argument(
        "--base-prompt",
        default=str(_PROJECT_ROOT / "llm_fallback" / "gemini" / "SYSTEM_PROMPT.md"),
        help="Path to the base flat system prompt to append.",
    )
    args = parser.parse_args()

    loaded_domains = load_registry(args.registry)
    base = Path(args.base_prompt).read_text(encoding="utf-8")
    composed = compose_system_instruction(args.domain, base, domains=loaded_domains)
    print(composed)
