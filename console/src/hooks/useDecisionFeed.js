import { useEffect, useRef, useState } from "react";

const FEED_URL = "ws://localhost:7432/decisions";
const MAX_HISTORY = 60;
const RECONNECT_DELAY_MS = 2000;

/**
 * status: "connecting" | "open" | "error"
 * "error" covers both a failed connect and a drop mid-session - the feed
 * keeps retrying underneath, so the UI only needs one disconnected state.
 */
export function useDecisionFeed() {
  const [status, setStatus] = useState("connecting");
  const [decisions, setDecisions] = useState([]);
  const retryTimer = useRef(null);
  const seq = useRef(0);
  // Bumped on every effect run. Each socket's handlers close over the
  // epoch they were opened under, so a socket from a superseded effect
  // run (StrictMode's dev-only double-invoke, or fast remounts) is force-
  // closed the moment it does anything, even if the effect's own
  // cleanup call to socket.close() didn't win the race against a
  // near-instant localhost handshake.
  const epoch = useRef(0);

  useEffect(() => {
    const myEpoch = ++epoch.current;
    let socket;

    const isCurrent = () => epoch.current === myEpoch;

    // "connecting" only applies to this effect's very first attempt (the
    // Loading state, before anything has ever arrived). A retry after a
    // drop stays "error" the whole time it's retrying - flipping back to
    // "connecting" on every 2s retry attempt would flash the error banner
    // off and on for as long as the feed stays down.
    setStatus("connecting");

    const connect = () => {
      if (!isCurrent()) return;
      socket = new WebSocket(FEED_URL);

      socket.onopen = () => {
        if (!isCurrent()) {
          socket.close();
          return;
        }
        setStatus("open");
      };

      socket.onmessage = (event) => {
        if (!isCurrent()) return;
        try {
          const decision = JSON.parse(event.data);
          // The feed's call_id isn't guaranteed unique across a long
          // session (the --fake fixture cycles the same 3 ids forever) -
          // a client-side sequence number is the one thing guaranteed
          // unique per received message, so it's what list rendering
          // keys on instead.
          seq.current += 1;
          const withSeq = { ...decision, _seq: seq.current };
          setDecisions((prev) => [...prev.slice(-(MAX_HISTORY - 1)), withSeq]);
        } catch {
          // malformed frame - drop it, the feed sends one JSON object per message
        }
      };

      socket.onerror = () => {
        if (isCurrent()) setStatus("error");
      };

      socket.onclose = () => {
        if (!isCurrent()) return;
        setStatus("error");
        retryTimer.current = setTimeout(connect, RECONNECT_DELAY_MS);
      };
    };

    connect();

    return () => {
      epoch.current += 1; // supersede this run - its socket becomes stale
      clearTimeout(retryTimer.current);
      socket?.close();
    };
  }, []);

  return { status, decisions };
}
