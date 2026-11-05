import { Check, Copy } from "lucide-react";
import { useState } from "react";
import ProviderLogo from "../ProviderLogo";
import type { ChatStats } from "../../api/client";

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="chat-meta__copy"
      title={copied ? "Copied" : "Copy"}
      aria-label={copied ? "Copied" : "Copy"}
      onClick={() =>
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        })
      }
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

const num = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());
const secs = (s: number | null | undefined) => (s == null ? "—" : `${s.toFixed(1)}s`);
const usd = (n: number) => `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;
const yes = (b: boolean) => (b ? "yes" : "no");

// Rows for the expanded view. A future signal is one more row here.
function rows(s: ChatStats): [string, string][] {
  const out: [string, string][] = [
    ["Answered by", s.handled_by === "router" ? "Router (no AI call)" : s.provider ? `${s.provider} · ${s.model ?? ""}` : "Nothing answered"],
    ["Total time", secs(s.total_s)],
  ];
  if (s.handled_by === "router") return out;
  out.push(["Model time", secs(s.llm_s)]);
  if (s.tokens) {
    out.push(["Tokens in / out", `${num(s.tokens.input)} / ${num(s.tokens.output)}`]);
    if (s.tokens.cached) out.push(["Cached input", num(s.tokens.cached)]);
  }
  if (s.cost_usd != null) out.push(["Cost", usd(s.cost_usd)]);
  if (s.budget) out.push(["Budget used", `${usd(s.budget.spent_usd)} of ${usd(s.budget.cap_usd)} (${Math.round((s.budget.spent_usd / s.budget.cap_usd) * 100)}%)`]);
  out.push(["Failover", s.failover_from.length ? `after ${s.failover_from.join(" → ")} failed` : "none"]);
  out.push(["Web search", yes(s.web_search)]);
  out.push(["Web research", yes(s.web_research)]);
  if (s.sources) out.push(["Sources", String(s.sources)]);
  if (s.recall_hits) out.push(["Archive days recalled", String(s.recall_hits)]);
  if (s.domain) out.push(["Domain", s.domain]);
  if (s.context_turns != null) out.push(["Conversation sent", `${s.context_turns} turns · ~${num(s.context_tokens)} tokens`]);
  if (s.truncated) out.push(["Cut off", "yes, hit the output limit"]);
  if (s.error) out.push(["Error", s.error]);
  return out;
}

function summary(s: ChatStats): string {
  const parts = [secs(s.total_s)];
  if (s.handled_by === "router") parts.push("router");
  if (s.tokens?.input != null || s.tokens?.output != null)
    parts.push(`${num((s.tokens?.input ?? 0) + (s.tokens?.output ?? 0))} tokens`);
  if (s.cost_usd != null) parts.push(usd(s.cost_usd));
  if (s.failover_from.length) parts.push("failover");
  if (s.web_search || s.web_research) parts.push("web");
  return parts.join(" · ");
}

// Copy + model + a clickable summary that expands into the full analytics.
export function ReplyMeta({ text, model, stats }: { text: string; model?: string | null; stats?: ChatStats | null }) {
  return (
    <div className="chat-meta">
      <div className="chat-meta__line">
        <CopyButton text={text} />
        {model && (
          <span className="chat-meta__model">
            <ProviderLogo name={model} size={12} /> {model}
          </span>
        )}
      </div>
      {stats && (
        <details className="chat-meta__stats">
          <summary>{summary(stats)}</summary>
          <dl>
            {rows(stats).map(([k, v]) => (
              <div key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}
