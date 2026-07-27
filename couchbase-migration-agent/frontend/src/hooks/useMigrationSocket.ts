import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "@/api/client";

/** Subscribes to live progress for a single migration (or "*" for all migrations)
 * over the FastAPI websocket, auto-reconnecting with backoff. */
export function useMigrationSocket<T = any>(migrationId: string | "*") {
  const [data, setData] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1000);

  useEffect(() => {
    let ws: WebSocket;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout>;

    const connect = () => {
      const path = migrationId === "*" ? "/ws/migrations" : `/ws/migrations/${migrationId}`;
      ws = new WebSocket(`${WS_BASE}${path}`);
      ws.onopen = () => {
        setConnected(true);
        retryDelay.current = 1000;
      };
      ws.onmessage = (evt) => {
        try {
          setData(JSON.parse(evt.data));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) {
          retryTimer = setTimeout(connect, retryDelay.current);
          retryDelay.current = Math.min(retryDelay.current * 1.5, 15000);
        }
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, [migrationId]);

  return { data, connected };
}
