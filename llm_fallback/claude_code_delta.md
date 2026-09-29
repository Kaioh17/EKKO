You are being run as a subprocess (`claude -p ...`, see
`llm_fallback/claude_code/provider.py`), working directory pinned to
`llm_fallback/claude_code/` so even a compromised or confused session has
nothing outside it to read. `WebSearch` is the only tool you are ever
given, and only when the per-call prompt says search is available this
call — nothing beyond that tool can ever be invoked.
