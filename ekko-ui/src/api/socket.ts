import { backend } from "./backend";

type Listener = (message: unknown) => void;

const RECONNECT_DELAY_MS = 2000;
const listeners = new Set<Listener>();
let started = false;

// One app-lifetime socket to /ws/status, shared by every subscriber. The
// token rides in the subprotocol list (browsers can't set WS headers), so
// it never appears in a URL or a log line.
async function connect(): Promise<void> {
  let ws: WebSocket;
  try {
    const { url, token } = await backend();
    ws = new WebSocket(`${url.replace(/^http/, "ws")}/ws/status`, ["ekko", token]);
  } catch {
    setTimeout(connect, RECONNECT_DELAY_MS);
    return;
  }
  ws.onmessage = (event) => {
    let data: unknown;
    try {
      data = JSON.parse(event.data);
    } catch {
      return; // ignore malformed frames
    }
    listeners.forEach((fn) => fn(data));
  };
  ws.onclose = () => setTimeout(connect, RECONNECT_DELAY_MS);
  ws.onerror = () => ws.close();
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  if (!started) {
    started = true;
    void connect();
  }
  return () => listeners.delete(fn);
}
