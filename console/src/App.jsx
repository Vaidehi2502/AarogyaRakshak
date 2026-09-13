import { useMemo, useRef, useState } from "react";
import "./App.css";
import { useDecisionFeed } from "./hooks/useDecisionFeed";
import Header from "./components/Header";
import SessionList from "./components/SessionList";
import CheckLedger from "./components/CheckLedger";
import ContextPane from "./components/ContextPane";
import SeverOverlay from "./components/SeverOverlay";

export default function App() {
  const { status, decisions } = useDecisionFeed();
  const [followLive, setFollowLive] = useState(true);
  const [manualIndex, setManualIndex] = useState(null);

  const ledgerRef = useRef(null);
  const contextRef = useRef(null);

  const effectiveIndex = followLive ? decisions.length - 1 : manualIndex;
  const decision = useMemo(
    () => (effectiveIndex != null && effectiveIndex >= 0 ? decisions[effectiveIndex] : null),
    [decisions, effectiveIndex]
  );

  const loading = status === "connecting" && decisions.length === 0;

  const handleSelect = (index) => {
    setFollowLive(false);
    setManualIndex(index);
  };

  const handleToggleFollow = () => {
    if (followLive) {
      setManualIndex(decisions.length - 1);
      setFollowLive(false);
    } else {
      setFollowLive(true);
    }
  };

  return (
    <div className="app-shell">
      <Header decisions={decisions} />
      {status === "error" && (
        <div className="app-error-banner text-xs mono">feed disconnected - retrying every 2s</div>
      )}
      <div className="app-columns">
        <SessionList
          decisions={decisions}
          effectiveIndex={effectiveIndex}
          followLive={followLive}
          onSelect={handleSelect}
          onToggleFollow={handleToggleFollow}
        />
        <div ref={ledgerRef} style={{ minHeight: 0 }}>
          <CheckLedger decision={decision} loading={loading} />
        </div>
        <div ref={contextRef} style={{ minHeight: 0 }}>
          <ContextPane decision={decision} />
        </div>
      </div>
      <SeverOverlay ledgerRef={ledgerRef} contextRef={contextRef} decision={decision} />
    </div>
  );
}
