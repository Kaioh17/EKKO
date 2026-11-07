"""Prompt in, MCP tool calls out: spawns the Spotify MCP server over stdio and
lets Gemini drive its tools. A test harness for "does MCP suit ekko", not the
voice path.

    python -m backend.mcp.agent "play lonely at the top"
"""

import asyncio
import os
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.config import get
from paths import ROOT

SYSTEM = "You control the user's Spotify through tools. Act immediately, then reply in one short sentence."
MAX_STEPS = 6
_DROP = {"title", "default", "additionalProperties"}  # JSON-schema keys Gemini rejects


def _clean(schema):
    if isinstance(schema, dict):
        return {k: _clean(v) for k, v in schema.items() if k not in _DROP}
    return [_clean(v) for v in schema] if isinstance(schema, list) else schema


def _declaration(t) -> dict:
    d = {"name": t.name, "description": t.description or ""}
    if t.input_schema.get("properties"):  # Gemini rejects an empty parameters object
        d["parameters"] = _clean(t.input_schema)
    return d


async def run(prompt: str) -> dict:
    model, key = get("llm").gemini_model, os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    params = StdioServerParameters(command=sys.executable, args=["-m", "backend.mcp.spotify_server"], cwd=str(ROOT), env=dict(os.environ))
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        decls = [_declaration(t) for t in (await session.list_tools()).tools]
        contents, calls = [{"role": "user", "parts": [{"text": prompt}]}], []
        async with httpx.AsyncClient(timeout=30) as http:
            for _ in range(MAX_STEPS):
                resp = await http.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": key},
                    json={"system_instruction": {"parts": [{"text": SYSTEM}]}, "tools": [{"functionDeclarations": decls}], "contents": contents},
                )
                resp.raise_for_status()
                content = resp.json()["candidates"][0]["content"]
                contents.append(content)  # sent back untouched: Gemini 3 needs its thought signatures
                parts = content.get("parts", [])
                uses = [p["functionCall"] for p in parts if "functionCall" in p]
                if not uses:
                    return {"reply": "".join(p.get("text", "") for p in parts), "calls": calls}
                results = []
                for u in uses:
                    out = await session.call_tool(u["name"], u.get("args", {}))
                    text = "".join(c.text for c in out.content if c.type == "text")
                    calls.append({"tool": u["name"], "args": u.get("args", {}), "result": text, "error": out.is_error})
                    results.append({"functionResponse": {"name": u["name"], "response": {"error" if out.is_error else "result": text}}})
                contents.append({"role": "user", "parts": results})
        return {"reply": "Gave up after too many steps.", "calls": calls}


if __name__ == "__main__":
    print(asyncio.run(run(" ".join(sys.argv[1:]) or "what's playing?")))
