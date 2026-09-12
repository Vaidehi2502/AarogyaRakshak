import { useEffect, useRef } from "react";
import "./SessionList.css";

const DOT_CLASS = { ALLOW: "allow", BLOCK: "block", FLAG: "flag" };

export default function SessionList({ decisions, effectiveIndex, followLive, onSelect, onToggleFollow }) {
  const rowsRef = useRef(null);

  useEffect(() => {
    if (followLive && rowsRef.current) {
      rowsRef.current.scrollTop = rowsRef.current.scrollHeight;
    }
  }, [decisions.length, followLive]);

  return (
    <div className="session-card">
      <div className="session-header-row">
        <span className="session-header">Session</span>
        <button className="session-live-btn text-xs" onClick={onToggleFollow}>
          <span className={`session-live-dot ${followLive ? "" : "paused"}`} />
          {followLive ? "live" : "paused"}
        </button>
      </div>

      <div className="session-rows" ref={rowsRef}>
        {decisions.length === 0 && <div className="session-empty text-sm">No calls yet.</div>}
        {decisions.map((d, i) => (
          <button
            key={d._seq ?? i}
            className={`session-row${i === effectiveIndex ? " selected" : ""}`}
            onClick={() => onSelect(i)}
          >
            <span className="session-row-top">
              <span className={`session-dot ${DOT_CLASS[d.verdict] ?? "flag"}`} />
              <span className="session-tool mono text-xs">{d.tool}</span>
            </span>
            <span className="session-class text-xs">{d.class}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
