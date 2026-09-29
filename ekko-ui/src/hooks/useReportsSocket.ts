import { useEffect, useState } from "react";
import { subscribe } from "../api/socket";

const MAX_REPORTS = 20;

export interface Report {
  id: number;
  kind: string;
  title: string;
  payload: Record<string, unknown>;
  receivedAt: number;
}

let nextId = 0;

export function useReportsSocket(): Report[] {
  const [reports, setReports] = useState<Report[]>([]);

  useEffect(
    () =>
      subscribe((data) => {
        const msg = data as { type?: string; kind: string; title: string; payload?: Record<string, unknown> };
        if (msg?.type !== "report") return;
        const report: Report = {
          id: nextId++,
          kind: msg.kind,
          title: msg.title,
          payload: msg.payload ?? {},
          receivedAt: Date.now(),
        };
        setReports((prev) => [report, ...prev].slice(0, MAX_REPORTS));
      }),
    [],
  );

  return reports;
}
