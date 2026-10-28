# EKKO agent instructions

- Never run `git commit`, `git push`, or `git add` (or any other git write action: staging, tagging, branch creation) in this repo. Always give the exact commands and let the user run them.
- Never include yourself (or any agent) as a contributor, co-author, or attribution line in commits, PRs, or any other artifact.

## Coding style (CI runs `uv run --no-sync ruff check .`)

- Run `uv run --no-sync ruff check .` before finishing any Python change; fix everything it reports.
- One statement per line. Never chain with `;` (E702).
- Closures defined in a loop must not capture loop variables (B023). Bind them as default args (`def f(x=x)`) or move the function out of the loop.
- Don't `del` a name that is used later or inside a closure (F821). Rebinding or scope exit frees it anyway.
