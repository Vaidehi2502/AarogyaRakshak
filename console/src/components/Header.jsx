import "./Header.css";

const STAT_ORDER = [
  { key: "ALLOW", label: "allowed", cls: "allow" },
  { key: "BLOCK", label: "blocked", cls: "block" },
  { key: "FLAG", label: "flagged", cls: "flag" },
];

export default function Header({ decisions }) {
  const counts = decisions.reduce(
    (acc, d) => ({ ...acc, [d.verdict]: (acc[d.verdict] ?? 0) + 1 }),
    {}
  );

  return (
    <header className="app-header">
      <div className="app-header-brand">
        <span className="app-header-title">AarogyaRakshak</span>
        <span className="app-header-tagline text-xs mono">
          provenance-aware enforcement gateway &middot; live decision console
        </span>
      </div>
      <div className="app-header-stats mono text-xs">
        <span className="app-stat">{decisions.length} calls</span>
        {STAT_ORDER.map(({ key, label, cls }) => (
          <span className={`app-stat ${cls}`} key={key}>
            <span className={`app-stat-dot ${cls}`} />
            {counts[key] ?? 0} {label}
          </span>
        ))}
      </div>
    </header>
  );
}
