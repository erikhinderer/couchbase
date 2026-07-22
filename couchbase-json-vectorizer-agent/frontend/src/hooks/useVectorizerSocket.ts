import { useEffect, useRef, useState } from "react";
import { WS_BASE } from "@/api/client";

/** Subscribes to live job updates over the FastAPI websocket, auto-reconnecting
 * with backoff. Pass a job id, or "*" to receive every job (dashboard + the
 * live operations feed). */
export function useVectorizerSocket<T = any>(jobId: string | "*") {
  const [data, setData] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1000);

  useEffect(() => {
    let ws: WebSocket;
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout>;

    const connect = () => {
      const path = jobId === "*" ? "/ws/jobs" : `/ws/jobs/${jobId}`;
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
  }, [jobId]);

  return { data, connected };
}
