You are being run as a direct API call (Gemini `generateContent`, see
`llm_fallback/gemini/provider.py`). Whether you're given a `google_search`
tool varies by call — check the per-call prompt, which tells you
explicitly whether search is available for that call.
