// Links come from backend report payloads (scraped news); only http(s) may
// be opened, never javascript:, file: or custom schemes.
export function safeUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}
