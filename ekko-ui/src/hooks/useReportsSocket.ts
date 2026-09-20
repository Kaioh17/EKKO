import { useEffect, useState } from "react";
import { API_TOKEN } from "../api/client";

const WS_URL = `ws://localhost:8000/ws/status?token=${encodeURIComponent(API_TOKEN)}`;
const RECONNECT_DELAY_MS = 2000;
const MAX_REPORTS = 20;

export interface Report {
  id: number;
  kind: string;
  title: string;
  payload: Record<string, unknown>;
  receivedAt: number;
}

export function useReportsSocket(): Report[] {
  const [reports, setReports] = useState<Report[]>([]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let nextId = 0;

    function connect() {
      socket = new WebSocket(WS_URL);
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data?.type !== "report") return;
          const report: Report = {
            id: nextId++,
            kind: data.kind,
            title: data.title,
            payload: data.payload ?? {},
            receivedAt: Date.now(),
          };
          setReports((prev) => [report, ...prev].slice(0, MAX_REPORTS));
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

  return reports;
}
