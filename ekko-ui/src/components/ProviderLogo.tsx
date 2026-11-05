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

function ProviderLogo({ name, size = 16 }: { name: string; size?: number }) {
  const lower = name.toLowerCase();
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
