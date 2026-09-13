import "./CheckLedger.css";

function PassMark() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="ledger-mark pass" aria-hidden="true">
      <path d="M3 8.5 6.5 12 13 4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FailMark() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="ledger-mark fail" aria-hidden="true">
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function formatMs(ms) {
  return `${ms.toFixed(2)} ms`;
}

export default function CheckLedger({ decision, loading }) {
  if (loading) {
    return (
      <div className="ledger-card">
        <div className="ledger-header">Check ledger</div>
        <div className="ledger-rows">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="ledger-skeleton-row" style={{ width: `${85 - i * 4}%` }} />
          ))}
        </div>
      </div>
    );
  }

  if (!decision) {
    return (
      <div className="ledger-card">
        <div className="ledger-header">Check ledger</div>
        <div className="ledger-empty">Waiting for tool calls.</div>
      </div>
    );
  }

  const checks = decision.checks ?? [];
  const failingCount = checks.filter((c) => !c.passed).length;
  const hasFailure = failingCount > 0;

  return (
    <div className="ledger-card" data-verdict={decision.verdict}>
      <div className="ledger-header">Check ledger</div>
      <div className="ledger-rows" key={decision._seq ?? decision.call_id}>
        {checks.map((c, i) => (
          <div
            key={c.n}
            className={`ledger-row${hasFailure && c.passed ? " ledger-row-dim" : ""}`}
            style={{ "--row-i": i }}
            data-failing-check={!c.passed && c.source ? "true" : undefined}
          >
            {c.passed ? <PassMark /> : <FailMark />}
            <span className="ledger-n mono text-xs">{c.n}</span>
            <span className="ledger-name mono text-sm">{c.name}</span>
            <span className={`ledger-detail text-sm${!c.passed ? " ledger-detail-failing" : ""}`}>
              {c.detail}
              {c.source && <span className="ledger-cite mono text-xs">{c.source.cite}</span>}
            </span>
          </div>
        ))}
      </div>
      <div className="ledger-verdict" data-verdict={decision.verdict}>
        <span className="ledger-verdict-word">{decision.verdict}</span>
        <span className="ledger-verdict-meta text-sm mono">
          {failingCount} failing &middot; {formatMs(decision.latency_ms)}
        </span>
      </div>
    </div>
  );
}
