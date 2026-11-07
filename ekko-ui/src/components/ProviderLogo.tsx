import { Sparkles } from "lucide-react";
import openai from "../assets/openai.svg";
import { siClaude, siDeepseek, siGooglegemini, siOllama } from "simple-icons";

// Matched on substring so provider ids ("claude_api") and model names ("gemini-2.5-flash") both resolve.
// simple-icons dropped OpenAI, so its logo is a local asset; anything unknown falls back to a generic mark.
const LOGOS = [
  ["gemini", siGooglegemini],
  ["deepseek", siDeepseek],
  ["claude", siClaude],
  ["ollama", siOllama],
] as const;

// simple-icons has no Groq either: official mark path in Groq's brand orange.
const GROQ_PATH =
  "M12.036 2c-3.853-.035-7 3-7.036 6.781-.035 3.782 3.055 6.872 6.908 6.907h2.42v-2.566h-2.292c-2.407.028-4.38-1.866-4.408-4.23-.029-2.362 1.901-4.298 4.308-4.326h.1c2.407 0 4.358 1.915 4.365 4.278v6.305c0 2.342-1.944 4.25-4.323 4.279a4.375 4.375 0 01-3.033-1.252l-1.851 1.818A7 7 0 0012.029 22h.092c3.803-.056 6.858-3.083 6.879-6.816v-6.5C18.907 4.963 15.817 2 12.036 2z";

function ProviderLogo({ name, size = 16 }: { name: string; size?: number }) {
  const lower = name.toLowerCase();
  if (lower.includes("groq"))
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="#F55036" role="img" aria-label="Groq" style={{ flex: "none" }}>
        <path d={GROQ_PATH} />
      </svg>
    );
  if (lower.includes("openai") || lower.includes("gpt")) return <img src={openai} width={size} height={size} alt="OpenAI" style={{ flex: "none" }} />;
  const hit = LOGOS.find(([key]) => lower.includes(key))?.[1];
  if (!hit) return <Sparkles size={size} aria-hidden style={{ flex: "none" }} />;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={`#${hit.hex}`} role="img" aria-label={hit.title} style={{ flex: "none" }}>
      <path d={hit.path} />
    </svg>
  );
}

export default ProviderLogo;
