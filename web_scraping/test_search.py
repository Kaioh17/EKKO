"""Manual smoke test + latency check for Brave / Tavily search.

    python -m web_scraping.test_search "who won the match last night"
    python -m web_scraping.test_search "bitcoin price" --fresh pd --runs 3
    python -m web_scraping.test_search "bitcoin price" --provider tavily
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from routing import host  # noqa: E402,F401 -- import triggers its load_dotenv()
from web_scraping import PROVIDER_MODULES, usage  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("query", nargs="?", default="latest news today")
p.add_argument("--provider", choices=list(PROVIDER_MODULES), default="brave")
p.add_argument("--count", type=int, default=5)
p.add_argument("--fresh", choices=["pd", "pw", "pm", "py"])
p.add_argument("--runs", type=int, default=1, help="repeat to see latency spread")
a = p.parse_args()
mod = PROVIDER_MODULES[a.provider]

times = []
for _ in range(a.runs):
    report = mod.search(a.query, a.count, a.fresh)
    times.append(report.elapsed_ms)
    assert report.hits, "no results returned"

for i, h in enumerate(report.hits, 1):
    snippet = re.sub(r"<[^>]+>", "", h.snippet)  # Brave wraps hits in <strong>
    print(f"{i}. {h.title} [{h.age}]\n   {h.url}\n   {snippet[:160]}")
print(f"\n{a.provider} latency ms: {times}  (min {min(times)}, max {max(times)})")
print("usage:", usage.local(a.provider), "| live:", mod.live_usage())
