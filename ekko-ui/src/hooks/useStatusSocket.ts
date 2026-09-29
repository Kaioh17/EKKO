import { useEffect, useState } from "react";
import { subscribe } from "../api/socket";
import type { EkkoStatus } from "../components/TopBar";

export function useStatusSocket(): EkkoStatus {
  const [status, setStatus] = useState<EkkoStatus>("idle");

  useEffect(
    () =>
      subscribe((data) => {
        const msg = data as { type?: string; state?: EkkoStatus };
        if (msg?.type === "status" && msg.state) setStatus(msg.state);
      }),
    [],
  );

  return status;
}
