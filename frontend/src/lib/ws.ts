"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { WsEvent } from "@/types/api";

type WsStatus = "connecting" | "open" | "closed" | "error";

interface UseWsOptions {
  /** Path relative to the WS base URL, e.g. "/ws/markets" */
  path: string;
  /** Called for each inbound message */
  onMessage: (event: WsEvent) => void;
  /** Reconnect delay in ms (default 2000) */
  reconnectDelay?: number;
  enabled?: boolean;
}

/**
 * Maintains a WebSocket connection with automatic reconnect.
 * Reads NEXT_PUBLIC_WS_URL for the base; falls back to the current host.
 */
export function useWs({ path, onMessage, reconnectDelay = 2000, enabled = true }: UseWsOptions): {
  status: WsStatus;
  disconnect: () => void;
} {
  const [status, setStatus] = useState<WsStatus>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  const connect = useCallback(() => {
    const apiKey = process.env.NEXT_PUBLIC_API_KEY ?? "";
    const base =
      process.env.NEXT_PUBLIC_WS_URL ??
      (window.location.protocol === "https:" ? "wss:" : "ws:") +
        "//" +
        window.location.host;
    const qs = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : "";
    const url = `${base}${path}${qs}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus("connecting");

    ws.onopen = () => setStatus("open");

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data as string) as WsEvent;
        onMessageRef.current(data);
      } catch {
        // ignore malformed frames
      }
    };

    ws.onerror = () => setStatus("error");

    ws.onclose = () => {
      setStatus("closed");
      if (enabled) {
        timerRef.current = setTimeout(connect, reconnectDelay);
      }
    };
  }, [path, reconnectDelay, enabled]);

  useEffect(() => {
    if (!enabled) return;
    connect();
    return () => {
      if (timerRef.current !== null) clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect, enabled]);

  const disconnect = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    wsRef.current?.close();
  }, []);

  return { status, disconnect };
}
