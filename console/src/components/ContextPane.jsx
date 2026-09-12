import "./ContextPane.css";

const SCALAR_ORDER = ["name", "phone", "email", "mrn"];
const DISPLAY_NAME = { phone: "registered_phone" };

function tagFor(label) {
  if (label === "free_text") return "untrusted";
  if (label === "trusted") return "trusted";
  return "structured";
}

function tagText(tag) {
  return tag.toUpperCase();
}

export default function ContextPane({ decision }) {
  if (!decision) {
    return (
      <div className="context-card">
        <div className="context-header">Context provenance</div>
      </div>
    );
  }

  const context = decision.context ?? {};
  const provenanceSource = decision.checks?.find((c) => !c.passed && c.source)?.source;

  const notesEntry = context.notes;
  const notesText = Array.isArray(notesEntry?.value)
    ? notesEntry.value.join(" ")
    : notesEntry?.value ?? "";

  const hasHighlight = provenanceSource && provenanceSource.field === "notes" && notesText;
  const before = hasHighlight ? notesText.slice(0, provenanceSource.start) : notesText;
  const hit = hasHighlight ? notesText.slice(provenanceSource.start, provenanceSource.end) : "";
  const after = hasHighlight ? notesText.slice(provenanceSource.end) : "";

  return (
    <div className="context-card">
      <div className="context-header">Context provenance</div>

      <div className="context-field">
        <span className="context-field-key mono text-xs">patient_id</span>
        <span className="context-field-right">
          <span className="context-tag structured mono text-xs">{tagText("structured")}</span>
          <span className="context-field-value text-sm">{decision.patient_id}</span>
        </span>
      </div>

      {SCALAR_ORDER.filter((key) => context[key]).map((key) => {
        const entry = context[key];
        const tag = tagFor(entry.label);
        return (
          <div className="context-field" key={key}>
            <span className="context-field-key mono text-xs">{DISPLAY_NAME[key] ?? key}</span>
            <span className="context-field-right">
              <span className={`context-tag ${tag} mono text-xs`}>{tagText(tag)}</span>
              <span className="context-field-value text-sm">{entry.value}</span>
            </span>
          </div>
        );
      })}

      <div className="context-notes-label text-xs mono">
        notes {notesEntry && <span className="context-tag untrusted">UNTRUSTED</span>}
      </div>
      <div className="context-notes-panel text-sm">
        {notesText ? (
          <>
            {before}
            {hasHighlight && (
              <span className="context-span-hit" data-injected-span="true">
                {hit}
              </span>
            )}
            {after}
          </>
        ) : (
          <span className="context-notes-empty">No clinical notes read in this session yet.</span>
        )}
      </div>
    </div>
  );
}
