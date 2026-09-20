import { useEffect, useState } from "react";
import { API_TOKEN } from "../api/client";
import type { EkkoStatus } from "../components/TopBar";

const WS_URL = `ws://localhost:8000/ws/status?token=${encodeURIComponent(API_TOKEN)}`;
const RECONNECT_DELAY_MS = 2000;

export function useStatusSocket(): EkkoStatus {
  const [status, setStatus] = useState<EkkoStatus>("idle");

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    function connect() {
      socket = new WebSocket(WS_URL);
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data?.type === "status") setStatus(data.state);
        } catch {
          // ignore malformed frames
        }
      };
      socket.onclose = () => {
        if (!cancelled) reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
      };
      socket.onerror = () => socket?.close();
    }

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  return status;
}
