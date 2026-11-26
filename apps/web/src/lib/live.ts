"use client";

import { useEffect, useRef, useState } from "react";
import type { LivePositionMessage } from "./types";

export type LiveStatus = "connecting" | "live" | "offline";

export function liveSocketUrl(): string {
  const configured = process.env.NEXT_PUBLIC_LIVE_WS_URL;
  if (configured) return configured;
  // The API port is exposed directly for WebSockets (Next.js rewrites don't proxy upgrades).
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.hostname}:8000/api/v1/ws/positions`;
}

/** Reconnect delays: 1s, 2s, 4s ... capped at 30s. */
export function reconnectDelay(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** attempt);
}

/**
 * Subscribe to live position updates. While disconnected the caller should keep polling
 * REST (status "offline"); the map never pretends to be live when it is not.
 */
export function useLivePositions(onMessage: (msg: LivePositionMessage) => void): LiveStatus {
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const handler = useRef(onMessage);
  handler.current = onMessage;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    const connect = () => {
      setStatus("connecting");
      try {
        socket = new WebSocket(liveSocketUrl());
      } catch {
        scheduleRetry();
        return;
      }
      socket.onopen = () => {
        attempt = 0;
        setStatus("live");
      };
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(String(event.data));
          if (data?.type === "positions") handler.current(data as LivePositionMessage);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        if (!closed) scheduleRetry();
      };
    };
    const scheduleRetry = () => {
      setStatus("offline");
      timer = setTimeout(connect, reconnectDelay(attempt++));
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, []);

  return status;
}
